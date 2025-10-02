"""Log level abstraction providing richer metadata than the stdlib enum.

Purpose
-------
Offer a domain-specific representation of log severities that augments the
stdlib levels with icons and helper conversions.

Contents
--------
* :class:`LogLevel` enum with conversion helpers and presentation metadata.
* ``_ICON_TABLE`` constant mapping levels to console glyphs.
* ``_CODE_TABLE`` constant providing four-character formatter abbreviations.

System Role
-----------
Used by the application layer to enforce consistent severity handling and by the
adapters to present human-friendly icons (see ``concept_architecture.md``).

Alignment Notes
---------------
Iconography and severity naming correspond to the palettes explained in
``docs/systemdesign/module_reference.md`` so CLI demos and dumps stay coherent.
"""

from __future__ import annotations

import logging
from enum import Enum


class LogLevel(Enum):
    """Enumerated logging levels used throughout the system.

    Why
    ---
    Wrapping stdlib levels keeps the public API stable while letting adapters
    add metadata (icons, human-readable severity strings) without duplicating
    logic.

    Examples
    --------
    >>> LogLevel.INFO.to_python_level() == logging.INFO
    True
    >>> LogLevel.ERROR.icon
    '✖'
    """

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50

    @property
    def severity(self) -> str:
        """Return the lowercase severity name for structured logging payloads.

        Examples
        --------
        >>> LogLevel.WARNING.severity
        'warning'
        """

        return self.name.lower()

    @property
    def icon(self) -> str:
        """Return the unicode icon visualizing the level on coloured consoles.

        Examples
        --------
        >>> LogLevel.CRITICAL.icon
        '☠'
        """

        return _ICON_TABLE[self]

    @property
    def code(self) -> str:
        """Return a four-character abbreviation for formatter strings.

        Why
        ---
        Many console layouts reserve a narrow column for the log level. A
        deterministic four-character string avoids padding logic in adapters.

        Examples
        --------
        >>> LogLevel.WARNING.code
        'WARN'
        >>> LogLevel.DEBUG.code
        'DEBG'
        """

        return _CODE_TABLE[self]

    def to_python_level(self) -> int:
        """Return the :mod:`logging` constant matching this level.

        Examples
        --------
        >>> LogLevel.DEBUG.to_python_level() == logging.DEBUG
        True
        """

        return getattr(logging, self.name)

    @classmethod
    def from_name(cls, name: str) -> "LogLevel":
        """Parse a case-insensitive level name into :class:`LogLevel`.

        Parameters
        ----------
        name:
            Human-entered text such as ``"info"`` or ``"warning"``.

        Returns
        -------
        LogLevel
            Matching enum member.

        Raises
        ------
        ValueError
            If the name cannot be resolved.

        Examples
        --------
        >>> LogLevel.from_name('Info') is LogLevel.INFO
        True
        >>> LogLevel.from_name('fatal')
        Traceback (most recent call last):
        ...
        ValueError: Unknown log level: 'fatal'
        """

        normalized = name.strip().upper()
        try:
            return cls[normalized]
        except KeyError as exc:  # pragma: no cover - defensive branch
            raise ValueError(f"Unknown log level: {name!r}") from exc

    @classmethod
    def from_python_level(cls, level: int) -> "LogLevel":
        """Translate a stdlib logging level integer into :class:`LogLevel`.

        Examples
        --------
        >>> LogLevel.from_python_level(logging.INFO) is LogLevel.INFO
        True
        """

        return cls.from_numeric(level)

    @classmethod
    def from_numeric(cls, level: int) -> "LogLevel":
        """Return the :class:`LogLevel` corresponding to ``level``.

        Parameters
        ----------
        level:
            Integer severity, typically originating from Python's logging API.

        Raises
        ------
        ValueError
            If the integer does not map to a known level.

        Examples
        --------
        >>> LogLevel.from_numeric(30) is LogLevel.WARNING
        True
        >>> LogLevel.from_numeric(5)
        Traceback (most recent call last):
        ...
        ValueError: Unsupported log level numeric: 5
        """

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


_CODE_TABLE = {
    LogLevel.DEBUG: "DEBG",
    LogLevel.INFO: "INFO",
    LogLevel.WARNING: "WARN",
    LogLevel.ERROR: "ERRO",
    LogLevel.CRITICAL: "CRIT",
}
# Four-character abbreviations for formatter strings and column layouts.


__all__ = ["LogLevel"]
