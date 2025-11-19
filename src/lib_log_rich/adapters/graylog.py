"""Configurable GELF adapter for Graylog integrations.

Purpose
-------
Forward structured log events to Graylog over TCP/TLS or UDP, aligning with the
remote sink requirements documented in ``concept_architecture.md``.

Contents
--------
* :data:`_LEVEL_MAP` - Graylog severity scaling.
* :class:`GraylogAdapter` - concrete :class:`GraylogPort` implementation.

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

import json
import socket
import ssl
from datetime import date, datetime
from typing import Any, Iterable, Mapping, cast

from lib_log_rich.application.ports.graylog import GraylogPort
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel

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

    # Fallback: string representation
    return str(value)


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
        protocol: str = "tcp",
        use_tls: bool = False,
    ) -> None:
        """Configure the adapter with Graylog connection details."""
        self._host = host
        self._port = port
        self._enabled = enabled
        self._timeout = timeout
        normalised = protocol.lower()
        if normalised not in {"tcp", "udp"}:
            raise ValueError("protocol must be 'tcp' or 'udp'")
        if normalised == "udp" and use_tls:
            raise ValueError("TLS is only supported for TCP Graylog transport")
        self._protocol = normalised
        self._use_tls = use_tls
        self._ssl_context = ssl.create_default_context() if use_tls else None
        self._socket: socket.socket | ssl.SSLSocket | None = None

    def emit(self, event: LogEvent) -> None:
        """Serialize ``event`` to GELF and send if the adapter is enabled.

        Examples
        --------
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

        payload = self._build_payload(event)
        data = json.dumps(payload).encode("utf-8") + b"\x00"

        if self._protocol == "udp":
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
    def _add_optional_context_fields(payload: dict[str, Any], context: dict[str, Any]) -> None:
        """Add optional context fields to payload if present."""
        if (service_value := context.get("service")) is not None:
            payload["_service"] = service_value
        if (user_value := context.get("user_name")) is not None:
            payload["_user"] = user_value
        if (hostname_value := context.get("hostname")) is not None:
            payload["_hostname"] = hostname_value
        if (process_id := context.get("process_id")) is not None:
            payload["_pid"] = process_id

    @staticmethod
    def _format_process_chain_gelf(chain_value: Any) -> str | None:
        """Format process ID chain for GELF payload."""
        chain_parts: list[str] = []
        if isinstance(chain_value, (list, tuple)):
            chain_iter = cast(Iterable[object], chain_value)
            chain_parts = [str(part) for part in chain_iter]
        elif chain_value:
            chain_parts = [str(chain_value)]
        return ">".join(chain_parts) if chain_parts else None

    @staticmethod
    def _add_extra_fields(payload: dict[str, Any], extra: dict[str, Any] | None) -> None:
        """Add extra fields to payload with underscore prefix."""
        if extra:
            for key, value in extra.items():
                payload[f"_{key}"] = _coerce_json_value(value)

    def _build_payload(self, event: LogEvent) -> dict[str, Any]:
        """Construct the GELF payload for ``event``.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job', request_id='req')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.WARNING, 'msg', ctx)
        >>> adapter = GraylogAdapter(host='localhost', port=12201, enabled=False)
        >>> payload = adapter._build_payload(event)
        >>> payload['level']
        4
        >>> payload['_request_id']
        'req'
        """
        context = event.context.to_dict(include_none=True)
        hostname = str(context.get("hostname") or context.get("service") or "unknown")
        payload: dict[str, Any] = {
            "version": "1.1",
            "short_message": event.message,
            "host": hostname,
            "timestamp": event.timestamp.timestamp(),
            "level": _LEVEL_MAP[event.level],
            "logger": event.logger_name,
            "_job_id": context.get("job_id"),
            "_environment": context.get("environment"),
            "_request_id": context.get("request_id"),
        }
        self._add_optional_context_fields(payload, context)
        if chain_str := self._format_process_chain_gelf(context.get("process_id_chain")):
            payload["_process_id_chain"] = chain_str
        self._add_extra_fields(payload, event.extra)
        return {key: value for key, value in payload.items() if value is not None}


__all__ = ["GraylogAdapter"]
