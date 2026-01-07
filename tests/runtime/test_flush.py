from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from lib_log_rich.application.use_cases._types import ProcessResult
from lib_log_rich.application.use_cases.shutdown import create_flush
from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer, SeverityMonitor
from lib_log_rich.runtime._settings import PayloadLimits
from lib_log_rich.runtime._state import (
    LoggingRuntime,
    clear_runtime,
    is_initialised,
    set_runtime,
)
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


class RecordingQueue:
    """Fake queue that records method calls."""

    def __init__(self, *, drain_success: bool = True) -> None:
        self.started = False
        self.stopped = False
        self.drain_on_stop = False
        self.idle_called = False
        self.idle_timeout: float | None = None
        self._drain_success = drain_success
        self.events: list[LogEvent] = []

    def start(self) -> None:
        self.started = True

    def stop(self, *, drain: bool = True, timeout: float | None = 5.0) -> None:
        self.stopped = True
        self.drain_on_stop = drain

    def put(self, event: LogEvent) -> bool:
        self.events.append(event)
        return True

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        self.idle_called = True
        self.idle_timeout = timeout
        return self._drain_success


class RecordingConsole:
    """Fake console that records method calls."""

    def __init__(self) -> None:
        self.events: list[LogEvent] = []
        self.colorize_flags: list[bool] = []
        self.flushed = False

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        self.events.append(event)
        self.colorize_flags.append(colorize)

    def flush(self) -> None:
        self.flushed = True


class RecordingGraylog:
    """Fake Graylog that records method calls."""

    def __init__(self) -> None:
        self.events: list[LogEvent] = []
        self.flushed = False

    def emit(self, event: LogEvent) -> None:
        self.events.append(event)

    async def flush(self) -> None:
        self.flushed = True


class RecordingRingBuffer(RingBuffer):
    """Fake ring buffer that records method calls."""

    def __init__(self, checkpoint_path: Path | None = None) -> None:
        super().__init__(max_events=100, checkpoint_path=checkpoint_path)
        self.flushed = False

    def flush(self) -> None:
        self.flushed = True


async def _mock_flush_async(timeout: float | None = None, flush_ring_buffer: bool = False) -> None:
    """Mock flush_async function for test LoggingRuntime instances."""
    return None


@pytest.fixture(autouse=True)
def runtime_state_clean() -> Iterator[None]:
    clear_runtime()
    yield
    clear_runtime()


def _make_runtime(
    *,
    queue: Any = None,
    flush_async_fn: Any | None = None,
) -> LoggingRuntime:
    """Create a test runtime with optional queue and flush_async override."""
    binder = ContextBinder()
    monitor = SeverityMonitor()

    def process(**payload: object) -> ProcessResult:
        return ProcessResult(ok=True)

    def capture_dump(**kwargs: object) -> str:
        return ""

    def shutdown() -> None:
        return None

    actual_flush = flush_async_fn if flush_async_fn is not None else _mock_flush_async

    return LoggingRuntime(
        binder=binder,
        process=process,
        capture_dump=capture_dump,
        shutdown_async=shutdown,
        flush_async=actual_flush,
        queue=queue,  # type: ignore[arg-type]  # Test double
        service="svc",
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        backend_enabled=False,
        graylog_level=LogLevel.ERROR,
        graylog_enabled=False,
        severity_monitor=monitor,
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )


