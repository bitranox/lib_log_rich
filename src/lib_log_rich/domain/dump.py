"""Dump format enumeration for ring buffer exports.

Purpose
-------
Standardise the supported output formats referenced in documentation,
configuration, and adapters.

Contents
--------
* :class:`DumpFormat` enumeration with parsing helpers.

System Role
-----------
Ensures application/adapters share a canonical understanding of dump formats
(``konzept_architecture.md`` section on ring buffer introspection).

Alignment Notes
---------------
Matches the options described in ``docs/systemdesign/module_reference.md`` so
user-facing docs and CLI help remain authoritative.
"""

from __future__ import annotations

from enum import Enum


class DumpFormat(Enum):
    """Define the supported export targets for ring buffer dumps.

    Why
    ---
    Centralising the mapping prevents drift between CLI validation, adapters,
    and documentation while keeping the public API expressive.

    Examples
    --------
    >>> DumpFormat.TEXT.value
    'text'
    >>> DumpFormat.HTML.name
    'HTML'
    """

    TEXT = "text"
    JSON = "json"
    HTML = "html"

    @classmethod
    def from_name(cls, name: str) -> "DumpFormat":
        """Return the matching enum member for a case-insensitive name.

        Parameters
        ----------
        name:
            Human-entered string, typically from CLI flags or config files.

        Returns
        -------
        DumpFormat
            Resolved enumeration member.

        Raises
        ------
        ValueError
            If the provided name is not recognised.

        Examples
        --------
        >>> DumpFormat.from_name('JSON') is DumpFormat.JSON
        True
        >>> DumpFormat.from_name('  html  ') is DumpFormat.HTML
        True
        >>> DumpFormat.from_name('yaml')
        Traceback (most recent call last):
        ...
        ValueError: Unsupported dump format: 'yaml'
        """

        normalized = name.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unsupported dump format: {name!r}")


__all__ = ["DumpFormat"]
