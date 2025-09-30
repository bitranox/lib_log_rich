"""Journald adapter that emits uppercase structured fields.

Purpose
-------
Send structured events to systemd-journald, aligning with the Linux deployment
story in ``konzept_architecture.md``.

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

from typing import Any, Callable

from lib_log_rich.application.ports.structures import StructuredBackendPort
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel

Sender = Callable[..., None]

_LEVEL_MAP = {
    LogLevel.DEBUG: 7,
    LogLevel.INFO: 6,
    LogLevel.WARNING: 4,
    LogLevel.ERROR: 3,
    LogLevel.CRITICAL: 2,
}


#: Map :class:`LogLevel` to syslog numeric priorities.


def _default_sender(**fields: Any) -> None:  # pragma: no cover - depends on systemd
    """Proxy to :func:`systemd.journal.send`, raising if unavailable."""
    try:
        from systemd import journal
    except ImportError as exc:  # pragma: no cover - executed only when systemd missing
        raise RuntimeError("systemd.journal is not available") from exc
    journal.send(**fields)


class JournaldAdapter(StructuredBackendPort):
    """Emit log events via ``systemd.journal.send``."""

    def __init__(self, *, sender: Sender | None = None, service_field: str = "SERVICE") -> None:
        """Initialise the adapter with an optional sender and service field."""
        self._sender = sender or _default_sender
        self._service_field = service_field.upper()

    def emit(self, event: LogEvent) -> None:
        """Send ``event`` to journald using the configured sender."""
        fields = self._build_fields(event)
        self._sender(**fields)

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
        fields: dict[str, Any] = {
            "MESSAGE": event.message,
            "PRIORITY": _LEVEL_MAP[event.level],
            "LOGGER_NAME": event.logger_name,
            "LOGGER_LEVEL": event.level.severity.upper(),
            "EVENT_ID": event.event_id,
            "TIMESTAMP": event.timestamp.isoformat(),
        }

        for key, value in context.items():
            if value in (None, {}):
                continue
            upper = key.upper()
            if upper == "SERVICE":
                fields[self._service_field] = value
            elif upper == "ENVIRONMENT":
                fields["ENVIRONMENT"] = value
            elif upper == "EXTRA":
                for extra_key, extra_value in value.items():
                    fields[extra_key.upper()] = extra_value
            elif upper == "PROCESS_ID_CHAIN":
                chain_str = ">".join(str(part) for part in value) if value else ""
                if chain_str:
                    fields["PROCESS_ID_CHAIN"] = chain_str
            else:
                fields[upper] = value

        for key, value in event.extra.items():
            fields[key.upper()] = value

        return fields


__all__ = ["JournaldAdapter"]
