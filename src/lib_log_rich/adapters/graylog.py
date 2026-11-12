"""Configurable GELF adapter for Graylog integrations.

Purpose
-------
Forward structured log events to Graylog over TCP/TLS or UDP, aligning with the
remote sink requirements documented in ``concept_architecture.md``.

Contents
--------
* :data:`_LEVEL_MAP` - Graylog severity scaling.
* :class:`GraylogAdapter` - concrete :class:`GraylogPort` implementation.
* :class:`GELFPayload` - Pydantic model of the GELF payload; its JSON dump is the wire form.

System Role
-----------
Provides the external system integration for GELF, translating domain events
into payloads consumed by Graylog.

Alignment Notes
---------------
Payload structure and connection handling match the Graylog expectations listed
in ``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

import socket
import ssl
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from lib_log_rich.adapters._formatting import traceback_text
from lib_log_rich.adapters._json_coerce import coerce_json_value
from lib_log_rich.adapters._text_utils import strip_emoji
from lib_log_rich.application.ports.graylog import GraylogPort
from lib_log_rich.domain.enums import GraylogProtocol
from lib_log_rich.domain.levels import LogLevel

if TYPE_CHECKING:
    from lib_log_rich.domain.events import LogEvent

# GELF 1.1 protocol constants (https://docs.graylog.org/docs/gelf)
GELF_MESSAGE_TERMINATOR: bytes = b"\x00"
"""Null byte terminator required by GELF 1.1 for TCP/UDP message framing."""

# After a failed send the adapter skips Graylog for a while instead of paying a
# connect timeout on every event: a dead endpoint otherwise stalls the queue
# worker for seconds per event, and shutdown then gives up on the backlog.
_INITIAL_BACKOFF_SECONDS: float = 5.0
_MAX_BACKOFF_SECONDS: float = 300.0
_SKIPPED_EVENTS_LOGGER = "lib_log_rich.graylog"


class GraylogUnavailableError(OSError):
    """Graylog could not be reached; the adapter skips it until the backoff ends.

    An ``OSError`` so callers that already treat transport failures as
    ``OSError`` keep doing so.
    """


# Syslog severity levels per RFC 5424 Section 6.2.1
# https://datatracker.ietf.org/doc/html/rfc5424#section-6.2.1
SYSLOG_EMERG: int = 0  # System is unusable
SYSLOG_ALERT: int = 1  # Action must be taken immediately
SYSLOG_CRIT: int = 2  # Critical conditions
SYSLOG_ERR: int = 3  # Error conditions
SYSLOG_WARNING: int = 4  # Warning conditions
SYSLOG_NOTICE: int = 5  # Normal but significant condition
SYSLOG_INFO: int = 6  # Informational messages
SYSLOG_DEBUG: int = 7  # Debug-level messages

_LEVEL_MAP: Mapping[LogLevel, int] = {
    LogLevel.DEBUG: SYSLOG_DEBUG,
    LogLevel.INFO: SYSLOG_INFO,
    LogLevel.WARNING: SYSLOG_WARNING,
    LogLevel.ERROR: SYSLOG_ERR,
    LogLevel.CRITICAL: SYSLOG_CRIT,
}

#: Map :class:`LogLevel` to GELF severities (syslog priority values).


def _empty_extra() -> dict[str, Any]:
    """Return a fresh, typed mapping for a payload without caller extras."""
    return {}


class GELFPayload(BaseModel):
    """Structured GELF 1.1 payload for Graylog integration.

    The context fields serialize under their ``_``-prefixed GELF names and are
    left out when ``None``. ``extra`` holds the caller's free-form fields; the
    model serializer flattens them into the top level as ``_<key>`` after the
    context fields, so a caller key such as ``job_id`` overrides the context
    value, exactly as the GELF consumers have always received it.

    Examples:
        >>> payload = GELFPayload(
        ...     version="1.1", short_message="m", host="h", timestamp=1.5, level=6, logger="l",
        ...     job_id="job", extra={"code": b"E1"},
        ... )
        >>> payload.model_dump_json()
        '{"version":"1.1","short_message":"m","host":"h","timestamp":1.5,"level":6,"logger":"l","_job_id":"job","_code":"E1"}'

    """

    # serialize_by_alias makes the GELF names the default output, so no caller
    # can emit the Python attribute names by forgetting a by_alias flag.
    model_config = ConfigDict(frozen=True, extra="forbid", serialize_by_alias=True)

    version: str
    short_message: str
    # GELF's slot for a long text such as a backtrace; dropped when unset.
    full_message: str | None = None
    host: str
    timestamp: float
    level: int
    logger: str
    job_id: str | None = Field(default=None, serialization_alias="_job_id")
    environment: str | None = Field(default=None, serialization_alias="_environment")
    request_id: str | None = Field(default=None, serialization_alias="_request_id")
    service: str | None = Field(default=None, serialization_alias="_service")
    user: str | None = Field(default=None, serialization_alias="_user")
    hostname: str | None = Field(default=None, serialization_alias="_hostname")
    pid: int | None = Field(default=None, serialization_alias="_pid")
    process_id_chain: str | None = Field(default=None, serialization_alias="_process_id_chain")
    # Arbitrary caller values (sets, paths, bytes, datetimes): Pydantic must not
    # validate them, only carry them to the serializer that coerces them.
    extra: Mapping[str, Any] = Field(default_factory=_empty_extra, exclude=True)

    @model_serializer(mode="wrap")
    def _flatten_extra(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Drop unset context fields and append ``extra`` as ``_``-prefixed fields.

        ``None`` is dropped only for the declared fields: a caller's ``extra``
        value of ``None`` is data and still reaches Graylog as ``null``.
        """
        fields = cast("dict[str, Any]", handler(self))
        payload = {key: value for key, value in fields.items() if value is not None}
        for key, value in self.extra.items():
            payload[f"_{key}"] = coerce_json_value(value)
        return payload


