"""Regex-based field scrubber.

Applies configurable regular expressions to the ``extra`` payload of
:class:`LogEvent` objects, masking sensitive values before fan-out.

Alignment Notes
---------------
Implements the scrubbing policy described under "Security & Privacy" in
``docs/systemdesign/konzept_architecture.md``.
"""

from __future__ import annotations

import re
from typing import Dict, Pattern

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
            if key in extra and isinstance(extra[key], str):
                if regex.search(extra[key]):
                    extra[key] = self._replacement
        return event.replace(extra=extra)


__all__ = ["RegexScrubber"]
