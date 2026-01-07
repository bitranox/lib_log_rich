"""Shutdown and flush orchestration for the logging backbone.

Purpose
-------
Provide unified shutdown and flush routines that drain queues, flush adapters,
and persist the ring buffer.

Alignment Notes
---------------
Replicates the shutdown sequence outlined in ``docs/systemdesign/module_reference.md``
so operators know exactly which resources are touched.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.application.ports.graylog import GraylogPort
from lib_log_rich.application.ports.queue import QueuePort
from lib_log_rich.domain import RingBuffer


async def _flush_adapters(
    *,
    console: ConsolePort | None,
    graylog: GraylogPort | None,
    ring_buffer: RingBuffer | None,
    flush_ring_buffer: bool,
) -> None:
    """Flush all adapters in the correct order.

    Execution order: Console flush → Graylog flush → ring buffer flush.
    This ensures streams are flushed before network adapters, and ring buffer
    is persisted last.

    Args:
        console: Optional console adapter whose streams must flush.
        graylog: Optional Graylog adapter whose buffers must flush.
        ring_buffer: Optional ring buffer to persist.
        flush_ring_buffer: Whether to flush the ring buffer.
    """
    if console is not None:
        console.flush()
    if graylog is not None:
        await graylog.flush()
    if flush_ring_buffer and ring_buffer is not None:
        ring_buffer.flush()


def create_shutdown(
    *,
    queue: QueuePort | None,
    console: ConsolePort | None,
    graylog: GraylogPort | None,
    ring_buffer: RingBuffer | None,
) -> Callable[[], Awaitable[None]]:
    """Return an async callable performing the shutdown sequence.

    Encapsulating shutdown logic keeps the composition root small and allows
    tests to inject fakes that observe the order of operations.

    Args:
        queue: Optional event queue adapter; ``None`` when inline fan-out is used.
        console: Optional console adapter whose streams must flush.
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
        >>> class DummyConsole(ConsolePort):
        ...     def __init__(self):
        ...         self.flushed = False
        ...     def emit(self, event, *, colorize: bool) -> None:
        ...         pass
        ...     def flush(self) -> None:
        ...         self.flushed = True
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
        >>> console = DummyConsole()
        >>> graylog = DummyGraylog()
        >>> ring = DummyRing()
        >>> shutdown = create_shutdown(queue=queue, console=console, graylog=graylog, ring_buffer=ring)
        >>> import asyncio
        >>> asyncio.run(shutdown())
        >>> queue.stopped and console.flushed and graylog.flushed
        True

    """

    async def shutdown() -> None:
        """Drain queues, flush adapters, and persist buffered events.

        Execution order: Queue stop → Console flush → Graylog flush → ring buffer flush,
        matching the resilience plan so that structured backends see every
        event before state is cleared.
        """
        if queue is not None:
            queue.stop(drain=True)
        await _flush_adapters(
            console=console,
            graylog=graylog,
            ring_buffer=ring_buffer,
            flush_ring_buffer=True,
        )

    return shutdown


def create_flush(
    *,
    queue: QueuePort | None,
    console: ConsolePort | None,
    graylog: GraylogPort | None,
    ring_buffer: RingBuffer | None,
    default_timeout: float = 5.0,
) -> Callable[[float | None, bool], Coroutine[Any, Any, None]]:
    """Return an async callable performing the flush sequence without terminating.

    Unlike :func:`create_shutdown`, this flushes queues and adapters while keeping
    the runtime active. The queue worker continues running after the flush completes.

    Args:
        queue: Optional event queue adapter; ``None`` when inline fan-out is used.
        console: Optional console adapter whose streams must flush.
        graylog: Optional Graylog adapter whose buffers must flush.
        ring_buffer: Optional ring buffer to persist.
        default_timeout: Default seconds to wait for queue drain if not specified.

    Returns:
        Async callable accepting timeout and flush_ring_buffer parameters.

    Raises:
        TimeoutError: If the queue does not drain within the specified timeout.

    Example:
        >>> class DummyQueue(QueuePort):
        ...     def __init__(self):
        ...         self.idle_called = False
        ...     def start(self) -> None:
        ...         pass
        ...     def stop(self, *, drain: bool = True, timeout: float | None = 5.0) -> None:
        ...         pass
        ...     def put(self, event):
        ...         return True
        ...     def wait_until_idle(self, timeout: float | None = None) -> bool:
        ...         self.idle_called = True
        ...         return True
        >>> class DummyConsole(ConsolePort):
        ...     def __init__(self):
        ...         self.flushed = False
        ...     def emit(self, event, *, colorize: bool) -> None:
        ...         pass
        ...     def flush(self) -> None:
        ...         self.flushed = True
        >>> class DummyGraylog(GraylogPort):
        ...     def __init__(self):
        ...         self.flushed = False
        ...     async def emit(self, event):
        ...         pass
        ...     async def flush(self) -> None:
        ...         self.flushed = True
        >>> class DummyRing(RingBuffer):
        ...     def __init__(self):
        ...         self.flushed = False
        ...     def flush(self) -> None:
        ...         self.flushed = True
        >>> queue = DummyQueue()
        >>> console = DummyConsole()
        >>> graylog = DummyGraylog()
        >>> ring = DummyRing()
        >>> flush = create_flush(queue=queue, console=console, graylog=graylog, ring_buffer=ring)
        >>> import asyncio
        >>> asyncio.run(flush(timeout=5.0, flush_ring_buffer=True))
        >>> queue.idle_called and console.flushed and graylog.flushed and ring.flushed
        True

    """

    async def flush(timeout: float | None = None, flush_ring_buffer: bool = False) -> None:
        """Drain queues and flush adapters without terminating the runtime.

        Execution order: Queue wait → Console flush → Graylog flush → ring buffer flush.
        Unlike shutdown, the queue worker keeps running after this completes.

        Args:
            timeout: Maximum seconds to wait for queue drain. Uses default if ``None``.
            flush_ring_buffer: Whether to persist the ring buffer. Default ``False``.

        Raises:
            TimeoutError: If the queue does not drain within the timeout.
        """
        effective_timeout = timeout if timeout is not None else default_timeout

        # 1. Wait for queue to drain (all events processed)
        if queue is not None:
            success = queue.wait_until_idle(effective_timeout)
            if not success:
                msg = f"Queue did not drain within {effective_timeout}s"
                raise TimeoutError(msg)

        # 2. Flush all adapters (console, graylog, ring buffer)
        await _flush_adapters(
            console=console,
            graylog=graylog,
            ring_buffer=ring_buffer,
            flush_ring_buffer=flush_ring_buffer,
        )

    return flush


__all__ = ["create_flush", "create_shutdown"]
