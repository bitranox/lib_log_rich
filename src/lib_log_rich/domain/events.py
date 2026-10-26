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
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, TypeVar

from . import json_codec

if TYPE_CHECKING:
    from .context import LogContext
    from .levels import LogLevel

_T = TypeVar("_T")


class _Keep(Enum):
    """Default of :meth:`LogEvent.replace` meaning "leave this field as it is".

    A dedicated marker rather than ``None``, because ``None`` is a value
    ``exc_info`` and ``stack_info`` can be replaced with.
    """

    KEEP = "keep"


_KEEP: Final = _Keep.KEEP


def _kept(override: _T | _Keep, current: _T) -> _T:
    """Return ``override`` unless it is the keep marker, in which case keep ``current``."""
    return current if isinstance(override, _Keep) else override


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
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
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
            >>> from lib_log_rich.domain.context import LogContext
            >>> from lib_log_rich.domain.levels import LogLevel
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
            >>> from lib_log_rich.domain.context import LogContext
            >>> from lib_log_rich.domain.levels import LogLevel
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
        return json_codec.dumps(self.to_dict(), sort_keys=True)

    def replace(
        self,
        *,
        event_id: str | _Keep = _KEEP,
        timestamp: datetime | _Keep = _KEEP,
        logger_name: str | _Keep = _KEEP,
        level: LogLevel | _Keep = _KEEP,
        message: str | _Keep = _KEEP,
        context: LogContext | _Keep = _KEEP,
        extra: dict[str, Any] | _Keep = _KEEP,
        exc_info: str | _Keep | None = _KEEP,
        stack_info: str | _Keep | None = _KEEP,
    ) -> LogEvent:
        """Return a copied event with the given fields replaced; an omitted field is kept.

        ``None`` is applied as a value, so ``exc_info`` and ``stack_info`` can
        be cleared. Every value passes the same validation as the constructor.

        Args:
            event_id: Replacement event identifier.
            timestamp: Replacement timezone-aware timestamp.
            logger_name: Replacement logger name.
            level: Replacement severity.
            message: Replacement message.
            context: Replacement bound context.
            extra: Replacement extra metadata (copied).
            exc_info: Replacement exception text, or ``None``.
            stack_info: Replacement stack text, or ``None``.

        Returns:
            New LogEvent instance with the specified fields replaced.

        Example:
            >>> from datetime import timezone
            >>> from lib_log_rich.domain.context import LogContext
            >>> from lib_log_rich.domain.levels import LogLevel
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent(
            ...     event_id='abc',
            ...     timestamp=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
            ...     logger_name='svc.worker',
            ...     level=LogLevel.INFO,
            ...     message='ok',
            ...     context=ctx,
            ...     exc_info='Traceback ...',
            ... )
            >>> changed = event.replace(message='changed', exc_info=None)
            >>> (changed.message, changed.exc_info, changed.event_id)
            ('changed', None, 'abc')

        """
        return replace(
            self,
            event_id=_kept(event_id, self.event_id),
            timestamp=_kept(timestamp, self.timestamp),
            logger_name=_kept(logger_name, self.logger_name),
            level=_kept(level, self.level),
            message=_kept(message, self.message),
            context=_kept(context, self.context),
            extra=_kept(extra, self.extra),
            exc_info=_kept(exc_info, self.exc_info),
            stack_info=_kept(stack_info, self.stack_info),
        )


__all__ = ["LogEvent"]
