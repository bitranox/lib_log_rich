"""Ports for structured backend adapters (journald, Windows, etc.).

Alignment Notes
---------------
Matches the OS backend integration contract described in
``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class StructuredBackendPort(Protocol):
    """Persist structured log events to an operating-system backend.

    Keeps journald/Event Log implementations interchangeable and testable.

    Example:
        >>> class Recorder:
        ...     def emit(self, event: LogEvent) -> None:
        ...         pass
        >>> isinstance(Recorder(), StructuredBackendPort)
        True

    """

    def emit(self, event: LogEvent) -> None:
        """Forward ``event`` to the structured backend."""


__all__ = ["StructuredBackendPort"]
