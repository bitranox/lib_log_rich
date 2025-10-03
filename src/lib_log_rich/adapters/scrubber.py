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
from collections.abc import Mapping, Sequence, Set
from typing import Any, Dict, Pattern, cast

from lib_log_rich.application.ports.scrubber import ScrubberPort
from lib_log_rich.domain.events import LogEvent


class RegexScrubber(ScrubberPort):
    """Redact sensitive fields using regular expressions.

    Why
    ---
    Keeps credential masking configurable while ensuring the application layer
    depends on a simple :class:`ScrubberPort`.

    Parameters
    ----------
    patterns:
        Mapping of field name → regex string; matching values are redacted.
    replacement:
        Token replacing matched values (defaults to ``"***"``).

    Examples
    --------
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
        self._patterns: Dict[str, Pattern[str]] = {}
        for key, pattern in patterns.items():
            normalised = self._normalise_key(key)
            if not normalised:
                continue
            self._patterns[normalised] = re.compile(pattern)
        self._replacement = replacement

    def scrub(self, event: LogEvent) -> LogEvent:
        """Return a copy of ``event`` with matching extra fields redacted."""
        extra_copy = dict(event.extra)
        extra_changed = False
        for key, value in list(extra_copy.items()):
            pattern = self._patterns.get(self._normalise_key(key))
            if pattern is None:
                continue
            scrubbed = self._scrub_value(value, pattern)
            if scrubbed != value:
                extra_changed = True
            extra_copy[key] = scrubbed

        context = event.context
        context_extra_copy = dict(context.extra)
        context_changed = False
        if context_extra_copy:
            for key, value in list(context_extra_copy.items()):
                pattern = self._patterns.get(self._normalise_key(key))
                if pattern is None:
                    continue
                scrubbed = self._scrub_value(value, pattern)
                if scrubbed != value:
                    context_changed = True
                context_extra_copy[key] = scrubbed
            if context_changed:
                context = context.replace(extra=context_extra_copy)

        if not extra_changed and not context_changed:
            return event

        return event.replace(
            extra=extra_copy if extra_changed else event.extra,
            context=context,
        )

    @staticmethod
    def _normalise_key(name: str) -> str:
        return name.strip().casefold()

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
            return self._replacement if pattern.search(value) else value
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="ignore")
            return self._replacement if pattern.search(text) else value
        if isinstance(value, Mapping):
            mapping = cast(Mapping[Any, Any], value)
            result: dict[Any, Any] = {}
            for key, item in mapping.items():
                result[key] = self._scrub_value(item, pattern)
            return result
        if isinstance(value, set):
            set_iter = cast(Set[Any], value)
            return {self._scrub_value(item, pattern) for item in set_iter}
        if isinstance(value, frozenset):
            frozen_iter = cast(Set[Any], value)
            return frozenset(self._scrub_value(item, pattern) for item in frozen_iter)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            sequence_iter = cast(Sequence[Any], value)
            converted: list[Any] = [self._scrub_value(item, pattern) for item in sequence_iter]
            if isinstance(value, tuple):
                return tuple(converted)
            return converted
        return value


__all__ = ["RegexScrubber"]
