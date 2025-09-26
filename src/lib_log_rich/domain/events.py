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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from .context import LogContext
from .levels import LogLevel


def _ensure_aware(ts: datetime) -> datetime:
    """Validate that ``ts`` is timezone-aware and normalise to UTC."""
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(timezone.utc)


@dataclass(slots=True, frozen=True)
class LogEvent:
    """Immutable log event transported through the logging pipeline.

    Attributes
    ----------
    event_id:
        Stable identifier used for deduplication and diagnostics.
    timestamp:
        Time of the event in timezone-aware UTC.
    logger_name:
        Logical logger emitting the event.
    level:
        :class:`LogLevel` severity associated with the event.
    message:
        Rendered message passed by the caller.
    context:
        :class:`LogContext` bound to the execution scope at emission time.
    extra:
        Shallow copy of caller-supplied key/value pairs.
    exc_info:
        Optional exception string captured when logging failures.
    """

    event_id: str
    timestamp: datetime
    logger_name: str
    level: LogLevel
    message: str
    context: LogContext
    extra: dict[str, Any] = field(default_factory=dict)
    exc_info: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _ensure_aware(self.timestamp))
        if not self.message.strip():
            raise ValueError("message must not be empty")
        if not self.event_id:
            raise ValueError("event_id must not be empty")
        object.__setattr__(self, "extra", dict(self.extra))

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event to a dictionary with ISO8601 timestamps."""

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
        return data

    def to_json(self) -> str:
        """Serialize the event to JSON with sorted keys for deterministic output."""

        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LogEvent":
        """Reconstruct an event from :meth:`to_dict` output."""

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
        )

    def replace(self, **changes: Any) -> "LogEvent":
        """Return a copied event with ``changes`` applied."""

        return replace(self, **changes)


__all__ = ["LogEvent"]
