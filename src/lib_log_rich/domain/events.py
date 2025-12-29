"""Domain event describing a structured log message.

Purpose
-------
Provide an immutable, serialisable representation of log events travelling
through the application pipeline.

Contents
--------
* :class:`LogEvent` dataclass with helper methods.
* Utility function ``_ensure_aware`` for timestamp validation.

System Role
-----------
Sits in the domain layer, ensuring all adapters/application services manipulate
pure data objects and keeping serialisation logic centralised.

Alignment Notes
---------------
The field semantics and serialization formats mirror the expectations laid out
in ``docs/systemdesign/module_reference.md`` so dumps, Graylog feeds, and CLI
renderers stay consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

import orjson

from .context import LogContext
from .levels import LogLevel


def _new_extra_mapping() -> dict[str, Any]:
    """Return a mutable mapping for event extras."""
    return {}


def _ensure_aware(ts: datetime) -> datetime:
    """Validate that ``ts`` is timezone-aware and normalise to UTC.

    Downstream sinks expect canonical UTC timestamps. Accidentally passing naive
    datetimes would silently assume local time, breaking cross-region analysis.

    Args:
        ts: Timestamp provided by the caller.

    Returns:
        The same instant converted to UTC.

    Raises:
        ValueError: If ``ts`` lacks timezone information.

    Example:
        >>> from datetime import timezone
        >>> aware = datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc)
        >>> _ensure_aware(aware).tzinfo == timezone.utc
        True
        >>> _ensure_aware(datetime(2025, 9, 30, 12, 0))
        Traceback (most recent call last):
        ...
        ValueError: timestamp must be timezone-aware

    """
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(timezone.utc)


@dataclass(slots=True, frozen=True)
class LogEvent:
    """Immutable log event transported through the logging pipeline.

    Encapsulates the information mandated by the architecture plan so every
    adapter can operate on consistent data without touching Python `logging`
    internals.

    Attributes:
        event_id: Stable identifier used for deduplication and diagnostics.
        timestamp: Time of the event in timezone-aware UTC.
        logger_name: Logical logger emitting the event.
        level: :class:`LogLevel` severity associated with the event.
        message: Rendered message passed by the caller.
        context: :class:`LogContext` bound to the execution scope at emission time.
        extra: Shallow copy of caller-supplied key/value pairs.
        exc_info: Optional exception string captured when logging failures.
        stack_info: Optional stack trace string recorded when callers request
            ``stack_info``.

    Example:
        >>> from datetime import timezone
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job-42')
        >>> event = LogEvent(
        ...     event_id='abc',
        ...     timestamp=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
        ...     logger_name='svc.worker',
        ...     level=LogLevel.INFO,
        ...     message='started',
        ...     context=ctx,
        ... )
        >>> event.level is LogLevel.INFO
        True

    """

    event_id: str
    timestamp: datetime
    logger_name: str
    level: LogLevel
    message: str
    context: LogContext
    extra: dict[str, Any] = field(default_factory=_new_extra_mapping)
    exc_info: str | None = None
    stack_info: str | None = None

    def __post_init__(self) -> None:
        """Normalise timestamp and protect against accidental mutation.

        Coerces ``timestamp`` to UTC and replaces ``extra`` with a shallow copy
        so caller dictionaries cannot be mutated later.
        """
        object.__setattr__(self, "timestamp", _ensure_aware(self.timestamp))
        if not self.message.strip():
            raise ValueError("message must not be empty")
        if not self.event_id:
            raise ValueError("event_id must not be empty")
        object.__setattr__(self, "extra", dict(self.extra))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event to a dictionary with ISO8601 timestamps.

        Returns:
            JSON-ready payload matching the expectation of dump/queue adapters.

        Example:
            >>> from datetime import timezone
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent(
            ...     event_id='abc',
            ...     timestamp=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
            ...     logger_name='svc.worker',
            ...     level=LogLevel.WARNING,
            ...     message='attention',
            ...     context=ctx,
            ...     extra={'k': 'v'},
            ... )
            >>> event.to_dict()['level']
            'warning'

        """
        data = {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "logger_name": self.logger_name,
            "level": self.level.severity,
            "message": self.message,
            "context": self.context.to_dict(),
            "extra": dict(self.extra),
        }
        if self.exc_info is not None:
            data["exc_info"] = self.exc_info
        if self.stack_info is not None:
            data["stack_info"] = self.stack_info
        return data

    def to_json(self) -> str:
        """Serialize the event to JSON with sorted keys for deterministic output.

        Returns:
            JSON string representation of the event.

        Example:
            >>> from datetime import timezone
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent(
            ...     event_id='abc',
            ...     timestamp=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
            ...     logger_name='svc.worker',
            ...     level=LogLevel.ERROR,
            ...     message='boom',
            ...     context=ctx,
            ... )
            >>> '"event_id":"abc"' in event.to_json()
            True

        """
        return orjson.dumps(self.to_dict(), option=orjson.OPT_SORT_KEYS).decode()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LogEvent:
        """Reconstruct an event from :meth:`to_dict` output.

        Args:
            payload: Dictionary either produced by :meth:`to_dict` or a
                compatible API.

        Returns:
            New event instance matching the serialized data.

        Example:
            >>> from datetime import timezone
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent(
            ...     event_id='abc',
            ...     timestamp=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
            ...     logger_name='svc.worker',
            ...     level=LogLevel.INFO,
            ...     message='ok',
            ...     context=ctx,
            ... )
            >>> round_trip = LogEvent.from_dict(event.to_dict())
            >>> round_trip.logger_name
            'svc.worker'

        """
        context = LogContext(**payload["context"])
        return cls(
            event_id=payload["event_id"],
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            logger_name=payload["logger_name"],
            level=LogLevel.from_name(payload["level"]),
            message=payload["message"],
            context=context,
            extra=payload.get("extra", {}),
            exc_info=payload.get("exc_info"),
            stack_info=payload.get("stack_info"),
        )

    def replace(self, **changes: Any) -> LogEvent:
        """Return a copied event with ``changes`` applied.

        Args:
            **changes: Field values to replace in the new event.

        Returns:
            New LogEvent instance with the specified fields replaced.

        Example:
            >>> from datetime import timezone
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent(
            ...     event_id='abc',
            ...     timestamp=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
            ...     logger_name='svc.worker',
            ...     level=LogLevel.INFO,
            ...     message='ok',
            ...     context=ctx,
            ... )
            >>> event.replace(message='changed').message
            'changed'

        """
        return replace(self, **changes)


__all__ = ["LogEvent"]
