"""Shared JSON coercion utilities for adapter serialization.

Purpose
-------
Provide a unified interface for coercing Python objects to JSON-serializable
representations, ensuring consistent behavior across adapters.

Contents
--------
* :func:`coerce_json_value` – recursively coerce values to JSON-compatible types.

System Role
-----------
Eliminates duplication between Graylog and schema adapters while maintaining
the serialization contracts defined in ``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from lib_log_rich.domain.paths import path_to_posix


def _coerce_datetime(value: datetime | date) -> str:
    """Coerce datetime/date to ISO format string."""
    return value.isoformat()


def _coerce_bytes(value: bytes) -> str:
    """Coerce bytes to UTF-8 string or hex representation."""
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.hex()


def _coerce_mapping(mapping: Mapping[Any, Any]) -> dict[str, Any]:
    """Recursively coerce mapping to JSON-compatible dict."""
    return {str(key): coerce_json_value(item) for key, item in mapping.items()}


def _coerce_iterable(items: Iterable[Any]) -> list[Any]:
    """Recursively coerce iterable to JSON-compatible list."""
    return [coerce_json_value(item) for item in items]


def coerce_json_value(value: Any) -> Any:
    """Return a JSON-serializable representation of ``value``.

    Recursively processes nested structures (mappings, iterables) and converts
    special types (datetime, bytes, Path) to their string representations.

    Args:
        value: Any Python value to coerce.

    Returns:
        A JSON-serializable equivalent of the input value.

    Examples:
        >>> coerce_json_value(None)
        >>> coerce_json_value("hello")
        'hello'
        >>> coerce_json_value(42)
        42
        >>> from datetime import datetime, timezone
        >>> coerce_json_value(datetime(2025, 1, 1, tzinfo=timezone.utc))
        '2025-01-01T00:00:00+00:00'
        >>> coerce_json_value(b"hello")
        'hello'
        >>> coerce_json_value({"a": 1})
        {'a': 1}
        >>> coerce_json_value([1, 2, 3])
        [1, 2, 3]

    """
    # Primitives pass through
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    # Date/time types
    if isinstance(value, (datetime, date)):
        return _coerce_datetime(value)

    # Bytes
    if isinstance(value, bytes):
        return _coerce_bytes(value)

    # Mappings
    if isinstance(value, Mapping):
        return _coerce_mapping(cast(Mapping[Any, Any], value))

    # Iterables (excluding str/bytes)
    if isinstance(value, (list, tuple, set, frozenset)):
        return _coerce_iterable(cast(Iterable[Any], value))

    # Path objects - serialize as POSIX for cross-platform compatibility
    if isinstance(value, Path):
        return path_to_posix(value)

    # Fallback: string representation
    return str(value)


__all__ = ["coerce_json_value"]
