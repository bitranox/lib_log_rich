"""Port for rate limiting filters protecting downstream sinks."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class RateLimiterPort(Protocol):
    """Decide whether a log event may pass through the pipeline."""

    def allow(self, event: LogEvent) -> bool:
        """Return ``True`` when ``event`` is permitted to proceed."""


__all__ = ["RateLimiterPort"]
