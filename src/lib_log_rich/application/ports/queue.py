"""Port describing the queue infrastructure for fan-out processing."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class QueuePort(Protocol):
    """Bridge between producer processes and the listener worker."""

    def start(self) -> None:
        """Start the queue worker."""

    def stop(self, *, drain: bool = True) -> None:
        """Stop the queue worker, optionally draining queued events."""

    def put(self, event: LogEvent) -> None:
        """Enqueue ``event`` for asynchronous processing."""


__all__ = ["QueuePort"]
