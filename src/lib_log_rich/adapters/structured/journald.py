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

from typing import Any, Callable, Iterable, Mapping, cast

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


def _default_sender(**fields: Any) -> None:  # pragma: no cover - depends on systemd
    """Proxy to :func:`systemd.journal.send`, raising if unavailable."""
    try:
        from systemd import journal  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - executed only when systemd missing
        raise RuntimeError("systemd.journal is not available") from exc
    journal_mod = cast(Any, journal)
    send_attr = getattr(journal_mod, "send", None)
    if not callable(send_attr):  # pragma: no cover - defensive
        raise RuntimeError("systemd.journal.send is not callable")
    send_fn = cast(Sender, send_attr)
    send_fn(**fields)


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
            if value is None or value == {}:
                continue
            upper = key.upper()
            if upper == "SERVICE":
                fields[self._service_field] = value
            elif upper == "ENVIRONMENT":
                fields["ENVIRONMENT"] = value
            elif upper == "EXTRA":
                extras = cast(Mapping[str, Any], value)
                for extra_key, extra_value in extras.items():
                    extra_upper = extra_key.upper()
                    target = extra_upper if extra_upper not in _RESERVED_FIELDS else f"EXTRA_{extra_upper}"
                    if target in fields:
                        target = f"EXTRA_{target}"
                    fields[target] = extra_value
            elif upper == "PROCESS_ID_CHAIN":
                chain_parts: list[str] = []
                if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
                    chain_parts = [str(part) for part in cast(Iterable[Any], value)]
                elif value:
                    chain_parts = [str(value)]
                if chain_parts:
                    fields["PROCESS_ID_CHAIN"] = ">".join(chain_parts)
            else:
                fields[upper] = value

        for key, value in event.extra.items():
            upper = key.upper()
            target = upper if upper not in _RESERVED_FIELDS else f"EXTRA_{upper}"
            if target in fields:
                target = f"EXTRA_{target}"
            fields[target] = value

        return fields


__all__ = ["JournaldAdapter"]
