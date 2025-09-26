"""Shutdown orchestration for the logging backbone.

Purpose
-------
Provide a unified shutdown routine that drains queues, flushes adapters, and
persists the ring buffer.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from lib_log_rich.domain import RingBuffer
from lib_log_rich.application.ports.graylog import GraylogPort
from lib_log_rich.application.ports.queue import QueuePort


def create_shutdown(
    *,
    queue: QueuePort | None,
    graylog: GraylogPort | None,
    ring_buffer: RingBuffer | None,
) -> Callable[[], Awaitable[None]]:
    """Return an async callable performing the shutdown sequence."""

    async def shutdown() -> None:
        """Drain queues, flush adapters, and persist buffered events."""
        if queue is not None:
            queue.stop(drain=True)
        if graylog is not None:
            await graylog.flush()
        if ring_buffer is not None:
            ring_buffer.flush()

    return shutdown


__all__ = ["create_shutdown"]
