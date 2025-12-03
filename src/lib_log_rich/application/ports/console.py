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

Alignment Notes
---------------
The contract mirrors the console behaviour documented in
``docs/systemdesign/module_reference.md`` (colour toggles, context-aware output).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class ConsolePort(Protocol):
    """Render a log event to an interactive console.

    Enables dependency inversion between the application use case and the Rich
    adapter while still supporting alternative console implementations in tests.

    Args:
        event: :class:`LogEvent` ready to display.
        colorize: When ``True`` adapters should render using ANSI colour codes;
            ``False`` is required for plain-text environments.

    Example:
        >>> class Recorder:
        ...     def __init__(self):
        ...         self.called = False
        ...     def emit(self, event: LogEvent, *, colorize: bool) -> None:
        ...         self.called = colorize
        >>> isinstance(Recorder(), ConsolePort)
        True

    """

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        """Render ``event`` with optional colour control."""


__all__ = ["ConsolePort"]