class GraylogAdapter(GraylogPort):
    """Send GELF-formatted events over TCP (optionally TLS) or UDP.

    Why
    ---
    Provides an optional integration that can be toggled via configuration while
    honouring Graylog's expectation for persistent TCP connections and newline
    terminated UDP frames.

    A failed send raises :class:`GraylogUnavailableError` once and then skips
    Graylog for a backoff window (5 s, doubling up to 300 s, reset by the next
    successful send). Events arriving inside the window are counted, not sent;
    the first successful send afterwards reports that count to Graylog as a
    warning, so the gap is visible where the events are missing.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        enabled: bool = True,
        timeout: float = 1.0,
        protocol: GraylogProtocol = GraylogProtocol.TCP,
        use_tls: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure the adapter with Graylog connection details.

        ``clock`` is the monotonic time source for the backoff window.
        """
        self._host = host
        self._port = port
        self._enabled = enabled
        self._timeout = timeout
        if protocol is GraylogProtocol.UDP and use_tls:
            raise ValueError("TLS is only supported for TCP Graylog transport")
        self._protocol = protocol
        self._use_tls = use_tls
        self._ssl_context = ssl.create_default_context() if use_tls else None
        self._socket: socket.socket | ssl.SSLSocket | None = None
        self._clock = clock
        # None means "not backing off"; a float sentinel would be ambiguous
        # with a clock that legitimately reads that value.
        self._skip_until: float | None = None
        self._backoff_seconds = 0.0
        self._skipped_events = 0

    def emit(self, event: LogEvent) -> None:
        """Serialize ``event`` to GELF and send if the adapter is enabled.

        Raises:
            GraylogUnavailableError: The send failed; Graylog is skipped until
                the backoff window ends.

        Note:
            UDP ``sendto()`` and TCP ``sendall()`` are blocking operations.
            In async contexts with high log volume, consider using queue-based
            logging adapters to prevent event loop blocking.

        Example:
            >>> from datetime import datetime, timezone
            >>> from lib_log_rich.domain.context import LogContext
            >>> from lib_log_rich.domain.events import LogEvent
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
            >>> adapter = GraylogAdapter(host='localhost', port=12201, enabled=False)
            >>> adapter.emit(event)  # does not raise when disabled
            >>> adapter._socket is None
            True

        """
        if not self._enabled:
            return
        if self._skip_until is not None and self._clock() < self._skip_until:
            self._skipped_events += 1
            return

        frame = self._frame(self._build_payload(event))
        try:
            if self._skipped_events:
                self._send(self._frame(self._skipped_events_notice(event)))
                # Reported: a failure of the event itself must not count them twice.
                self._skipped_events = 0
            self._send(frame)
        except OSError as exc:
            # The event that failed counts as skipped too: it never arrived.
            self._skipped_events += 1
            raise self._start_backoff(exc) from exc
        self._skip_until = None
        self._backoff_seconds = 0.0

    @staticmethod
    def _frame(payload: GELFPayload) -> bytes:
        """Serialize ``payload`` into one terminated GELF frame."""
        return payload.model_dump_json().encode("utf-8") + GELF_MESSAGE_TERMINATOR

    def _send(self, data: bytes) -> None:
        """Send one frame over the configured transport."""
        if self._protocol is GraylogProtocol.UDP:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self._timeout)
                sock.sendto(data, (self._host, self._port))
            return
        # A failed connect is not retried: a second attempt against an endpoint
        # that just timed out only doubles the wait. A failed SEND is retried
        # once on a fresh connection, because a cached connection the server
        # closed is the common case there.
        for attempt in range(2):
            sock = self._get_tcp_socket()
            try:
                sock.sendall(data)
            except OSError:
                self._close_socket()
                if attempt == 1:
                    raise
            else:
                return

    def _start_backoff(self, exc: OSError) -> GraylogUnavailableError:
        """Open (or lengthen) the skip window and describe it for the caller."""
        self._close_socket()
        if self._backoff_seconds:
            self._backoff_seconds = min(self._backoff_seconds * 2, _MAX_BACKOFF_SECONDS)
        else:
            self._backoff_seconds = _INITIAL_BACKOFF_SECONDS
        self._skip_until = self._clock() + self._backoff_seconds
        return GraylogUnavailableError(f"Graylog {self._host}:{self._port} unreachable ({exc}); not sending to Graylog for {self._backoff_seconds:g} s")

    def _skipped_events_notice(self, event: LogEvent) -> GELFPayload:
        """Build the warning that tells Graylog how many events never arrived."""
        context = event.context
        return GELFPayload(
            version="1.1",
            short_message=(f"{self._skipped_events} log events were not sent to Graylog while {self._host}:{self._port} was unreachable"),
            host=str(context.hostname or context.service or "unknown"),
            timestamp=event.timestamp.timestamp(),
            level=SYSLOG_WARNING,
            logger=_SKIPPED_EVENTS_LOGGER,
            service=context.service,
            environment=context.environment,
            hostname=context.hostname,
            pid=context.process_id,
            extra={"skipped_events": self._skipped_events},
        )

    async def flush(self) -> None:
        """Close any persistent TCP connection so the adapter can shut down cleanly."""
        self._close_socket()

    def _get_tcp_socket(self) -> socket.socket | ssl.SSLSocket:
        """Return a connected TCP socket, creating one if necessary."""
        if self._socket is not None:
            return self._socket
        return self._connect_tcp()

    def _connect_tcp(self) -> socket.socket | ssl.SSLSocket:
        """Establish a TCP (optionally TLS-wrapped) connection to Graylog."""
        connection = socket.create_connection((self._host, self._port), timeout=self._timeout)
        connection.settimeout(self._timeout)
        sock: socket.socket | ssl.SSLSocket = connection
        if self._use_tls:
            context = self._ssl_context or ssl.create_default_context()
            self._ssl_context = context
            try:
                sock = context.wrap_socket(connection, server_hostname=self._host)
            except OSError:
                # Nothing else holds the plain socket once the handshake fails.
                connection.close()
                raise
            sock.settimeout(self._timeout)
        self._socket = sock
        return sock

    def _close_socket(self) -> None:
        """Close and clear any cached TCP socket."""
        if self._socket is None:
            return
        try:
            self._socket.close()
        finally:
            self._socket = None

    @staticmethod
    def _format_process_chain_gelf(chain: tuple[int, ...]) -> str | None:
        """Format process ID chain tuple for GELF payload."""
        if chain:
            return ">".join(str(part) for part in chain)
        return None

    def _build_payload(self, event: LogEvent) -> GELFPayload:
        """Construct the GELF payload for ``event``.

        Uses direct attribute access on LogContext dataclass.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.events import LogEvent
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job', request_id='req')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.WARNING, 'msg', ctx)
        >>> adapter = GraylogAdapter(host='localhost', port=12201, enabled=False)
        >>> payload = adapter._build_payload(event)
        >>> payload.level
        4
        >>> payload.request_id
        'req'

        """
        context = event.context
        hostname = str(context.hostname or context.service or "unknown")
        chain_str = self._format_process_chain_gelf(context.process_id_chain)

        return GELFPayload(
            version="1.1",
            short_message=strip_emoji(event.message),
            full_message=traceback_text(event),
            host=hostname,
            timestamp=event.timestamp.timestamp(),
            level=_LEVEL_MAP[event.level],
            logger=event.logger_name,
            job_id=context.job_id,
            environment=context.environment,
            request_id=context.request_id,
            service=context.service,
            user=context.user_name,
            hostname=context.hostname,
            pid=context.process_id,
            process_id_chain=chain_str,
            extra=event.extra,
        )


__all__ = ["GELFPayload", "GraylogAdapter", "GraylogUnavailableError"]
