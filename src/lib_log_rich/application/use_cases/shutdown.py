"""Shutdown orchestration for the logging backbone.

Purpose
-------
Provide a unified shutdown routine that drains queues, flushes adapters, and
persists the ring buffer.

Alignment Notes
---------------
Replicates the shutdown sequence outlined in ``docs/systemdesign/module_reference.md``
so operators know exactly which resources are touched.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from lib_log_rich.application.ports.graylog import GraylogPort
from lib_log_rich.application.ports.queue import QueuePort
from lib_log_rich.domain import RingBuffer


def create_shutdown(
    *,
    queue: QueuePort | None,
    graylog: GraylogPort | None,
    ring_buffer: RingBuffer | None,
) -> Callable[[], Awaitable[None]]:
    """Return an async callable performing the shutdown sequence.

    Encapsulating shutdown logic keeps the composition root small and allows
    tests to inject fakes that observe the order of operations.

    Args:
        queue: Optional event queue adapter; ``None`` when inline fan-out is used.
        graylog: Optional Graylog adapter whose buffers must flush before exit.
        ring_buffer: Optional ring buffer to persist before teardown.

    Returns:
        Async callable executed during :func:`lib_log_rich.shutdown`.

    Example:
        >>> class DummyQueue(QueuePort):
        ...     def __init__(self):
        ...         self.stopped = False
        ...     def put(self, event):
        ...         pass
        ...     def stop(self, drain: bool) -> None:
        ...         self.stopped = drain
        >>> class DummyGraylog(GraylogPort):
        ...     def __init__(self):
        ...         self.flushed = False
        ...     async def emit(self, event):
        ...         pass
        ...     async def flush(self) -> None:
        ...         self.flushed = True
        >>> class DummyRing(RingBuffer):
        ...     def __init__(self):
        ...         pass
        ...     def flush(self) -> None:
        ...         self.flushed = True
        >>> queue = DummyQueue()
        >>> graylog = DummyGraylog()
        >>> ring = DummyRing()
        >>> shutdown = create_shutdown(queue=queue, graylog=graylog, ring_buffer=ring)
        >>> import asyncio
        >>> asyncio.run(shutdown())
        >>> queue.stopped and graylog.flushed
        True

    """

    async def shutdown() -> None:
        """Drain queues, flush adapters, and persist buffered events.

        Execution order: Queue stop → Graylog flush → ring buffer flush,
        matching the resilience plan so that structured backends see every
        event before state is cleared.
        """
        if queue is not None:
            queue.stop(drain=True)
        if graylog is not None:
            await graylog.flush()
        if ring_buffer is not None:
            ring_buffer.flush()

    return shutdown


__all__ = ["create_shutdown"]
