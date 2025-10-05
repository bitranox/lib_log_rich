from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from queue import Queue

import pytest

from lib_log_rich.adapters.console.queue_console import (
    AsyncQueueConsoleAdapter,
    ExportStyle,
    QueueConsoleAdapter,
)
from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent


def _sample_event() -> LogEvent:
    context = LogContext(service="svc", environment="dev", job_id="job-1")
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        logger_name="svc.worker",
        level=LogLevel.INFO,
        message="hello",
        context=context,
    )


@pytest.mark.parametrize("export_style", ["ansi", "html"])
def test_queue_console_adapter_emits_segments(export_style: ExportStyle) -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style=export_style)

    adapter.emit(_sample_event(), colorize=True)

    # The adapter pushes at least one segment per event
    segment = queue.get(timeout=1)
    assert segment  # non-empty payload


def test_queue_console_adapter_respects_multiple_events() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style="ansi")

    for ix in range(3):
        event = _sample_event()
        adapter.emit(event, colorize=bool(ix % 2))

    segments = [queue.get(timeout=1) for _ in range(queue.qsize())]
    joined = "".join(segments)
    assert "svc.worker" in joined
    assert "hello" in joined


@pytest.mark.asyncio
async def test_async_queue_console_adapter_emits_segments() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    adapter = AsyncQueueConsoleAdapter(queue, export_style="ansi")

    adapter.emit(_sample_event(), colorize=True)

    segment = await asyncio.wait_for(queue.get(), timeout=1)
    assert segment


@pytest.mark.asyncio
async def test_async_queue_console_adapter_calls_drop_handler_when_full() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    drops: list[str] = []

    adapter = AsyncQueueConsoleAdapter(queue, export_style="ansi", on_drop=drops.append)

    # Pre-fill queue so subsequent segments overflow and trigger the handler.
    await queue.put("occupied")
    adapter.emit(_sample_event(), colorize=True)

    assert drops  # at least one segment was dropped

    # Clean up queue to avoid pending tasks warnings in pytest-asyncio.
    while not queue.empty():
        queue.get_nowait()
