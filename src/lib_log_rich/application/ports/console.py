"""Console port describing terminal emission contracts.

Purpose
-------
Define the abstraction for adapters that render log events to interactive
consoles, letting the application layer depend on a narrow protocol.

Contents
--------
* :class:`ConsolePort` – runtime-checkable protocol with a single ``emit``
  method supporting optional colour control.

System Role
-----------
Clarifies the console-facing boundary in the Clean Architecture stack so
adapters (e.g. Rich) can plug in without leaking implementation details
upstream.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class ConsolePort(Protocol):
    """Render a log event to an interactive console."""

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        """Render ``event`` with optional colour control."""


__all__ = ["ConsolePort"]
