from __future__ import annotations

import asyncio
import threading
from collections import Counter
from contextlib import suppress
from datetime import datetime, timezone
from typing import Iterable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from lib_log_rich.adapters.console import AsyncQueueConsoleAdapter
from lib_log_rich.adapters.queue import QueueAdapter
from lib_log_rich.domain import LogEvent, LogLevel
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.enums import QueuePolicy
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def _make_event(index: int) -> LogEvent:
    return LogEvent(
        event_id=f"evt-{index}",
        timestamp=datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message=f"message-{index}",
        context=LogContext(service="svc", environment="env", job_id="job"),
    )


def _iter_events(count: int) -> Iterable[LogEvent]:
    for idx in range(count):
        yield _make_event(idx)


@given(st.integers(min_value=10, max_value=200))
@settings(deadline=None)
def test_queue_adapter_drop_policy_handles_burst(count: int) -> None:
    dropped: Counter[str] = Counter()
    diagnostics: list[tuple[str, dict[str, object]]] = []
    adapter = QueueAdapter(
        worker=None,
        maxsize=5,
        drop_policy=QueuePolicy.DROP,
        on_drop=lambda event: dropped.update([event.event_id]),
        diagnostic=lambda name, payload: diagnostics.append((name, dict(payload))),
    )

    accepted = 0
    try:
        for event in _iter_events(count):
            if adapter.put(event):
                accepted += 1
    finally:
        adapter.stop(drain=True)

    assert accepted <= 5
    expected_drops = count - accepted
    assert sum(dropped.values()) == expected_drops
    assert sum(1 for name, _ in diagnostics if name == "queue_dropped") == expected_drops


def test_queue_adapter_block_policy_enters_degraded_drop_mode_under_burst() -> None:
    diagnostics: list[tuple[str, dict[str, object]]] = []
    first_failure = threading.Event()

    def failing_worker(event: LogEvent) -> None:  # noqa: ARG001 - error path only
        first_failure.set()
        raise RuntimeError("boom")

    adapter = QueueAdapter(
        worker=failing_worker,
        maxsize=1,
        drop_policy=QueuePolicy.BLOCK,
        timeout=0.001,
        diagnostic=lambda name, payload: diagnostics.append((name, dict(payload))),
        failure_reset_after=None,
    )

    adapter.start()
    try:
        adapter.put(_make_event(0))
        assert first_failure.wait(timeout=1.0)

        drops = 0
        for event in _iter_events(50):
            if not adapter.put(event):
                drops += 1
    finally:
        adapter.stop(drain=False)
        adapter.stop(drain=True)

    assert drops > 0
    assert any(name == "queue_degraded_drop_mode" for name, _ in diagnostics)
    assert sum(1 for name, _ in diagnostics if name == "queue_dropped") >= drops


@pytest.mark.asyncio
async def test_async_queue_console_adapter_invokes_drop_hook_under_pressure() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    dropped_segments: Counter[str] = Counter()
    adapter = AsyncQueueConsoleAdapter(
        queue=queue,
        export_style="ansi",
        on_drop=lambda segment: dropped_segments.update([segment]),
    )

    gate = asyncio.Event()
    release = asyncio.Event()

    async def consumer() -> None:
        try:
            gate.set()
            await release.wait()
            while True:
                _ = await queue.get()
                await asyncio.sleep(0.01)
                queue.task_done()
        except asyncio.CancelledError:  # pragma: no cover - cleanup path
            pass

    consumer_task = asyncio.create_task(consumer())
    try:
        await gate.wait()
        for event in _iter_events(20):
            adapter.emit(event, colorize=True)
        await asyncio.sleep(0.01)
        assert queue.full()
        release.set()
        for event in _iter_events(20):
            adapter.emit(event, colorize=True)
            await asyncio.sleep(0)
        await asyncio.sleep(0.1)
    finally:
        consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await consumer_task

    assert dropped_segments
    assert sum(dropped_segments.values()) >= 19
