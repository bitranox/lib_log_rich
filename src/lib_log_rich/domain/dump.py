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
"""

from __future__ import annotations

from enum import Enum


class DumpFormat(Enum):
    """Define the supported export targets for ring buffer dumps."""

    TEXT = "text"
    JSON = "json"
    HTML = "html"

    @classmethod
    def from_name(cls, name: str) -> "DumpFormat":
        """Return the matching enum member for a case-insensitive name."""
        normalized = name.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Unsupported dump format: {name!r}")


__all__ = ["DumpFormat"]
