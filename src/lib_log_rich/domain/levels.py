"""Log level abstraction providing richer metadata than the stdlib enum.

Purpose
-------
Offer a domain-specific representation of log severities that augments the
stdlib levels with icons and helper conversions.

Contents
--------
* :class:`LogLevel` enum with conversion helpers and presentation metadata.
* ``_ICON_TABLE`` constant mapping levels to console glyphs.

System Role
-----------
Used by the application layer to enforce consistent severity handling and by the
adapters to present human-friendly icons (see ``konzept_architecture.md``).
"""

from __future__ import annotations

import logging
from enum import Enum


class LogLevel(Enum):
    """Enumerated logging levels used throughout the system."""

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @property
    def severity(self) -> str:
        """Return the lowercase severity name for structured logging payloads."""

        return self.name.lower()

    @property
    def icon(self) -> str:
        """Return the unicode icon visualizing the level on colored consoles."""

        return _ICON_TABLE[self]

    def to_python_level(self) -> int:
        """Return the :mod:`logging` constant matching this level."""

        return getattr(logging, self.name)

    @classmethod
    def from_name(cls, name: str) -> "LogLevel":
        normalized = name.strip().upper()
        try:
            return cls[normalized]
        except KeyError as exc:  # pragma: no cover - defensive branch
            raise ValueError(f"Unknown log level: {name!r}") from exc

    @classmethod
    def from_python_level(cls, level: int) -> "LogLevel":
        """Translate a stdlib logging level integer into :class:`LogLevel`."""
        return cls.from_numeric(level)

    @classmethod
    def from_numeric(cls, level: int) -> "LogLevel":
        """Return the :class:`LogLevel` corresponding to ``level``."""
        try:
            return cls(level)
        except ValueError as exc:  # pragma: no cover - defensive branch
            raise ValueError(f"Unsupported log level numeric: {level}") from exc


_ICON_TABLE = {
    LogLevel.DEBUG: "🐞",
    LogLevel.INFO: "ℹ",
    LogLevel.WARNING: "⚠",
    LogLevel.ERROR: "✖",
    LogLevel.CRITICAL: "☠",
}
# Console glyphs displayed by the Rich adapter per log level.


__all__ = ["LogLevel"]
