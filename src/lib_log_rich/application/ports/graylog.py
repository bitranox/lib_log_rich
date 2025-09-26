"""Port describing the optional Graylog/GELF adapter."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class GraylogPort(Protocol):
    """Emit structured events to a Graylog instance via GELF."""

    def emit(self, event: LogEvent) -> None:
        """Send ``event`` to Graylog using GELF."""

    async def flush(self) -> None:
        """Flush buffered data (if any)."""


__all__ = ["GraylogPort"]
