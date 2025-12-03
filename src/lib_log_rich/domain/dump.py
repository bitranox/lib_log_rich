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
(``concept_architecture.md`` section on ring buffer introspection).

Alignment Notes
---------------
Matches the options described in ``docs/systemdesign/module_reference.md`` so
user-facing docs and CLI help remain authoritative.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache


class DumpFormat(Enum):
    """Define the supported export targets for ring buffer dumps.

    Centralising the mapping prevents drift between CLI validation, adapters,
    and documentation while keeping the public API expressive.

    Example:
        >>> DumpFormat.TEXT.value
        'text'
        >>> DumpFormat.HTML_TABLE.name
        'HTML_TABLE'

    """

    TEXT = "text"
    JSON = "json"
    HTML_TABLE = "html_table"
    HTML_TXT = "html_txt"

    @classmethod
    @lru_cache(maxsize=8)
    def from_name(cls, name: str) -> DumpFormat:
        """Return the matching enum member for a case-insensitive name.

        Args:
            name: Human-entered string, typically from CLI flags or config files.

        Returns:
            Resolved enumeration member.

        Raises:
            ValueError: If the provided name is not recognised.

        Example:
            >>> DumpFormat.from_name('JSON') is DumpFormat.JSON
            True
            >>> DumpFormat.from_name('  html_table  ') is DumpFormat.HTML_TABLE
            True
            >>> DumpFormat.from_name('html') is DumpFormat.HTML_TABLE
            True
            >>> DumpFormat.from_name('yaml')
            Traceback (most recent call last):
            ...
            ValueError: Unsupported dump format: 'yaml'

        """
        normalized = name.strip().lower()
        if normalized == "html":
            normalized = DumpFormat.HTML_TABLE.value
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unsupported dump format: {name!r}")


__all__ = ["DumpFormat"]
