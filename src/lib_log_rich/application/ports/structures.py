"""Ports for structured backend adapters (journald, Windows, etc.)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class StructuredBackendPort(Protocol):
    """Persist structured log events to an operating-system backend."""

    def emit(self, event: LogEvent) -> None:
        """Forward ``event`` to the structured backend."""


__all__ = ["StructuredBackendPort"]
