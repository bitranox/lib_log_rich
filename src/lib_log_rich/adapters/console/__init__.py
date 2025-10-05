"""Console adapter exports for terminal and queue-based rendering.

Purpose
-------
Expose Rich-powered console adapters so composition roots can wire
:class:`lib_log_rich.application.ports.console.ConsolePort` implementations
without reaching into module internals. The queue variants enable streaming log
output to external consumers (Textual TUI, Flask SSE) while reusing the Rich
formatting pipeline documented in ``docs/systemdesign/module_reference.md``.

Contents
--------
* :class:`RichConsoleAdapter` – renders directly to a Rich console instance.
* :class:`QueueConsoleAdapter` – enqueues ANSI/HTML segments into a thread-safe
  queue for background consumers.
* :class:`AsyncQueueConsoleAdapter` – asyncio variant for cooperative tasks.

System Role
-----------
Defines the adapter-layer boundary for console rendering inside the Clean
Architecture stack, ensuring higher layers depend only on the published surface.
"""

from __future__ import annotations

from .queue_console import AsyncQueueConsoleAdapter, ExportStyle, QueueConsoleAdapter
from .rich_console import RichConsoleAdapter

__all__ = [
    "RichConsoleAdapter",
    "QueueConsoleAdapter",
    "AsyncQueueConsoleAdapter",
    "ExportStyle",
]
