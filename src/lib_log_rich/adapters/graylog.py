"""Configurable GELF adapter for Graylog integrations.

Purpose
-------
Forward structured log events to Graylog over TCP/TLS or UDP, aligning with the
remote sink requirements documented in ``concept_architecture.md``.

Contents
--------
* :data:`_LEVEL_MAP` - Graylog severity scaling.
* :class:`GraylogAdapter` - concrete :class:`GraylogPort` implementation.
* :class:`GELFPayload` - structured GELF payload dataclass.

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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import orjson

from lib_log_rich.adapters._text_utils import strip_emoji
from lib_log_rich.application.ports.graylog import GraylogPort
from lib_log_rich.domain.enums import GraylogProtocol
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.paths import path_to_posix

_LEVEL_MAP: Mapping[LogLevel, int] = {
    LogLevel.DEBUG: 7,
    LogLevel.INFO: 6,
    LogLevel.WARNING: 4,
    LogLevel.ERROR: 3,
    LogLevel.CRITICAL: 2,
}

#: Map :class:`LogLevel` to GELF severities.


def _coerce_datetime(value: datetime | date) -> str:
    """Coerce datetime/date to ISO format string."""
    return value.isoformat()


def _coerce_bytes(value: bytes) -> str:
    """Coerce bytes to UTF-8 string or hex representation."""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.hex()


def _coerce_mapping(mapping: Mapping[Any, Any]) -> dict[str, Any]:
    """Recursively coerce mapping to JSON-compatible dict."""
    return {str(key): _coerce_json_value(item) for key, item in mapping.items()}


def _coerce_iterable(items: Iterable[Any]) -> list[Any]:
    """Recursively coerce iterable to JSON-compatible list."""
    return [_coerce_json_value(item) for item in items]


def _coerce_json_value(value: Any) -> Any:
    """Return a JSON-serialisable representation of ``value``."""
    # Primitives pass through
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    # Date/time types
    if isinstance(value, (datetime, date)):
        return _coerce_datetime(value)

    # Bytes
    if isinstance(value, bytes):
        return _coerce_bytes(value)

    # Mappings
    if isinstance(value, Mapping):
        return _coerce_mapping(cast(Mapping[Any, Any], value))

    # Iterables (excluding str/bytes)
    if isinstance(value, (list, tuple, set, frozenset)):
        return _coerce_iterable(cast(Iterable[Any], value))

    # Path objects - serialize as POSIX for cross-platform compatibility
    if isinstance(value, Path):
        return path_to_posix(value)

    # Fallback: string representation
    return str(value)


def _empty_extra() -> dict[str, Any]:
    """Factory for empty extra fields dict."""
    return {}


# Optional GELF fields mapping (attr_name, payload_key) for iteration-based serialization
_OPTIONAL_GELF_FIELDS: tuple[tuple[str, str], ...] = (
    ("job_id", "_job_id"),
    ("environment", "_environment"),
    ("request_id", "_request_id"),
    ("service", "_service"),
    ("user", "_user"),
    ("hostname", "_hostname"),
    ("pid", "_pid"),
    ("process_id_chain", "_process_id_chain"),
)


@dataclass(slots=True, frozen=True)
class GELFPayload:
    """Structured GELF payload for Graylog integration.

    Encapsulates all fields required by GELF 1.1 specification with
    proper typing and optional fields handling.
    """

    version: str
    short_message: str
    host: str
    timestamp: float
    level: int
    logger: str
    job_id: str | None = None
    environment: str | None = None
    request_id: str | None = None
    service: str | None = None
    user: str | None = None
    hostname: str | None = None
    pid: int | None = None
    process_id_chain: str | None = None
    extra: dict[str, Any] = field(default_factory=_empty_extra)

    def to_dict(self) -> dict[str, Any]:
        """Convert to GELF-compatible dictionary.

        Filters out None values and prefixes custom fields with underscore.
        """
        payload: dict[str, Any] = {
            "version": self.version,
            "short_message": self.short_message,
            "host": self.host,
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger,
        }

        # Add optional fields via iteration
        for attr_name, payload_key in _OPTIONAL_GELF_FIELDS:
            value = getattr(self, attr_name)
            if value is not None:
                payload[payload_key] = value

        # Add extra fields with underscore prefix
        for key, value in self.extra.items():
            payload[f"_{key}"] = _coerce_json_value(value)

        return payload


class GraylogAdapter(GraylogPort):
    """Send GELF-formatted events over TCP (optionally TLS) or UDP.

    Why
    ---
    Provides an optional integration that can be toggled via configuration while
    honouring Graylog's expectation for persistent TCP connections and newline
    terminated UDP frames.
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
    ) -> None:
        """Configure the adapter with Graylog connection details."""
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

    def emit(self, event: LogEvent) -> None:
        """Serialize ``event`` to GELF and send if the adapter is enabled.

        Example:
            >>> from datetime import datetime, timezone
            >>> from lib_log_rich.domain.context import LogContext
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
            >>> adapter = GraylogAdapter(host='localhost', port=12201, enabled=False)
            >>> adapter.emit(event)  # does not raise when disabled
            >>> adapter._socket is None
            True

        """
        if not self._enabled:
            return

        payload = self._build_payload(event).to_dict()
        data = orjson.dumps(payload) + b"\x00"

        if self._protocol is GraylogProtocol.UDP:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(self._timeout)
                sock.sendto(data, (self._host, self._port))
            return

        for attempt in range(2):
            sock = self._get_tcp_socket()
            try:
                sock.sendall(data)
                break
            except (OSError, ssl.SSLError):
                self._close_socket()
                if attempt == 0:
                    continue
                raise

    async def flush(self) -> None:
        """Close any persistent TCP connection so the adapter can shut down cleanly."""
        self._close_socket()
        return None

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
            sock = context.wrap_socket(connection, server_hostname=self._host)
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
            extra=dict(event.extra) if event.extra else {},
        )


__all__ = ["GraylogAdapter"]
