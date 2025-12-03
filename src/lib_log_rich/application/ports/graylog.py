"""Port describing the optional Graylog/GELF adapter.

Alignment Notes
---------------
Captures the contract from ``docs/systemdesign/module_reference.md`` covering
Graylog transport expectations (emit + flush for graceful shutdown).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class GraylogPort(Protocol):
    """Emit structured events to a Graylog instance via GELF.

    Keeps the Graylog integration optional and swappable while ensuring the
    pipeline can request a graceful drain at shutdown.

    Example:
        >>> class Recorder:
        ...     def __init__(self):
        ...         self.calls = []
        ...     def emit(self, event: LogEvent) -> None:
        ...         self.calls.append(event.logger_name)
        ...     async def flush(self) -> None:
        ...         self.calls.append('flush')
        >>> isinstance(Recorder(), GraylogPort)
        True

    """

    def emit(self, event: LogEvent) -> None:
        """Send ``event`` to Graylog using GELF."""

    async def flush(self) -> None:
        """Flush buffered data (if any)."""


__all__ = ["GraylogPort"]
