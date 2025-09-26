"""Regex-based field scrubber.

Applies configurable regular expressions to the ``extra`` payload of
:class:`LogEvent` objects, masking sensitive values before fan-out.
"""

from __future__ import annotations

import re
from typing import Dict, Pattern

from lib_log_rich.application.ports.scrubber import ScrubberPort
from lib_log_rich.domain.events import LogEvent


class RegexScrubber(ScrubberPort):
    """Redact sensitive fields using regular expressions."""

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
