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
from collections.abc import Mapping, Sequence, Set as AbstractSet
from typing import Any, Dict, Pattern

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
        self._patterns: Dict[str, Pattern[str]] = {key: re.compile(pattern) for key, pattern in patterns.items()}
        self._replacement = replacement

    def scrub(self, event: LogEvent) -> LogEvent:
        """Return a copy of ``event`` with matching extra fields redacted."""
        extra = dict(event.extra)
        for key, regex in self._patterns.items():
            if key not in extra:
                continue
            extra[key] = self._scrub_value(extra[key], regex)
        return event.replace(extra=extra)

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
            return {k: self._scrub_value(v, pattern) for k, v in value.items()}
        if isinstance(value, AbstractSet):
            return type(value)(self._scrub_value(item, pattern) for item in value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            converted = [self._scrub_value(item, pattern) for item in value]
            if isinstance(value, tuple):
                return tuple(converted)
            return type(value)(converted)
        return value


__all__ = ["RegexScrubber"]
