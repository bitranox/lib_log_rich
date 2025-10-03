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


def _coerce_json_value(value: Any) -> Any:
    """Return a JSON-serialisable representation of ``value``."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, Mapping):
        mapping = cast(Mapping[Any, Any], value)
        return {str(key): _coerce_json_value(item) for key, item in mapping.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = cast(Iterable[Any], value)
        return [_coerce_json_value(item) for item in items]
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
        service_value = context.get("service")
        if service_value is not None:
            payload["_service"] = service_value
        user_value = context.get("user_name")
        if user_value is not None:
            payload["_user"] = user_value
        hostname_value = context.get("hostname")
        if hostname_value is not None:
            payload["_hostname"] = hostname_value
        process_id = context.get("process_id")
        if process_id is not None:
            payload["_pid"] = process_id
        chain_value = context.get("process_id_chain")
        chain_parts: list[str] = []
        if isinstance(chain_value, (list, tuple)):
            chain_iter = cast(Iterable[object], chain_value)
            chain_parts = [str(part) for part in chain_iter]
        elif chain_value:
            chain_parts = [str(chain_value)]
        if chain_parts:
            payload["_process_id_chain"] = ">".join(chain_parts)
        if event.extra:
            for key, value in event.extra.items():
                payload[f"_{key}"] = _coerce_json_value(value)
        return {key: value for key, value in payload.items() if value is not None}


__all__ = ["GraylogAdapter"]
