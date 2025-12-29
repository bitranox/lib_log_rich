"""Regex-based field scrubber.

Purpose
-------
Apply configurable regular expressions to the ``extra`` payload of
:class:`LogEvent` objects so secrets are masked before adapters receive the
event.

Contents
--------
* :class:`RegexScrubber` – concrete :class:`ScrubberPort` implementation.

System Role
-----------
Enforces the "Security & Privacy" guidance in ``concept_architecture.md`` by
ensuring sensitive fields never leave the application layer unredacted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from re import Pattern
from typing import Any, cast

from lib_log_rich.application.ports.scrubber import ScrubberPort
from lib_log_rich.domain.events import LogEvent


class RegexScrubber(ScrubberPort):
    """Redact sensitive fields using regular expressions.

    Keeps credential masking configurable while ensuring the application layer
    depends on a simple :class:`ScrubberPort`.

    Args:
        patterns: Mapping of field name → regex string; matching values are redacted.
        replacement: Token replacing matched values (defaults to ``"***"``).

    Example:
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx, extra={'token': 'secret123'})
        >>> scrubber = RegexScrubber(patterns={'token': 'secret'})
        >>> scrubber.scrub(event).extra['token']
        '***'

    """

    def __init__(self, *, patterns: dict[str, str], replacement: str = "***") -> None:
        """Compile the provided ``patterns`` and store the replacement token."""
        self._patterns: dict[str, Pattern[str]] = {}
        for key, pattern in patterns.items():
            normalised = self._normalise_key(key)
            if not normalised:
                continue
            self._patterns[normalised] = re.compile(pattern)
        self._replacement = replacement

    def _scrub_dict(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Scrub a dictionary, returning (scrubbed_dict, was_changed)."""
        data_copy = dict(data)
        changed = False
        for key, value in list(data_copy.items()):
            pattern = self._patterns.get(self._normalise_key(key))
            if pattern is None:
                continue
            scrubbed = self._scrub_value(value, pattern)
            if scrubbed != value:
                changed = True
            data_copy[key] = scrubbed
        return data_copy, changed

    def scrub(self, event: LogEvent) -> LogEvent:
        """Return a copy of ``event`` with matching extra fields redacted."""
        extra_copy, extra_changed = self._scrub_dict(event.extra)

        context = event.context
        if context.extra:
            context_extra_copy, context_changed = self._scrub_dict(context.extra)
            if context_changed:
                context = context.replace(extra=context_extra_copy)
        else:
            context_changed = False

        if not extra_changed and not context_changed:
            return event

        return event.replace(
            extra=extra_copy if extra_changed else event.extra,
            context=context,
        )

    @staticmethod
    @lru_cache(maxsize=32)
    def _normalise_key(name: str) -> str:
        return name.strip().casefold()

    def _scrub_string(self, value: str, pattern: Pattern[str]) -> str:
        """Scrub a string value using the pattern."""
        return self._replacement if pattern.search(value) else value

    def _scrub_bytes(self, value: bytes, pattern: Pattern[str]) -> bytes | str:
        """Scrub bytes value by decoding and checking for pattern."""
        text = value.decode("utf-8", errors="ignore")
        return self._replacement if pattern.search(text) else value

    def _scrub_mapping(self, mapping: Mapping[Any, Any], pattern: Pattern[str]) -> dict[Any, Any]:
        """Recursively scrub mapping values."""
        result: dict[Any, Any] = {}
        for key, item in mapping.items():
            result[key] = self._scrub_value(item, pattern)
        return result

    def _scrub_set(self, value: set[Any] | frozenset[Any], pattern: Pattern[str]) -> set[Any] | frozenset[Any]:
        """Recursively scrub set elements."""
        scrubbed = {self._scrub_value(item, pattern) for item in value}
        if isinstance(value, frozenset):
            return frozenset(scrubbed)
        return scrubbed

    def _scrub_sequence(self, value: Sequence[Any], pattern: Pattern[str]) -> list[Any] | tuple[Any, ...]:
        """Recursively scrub sequence elements."""
        converted = [self._scrub_value(item, pattern) for item in value]
        if isinstance(value, tuple):
            return tuple(converted)
        return converted

    def _scrub_value(self, value: Any, pattern: Pattern[str]) -> Any:
        """Recursively scrub ``value`` using ``pattern``.

        Why
        ---
        ``extra`` payloads often contain nested structures. This helper enforces
        the redaction contract across mappings, sequences, sets, and raw bytes.

        Inputs
        ------
        value:
            Arbitrary payload extracted from :class:`LogEvent.extra`.
        pattern:
            Compiled regular expression associated with the field name.

        Outputs
        -------
        Any
            Original value when it does not match; the replacement token (or
            structure containing it) when matches are found.
        """
        if isinstance(value, str):
            return self._scrub_string(value, pattern)
        if isinstance(value, bytes):
            return self._scrub_bytes(value, pattern)
        if isinstance(value, Mapping):
            return self._scrub_mapping(cast(Mapping[Any, Any], value), pattern)
        if isinstance(value, (set, frozenset)):
            return self._scrub_set(cast("set[Any] | frozenset[Any]", value), pattern)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return self._scrub_sequence(cast(Sequence[Any], value), pattern)
        return value


__all__ = ["RegexScrubber"]