class TestCreateFlush:
    """Tests for the create_flush() factory function."""

    def test_flush_waits_for_queue_to_drain(self) -> None:
        """flush() calls wait_until_idle on the queue."""
        queue = RecordingQueue()
        console = RecordingConsole()
        flush_fn = create_flush(
            queue=queue,
            console=console,
            graylog=None,
            ring_buffer=None,
        )
        asyncio.run(flush_fn(10.0, False))
        assert queue.idle_called is True
        assert queue.idle_timeout == 10.0

    def test_flush_uses_default_timeout(self) -> None:
        """flush() uses default timeout when not specified."""
        queue = RecordingQueue()
        console = RecordingConsole()
        flush_fn = create_flush(
            queue=queue,
            console=console,
            graylog=None,
            ring_buffer=None,
            default_timeout=7.5,
        )
        asyncio.run(flush_fn(None, False))
        assert queue.idle_timeout == 7.5

    def test_flush_raises_timeout_when_queue_does_not_drain(self) -> None:
        """flush() raises TimeoutError when queue doesn't drain in time."""
        queue = RecordingQueue(drain_success=False)
        console = RecordingConsole()
        flush_fn = create_flush(
            queue=queue,
            console=console,
            graylog=None,
            ring_buffer=None,
            default_timeout=5.0,
        )
        with pytest.raises(TimeoutError, match="Queue did not drain within 5.0s"):
            asyncio.run(flush_fn(5.0, False))

    def test_flush_flushes_console(self) -> None:
        """flush() calls flush on the console adapter."""
        console = RecordingConsole()
        flush_fn = create_flush(
            queue=None,
            console=console,
            graylog=None,
            ring_buffer=None,
        )
        asyncio.run(flush_fn(None, False))
        assert console.flushed is True

    def test_flush_flushes_graylog(self) -> None:
        """flush() calls flush on the graylog adapter."""
        graylog = RecordingGraylog()
        flush_fn = create_flush(
            queue=None,
            console=None,
            graylog=graylog,
            ring_buffer=None,
        )
        asyncio.run(flush_fn(None, False))
        assert graylog.flushed is True

    def test_flush_skips_ring_buffer_by_default(self) -> None:
        """flush() does not flush ring buffer when flush_ring_buffer=False."""
        ring = RecordingRingBuffer()
        flush_fn = create_flush(
            queue=None,
            console=None,
            graylog=None,
            ring_buffer=ring,
        )
        asyncio.run(flush_fn(None, False))
        assert ring.flushed is False

    def test_flush_flushes_ring_buffer_when_requested(self) -> None:
        """flush() flushes ring buffer when flush_ring_buffer=True."""
        ring = RecordingRingBuffer()
        flush_fn = create_flush(
            queue=None,
            console=None,
            graylog=None,
            ring_buffer=ring,
        )
        asyncio.run(flush_fn(None, True))
        assert ring.flushed is True

    def test_flush_does_not_stop_queue(self) -> None:
        """flush() waits for queue but does not stop it."""
        queue = RecordingQueue()
        console = RecordingConsole()
        flush_fn = create_flush(
            queue=queue,
            console=console,
            graylog=None,
            ring_buffer=None,
        )
        asyncio.run(flush_fn(None, False))
        assert queue.idle_called is True
        assert queue.stopped is False

    def test_flush_order_queue_then_console_then_graylog_then_ring(self) -> None:
        """flush() processes in order: queue -> console -> graylog -> ring buffer."""
        call_order: list[str] = []

        class OrderTrackingQueue(RecordingQueue):
            def wait_until_idle(self, timeout: float | None = None) -> bool:
                call_order.append("queue")
                return True

        class OrderTrackingConsole(RecordingConsole):
            def flush(self) -> None:
                call_order.append("console")

        class OrderTrackingGraylog(RecordingGraylog):
            async def flush(self) -> None:
                call_order.append("graylog")

        class OrderTrackingRing(RecordingRingBuffer):
            def flush(self) -> None:
                call_order.append("ring")

        flush_fn = create_flush(
            queue=OrderTrackingQueue(),
            console=OrderTrackingConsole(),
            graylog=OrderTrackingGraylog(),
            ring_buffer=OrderTrackingRing(),
        )
        asyncio.run(flush_fn(None, True))
        assert call_order == ["queue", "console", "graylog", "ring"]

    def test_flush_handles_all_none_adapters(self) -> None:
        """flush() succeeds when all adapters are None."""
        flush_fn = create_flush(
            queue=None,
            console=None,
            graylog=None,
            ring_buffer=None,
        )
        # Should not raise
        asyncio.run(flush_fn(None, False))


class TestFlushRuntimeIntegration:
    """Tests for flush() and flush_async() runtime API functions."""

    def test_flush_keeps_runtime_active(self) -> None:
        """flush_async() does not clear the runtime singleton."""
        queue = RecordingQueue()
        console = RecordingConsole()

        flush_fn = create_flush(
            queue=queue,
            console=console,
            graylog=None,
            ring_buffer=None,
        )

        runtime = _make_runtime(queue=queue, flush_async_fn=flush_fn)
        set_runtime(runtime)

        assert is_initialised() is True

        asyncio.run(runtime.flush_async(5.0, False))

        # Runtime should still be active
        assert is_initialised() is True

    def test_flush_from_event_loop_guard(self) -> None:
        """Sync flush() from async context raises RuntimeError."""
        import lib_log_rich

        runtime = _make_runtime()
        set_runtime(runtime)

        async def attempt_flush_in_loop() -> None:
            lib_log_rich.flush()

        with pytest.raises(RuntimeError, match="cannot run inside an active event loop"):
            asyncio.run(attempt_flush_in_loop())

    def test_flush_async_works_in_async_context(self) -> None:
        """flush_async() can be awaited from an async context."""
        queue = RecordingQueue()
        console = RecordingConsole()

        flush_fn = create_flush(
            queue=queue,
            console=console,
            graylog=None,
            ring_buffer=None,
        )

        runtime = _make_runtime(queue=queue, flush_async_fn=flush_fn)
        set_runtime(runtime)

        async def run_flush() -> None:
            await runtime.flush_async(5.0, False)

        asyncio.run(run_flush())
        assert queue.idle_called is True
        assert console.flushed is True


class TestFlushWithRealRingBuffer:
    """Tests using a real RingBuffer to verify flush behavior."""

    def test_flush_with_real_ring_buffer(self) -> None:
        """flush() can flush a real RingBuffer instance."""
        ring = RingBuffer(max_events=10)
        flush_fn = create_flush(
            queue=None,
            console=None,
            graylog=None,
            ring_buffer=ring,
        )
        # Should not raise - RingBuffer.flush() is a no-op when no checkpoint path
        asyncio.run(flush_fn(None, True))
