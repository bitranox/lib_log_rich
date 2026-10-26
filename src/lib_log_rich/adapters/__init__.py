"""Adapter implementations bridging application ports to concrete systems.

Purpose
-------
Collect all outer-layer implementations so composition roots can import a single
module when wiring ports to concrete dependencies, as described in
``docs/systemdesign/concept_architecture.md``.

Contents
--------
* Console adapter backed by Rich for terminal rendering.
* Structured backends for journald and the Windows Event Log.
* Graylog GELF adapter, dump exporter, queue orchestration, scrubber, and
  sliding-window rate limiter.
* JSONL checkpoint persisting the ring buffer, and the JSON text form of a
  context snapshot for handing to a child process.

System Role
-----------
Groups the outermost Clean Architecture layer, keeping re-exports organised and
documented for system design references and IDE navigation.
"""

from __future__ import annotations

from .checkpoint import JsonlCheckpoint
from .console.queue_console import AsyncQueueConsoleAdapter, QueueConsoleAdapter
from .console.rich_console import RichConsoleAdapter
from .context_snapshot import dump_context_snapshot, load_context_snapshot
from .dump import DumpAdapter
from .graylog import GraylogAdapter
from .queue import QueueAdapter
from .rate_limiter import SlidingWindowRateLimiter
from .scrubber import RegexScrubber
from .structured import JournaldAdapter, WindowsEventLogAdapter

__all__ = [
    "AsyncQueueConsoleAdapter",
    "DumpAdapter",
    "GraylogAdapter",
    "JournaldAdapter",
    "JsonlCheckpoint",
    "QueueAdapter",
    "QueueConsoleAdapter",
    "RegexScrubber",
    "RichConsoleAdapter",
    "SlidingWindowRateLimiter",
    "WindowsEventLogAdapter",
    "dump_context_snapshot",
    "load_context_snapshot",
]
