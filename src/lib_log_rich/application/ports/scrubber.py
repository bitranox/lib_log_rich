"""Port for redacting sensitive information."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class ScrubberPort(Protocol):
    """Scrub sensitive values from log events before emission."""

    def scrub(self, event: LogEvent) -> LogEvent:
        """Return a (possibly) redacted copy of ``event``."""


__all__ = ["ScrubberPort"]
