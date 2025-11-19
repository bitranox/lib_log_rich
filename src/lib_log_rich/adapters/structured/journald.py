"""Journald adapter that emits uppercase structured fields.

Purpose
-------
Send structured events to systemd-journald, aligning with the Linux deployment
story in ``concept_architecture.md``.

Contents
--------
* :data:`_LEVEL_MAP` - syslog priority mapping.
* :class:`JournaldAdapter` - concrete :class:`StructuredBackendPort` implementation.

System Role
-----------
Transforms :class:`LogEvent` objects into journald field dictionaries and invokes
``systemd.journal.send`` (or a supplied sender).

Alignment Notes
---------------
Field naming conventions match the journald expectations documented in
``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

import socket
import sys
import types
import warnings
from typing import Any, Callable, Final, Iterable, Mapping, cast

from lib_log_rich.application.ports.structures import StructuredBackendPort
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel

Sender = Callable[..., None]

_UNIX_SOCKET_FAMILY: Final[int | None] = cast(int | None, getattr(socket, "AF_UNIX", None))

_systemd_send: Sender | None = None
_JOURNAL_SOCKETS: tuple[str, ...] = ("/run/systemd/journal/socket", "/dev/log")

_LEVEL_MAP = {
    LogLevel.DEBUG: 7,
    LogLevel.INFO: 6,
    LogLevel.WARNING: 4,
    LogLevel.ERROR: 3,
    LogLevel.CRITICAL: 2,
}


#: Map :class:`LogLevel` to syslog numeric priorities.


_RESERVED_FIELDS: set[str] = {
    "MESSAGE",
    "PRIORITY",
    "LOGGER_NAME",
    "LOGGER_LEVEL",
    "EVENT_ID",
    "TIMESTAMP",
    "SERVICE",
    "ENVIRONMENT",
    "PROCESS_ID",
    "PROCESS_ID_CHAIN",
}


def _ensure_systemd_journal_module() -> Sender:
    """Ensure ``systemd.journal`` is importable, installing a socket-based fallback when bindings are absent."""

    module_name = "systemd.journal"
    existing = sys.modules.get(module_name)
    if existing and callable(getattr(existing, "send", None)):
        return cast(Sender, existing.send)

    # If a top-level ``systemd`` module exists but is not a package, replace it with one that exposes ``journal``.
    package = sys.modules.get("systemd")
    if isinstance(package, types.ModuleType):
        journal_attr = getattr(package, "journal", None)
        if journal_attr and callable(getattr(journal_attr, "send", None)):
            sys.modules[module_name] = journal_attr
            return cast(Sender, journal_attr.send)
    if not isinstance(package, types.ModuleType) or not hasattr(package, "__path__"):
        package = types.ModuleType("systemd")
        package.__path__ = []  # type: ignore[attr-defined]
        sys.modules["systemd"] = package

    journal_module = types.ModuleType(module_name)

    def _send_via_socket(**fields: Any) -> None:
        family = _UNIX_SOCKET_FAMILY
        if family is None:
            warnings.warn(
                "lib_log_rich: journald fallback requires UNIX domain sockets; install python-systemd on Linux. Calls on non-UNIX platforms will be ignored.",
                RuntimeWarning,
                stacklevel=2,
            )
            raise RuntimeError(
                "UNIX domain sockets unavailable; install python-systemd for native support.",
            )

        message = _encode_journal_fields(fields)
        last_error: OSError | None = None
        for socket_path in _JOURNAL_SOCKETS:
            try:
                with socket.socket(family, socket.SOCK_DGRAM) as sock:
                    sock.connect(socket_path)
                    sock.sendall(message)
                return
            except OSError as exc:
                last_error = exc
                continue
        raise RuntimeError("Unable to write to journald socket. Install the python-systemd bindings for native support.") from last_error

    journal_module.send = _send_via_socket  # type: ignore[attr-defined]
    setattr(package, "journal", journal_module)
    sys.modules[module_name] = journal_module
    return cast(Sender, journal_module.send)


def _encode_journal_fields(fields: Mapping[str, Any]) -> bytes:
    """Encode journald fields using the native datagram format."""

    encoded_lines: list[bytes] = []
    for key, value in fields.items():
        key_bytes = key.encode("utf-8", errors="strict")
        if isinstance(value, bytes):
            value_bytes = value
        else:
            value_bytes = str(value).encode("utf-8", errors="strict")
        encoded_lines.append(key_bytes + b"=" + value_bytes)
    return b"\n".join(encoded_lines) + b"\n"


def _resolve_systemd_sender() -> Sender:
    """Resolve and cache the systemd journald sender."""
    global _systemd_send
    if _systemd_send is not None:
        return _systemd_send
    try:
        from systemd import journal  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - executed only when systemd missing
        _systemd_send = _ensure_systemd_journal_module()
        return _systemd_send
    if not callable(getattr(cast(Any, journal), "send", None)):
        _systemd_send = _ensure_systemd_journal_module()
        return _systemd_send
    journal_mod = cast(Any, journal)
    send_attr = getattr(journal_mod, "send", None)
    if not callable(send_attr):  # pragma: no cover - defensive
        raise RuntimeError("systemd.journal.send is not callable")
    sys.modules.setdefault("systemd.journal", journal_mod)
    _systemd_send = cast(Sender, send_attr)
    return _systemd_send


try:  # pragma: no cover - best-effort import normalization at module import time
    _ensure_systemd_journal_module()
except Exception as exc:  # noqa: BLE001
    warnings.warn(
        f"lib_log_rich: unable to pre-load systemd.journal module ({exc}) — fallback will be attempted lazily.",
        RuntimeWarning,
        stacklevel=1,
    )


class JournaldAdapter(StructuredBackendPort):
    """Emit log events via ``systemd.journal.send``."""

    def __init__(self, *, sender: Sender | None = None, service_field: str = "SERVICE") -> None:
        """Initialise the adapter with an optional sender and service field."""
        self._sender = sender or _resolve_systemd_sender()
        self._service_field = service_field.upper()

    def emit(self, event: LogEvent) -> None:
        """Send ``event`` to journald using the configured sender."""
        fields = self._build_fields(event)
        self._sender(**fields)

    def _handle_service_field(self, fields: dict[str, Any], value: Any) -> None:
        """Handle SERVICE field mapping."""
        fields[self._service_field] = value

    def _handle_environment_field(self, fields: dict[str, Any], value: Any) -> None:
        """Handle ENVIRONMENT field mapping."""
        fields["ENVIRONMENT"] = value

    def _handle_extra_fields(self, fields: dict[str, Any], extras: Mapping[str, Any]) -> None:
        """Handle EXTRA fields with conflict resolution."""
        for extra_key, extra_value in extras.items():
            target = self._resolve_field_name(extra_key.upper(), fields)
            fields[target] = extra_value

    def _handle_process_chain(self, fields: dict[str, Any], value: Any) -> None:
        """Handle PROCESS_ID_CHAIN field formatting."""
        chain_parts = self._normalize_chain_value(value)
        if chain_parts:
            fields["PROCESS_ID_CHAIN"] = ">".join(chain_parts)

    def _resolve_field_name(self, key_upper: str, existing_fields: dict[str, Any]) -> str:
        """Resolve field name avoiding conflicts with reserved fields."""
        target = key_upper if key_upper not in _RESERVED_FIELDS else f"EXTRA_{key_upper}"
        if target in existing_fields:
            target = f"EXTRA_{target}"
        return target

    def _normalize_chain_value(self, value: Any) -> list[str]:
        """Normalize process chain value to list of strings."""
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            return [str(part) for part in cast(Iterable[Any], value)]
        elif value:
            return [str(value)]
        return []

    def _build_fields(self, event: LogEvent) -> dict[str, Any]:
        """Construct a journald field dictionary for ``event``.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job', extra={'foo': 'bar'})
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
        >>> adapter = JournaldAdapter(sender=lambda **fields: None)
        >>> fields = adapter._build_fields(event)
        >>> fields['MESSAGE'], fields['SERVICE']
        ('msg', 'svc')
        >>> fields['FOO']
        'bar'
        """
        context = event.context.to_dict(include_none=True)

        # Base fields
        fields: dict[str, Any] = {
            "MESSAGE": event.message,
            "PRIORITY": _LEVEL_MAP[event.level],
            "LOGGER_NAME": event.logger_name,
            "LOGGER_LEVEL": event.level.severity.upper(),
            "EVENT_ID": event.event_id,
            "TIMESTAMP": event.timestamp.isoformat(),
        }

        # Field handler dispatch table
        field_handlers = {
            "SERVICE": self._handle_service_field,
            "ENVIRONMENT": self._handle_environment_field,
            "EXTRA": self._handle_extra_fields,
            "PROCESS_ID_CHAIN": self._handle_process_chain,
        }

        # Process context fields using dispatch table
        for key, value in context.items():
            if value is None or value == {}:
                continue

            upper = key.upper()
            handler = field_handlers.get(upper)

            if handler:
                handler(fields, value)
            else:
                fields[upper] = value

        # Process extra fields with conflict resolution
        for key, value in event.extra.items():
            target = self._resolve_field_name(key.upper(), fields)
            fields[target] = value

        return fields


__all__ = ["JournaldAdapter"]
