from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from queue import Queue
from typing import cast

import pytest
from rich.console import Console

from lib_log_rich.adapters.console import queue_console as queue_console_module
from lib_log_rich.adapters.console.queue_console import (
    AsyncQueueConsoleAdapter,
    QueueConsoleAdapter,
)
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.enums import ExportStyle
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def _make_event(*, message: str = "hello") -> LogEvent:
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        logger_name="svc.worker",
        level=LogLevel.INFO,
        message=message,
        context=LogContext(service="svc", environment="dev", job_id="job-1"),
    )


def test_queue_console_sends_a_fresh_ansi_segment_into_the_queue() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style=ExportStyle.ANSI)
    adapter.emit(_make_event(), colorize=True)
    segment = queue.get(timeout=1)
    assert "hello" in segment


def test_queue_console_serves_html_when_the_listener_requests_markup() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style=ExportStyle.HTML)
    adapter.emit(_make_event(), colorize=False)
    snippet = queue.get(timeout=1)
    assert "<pre" in snippet


def test_queue_console_wraps_long_lines_when_the_stage_is_narrow() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style=ExportStyle.ANSI, console_width=24)
    adapter.emit(_make_event(message="a meadow full of luminous fireflies"), colorize=False)
    segments: list[str] = []
    while not queue.empty():
        segments.append(queue.get(timeout=1))
    assert len(segments) > 1


def test_queue_console_keeps_text_plain_when_colour_is_banned() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style=ExportStyle.ANSI, no_color=True)
    adapter.emit(_make_event(), colorize=True)
    segment = queue.get(timeout=1)
    assert "\x1b[" not in segment


def test_queue_console_leaves_the_queue_empty_when_the_console_whispers_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    queue: Queue[str] = Queue()

    class SilentConsole(Console):
        def __init__(self) -> None:
            super().__init__(record=True)

        def export_text(self, *, clear: bool, styles: bool) -> str:  # type: ignore[override]
            super().export_text(clear=clear, styles=styles)
            return ""

    def build_silent_console(*args: object, **kwargs: object) -> Console:
        return SilentConsole()

    monkeypatch.setattr(queue_console_module, "Console", build_silent_console)
    adapter = QueueConsoleAdapter(queue, export_style=ExportStyle.ANSI)
    adapter.emit(_make_event(), colorize=False)
    assert queue.empty()


@pytest.mark.parametrize("style", ["html", "ansi"])
def test_queue_console_refuses_an_export_style_given_as_a_plain_string(style: str) -> None:
    # "html" equals ExportStyle.HTML but is not it, so an `is` test would
    # silently render ANSI; the constructor refuses the string instead.
    with pytest.raises(TypeError, match="export_style must be a member of ExportStyle"):
        QueueConsoleAdapter(Queue(), export_style=cast("ExportStyle", style))
    with pytest.raises(TypeError, match="export_style must be a member of ExportStyle"):
        AsyncQueueConsoleAdapter(asyncio.Queue(), export_style=cast("ExportStyle", style))


def test_queue_console_defaults_to_ansi_segments() -> None:
    queue: Queue[str] = Queue()
    QueueConsoleAdapter(queue).emit(_make_event(), colorize=False)
    segment = queue.get(timeout=1)
    assert "<pre" not in segment
    assert "hello" in segment


@pytest.mark.asyncio
async def test_async_queue_console_offers_a_segment_without_delay() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    adapter = AsyncQueueConsoleAdapter(queue, export_style=ExportStyle.ANSI)
    adapter.emit(_make_event(), colorize=True)
    segment = await asyncio.wait_for(queue.get(), timeout=1)
    assert "hello" in segment


@pytest.mark.asyncio
async def test_async_queue_console_calls_the_drop_hook_when_space_runs_out() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    drops: list[str] = []
    adapter = AsyncQueueConsoleAdapter(queue, export_style=ExportStyle.ANSI, on_drop=drops.append)
    await queue.put("occupied")
    adapter.emit(_make_event(), colorize=True)
    assert drops
    while not queue.empty():
        queue.get_nowait()


@pytest.mark.asyncio
async def test_async_queue_console_warns_loudly_when_no_drop_hook_is_provided(caplog: pytest.LogCaptureFixture) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    adapter = AsyncQueueConsoleAdapter(queue, export_style=ExportStyle.ANSI)
    await queue.put("occupied")
    with caplog.at_level(logging.WARNING):
        adapter.emit(_make_event(), colorize=True)
    message = " ".join(record.message for record in caplog.records)
    assert "queue full" in message
    while not queue.empty():
        queue.get_nowait()


async def _emit_from_another_thread(adapter: AsyncQueueConsoleAdapter) -> None:
    # The runtime's queue worker is a plain thread, so with queue_enabled=True every emit
    # arrives off the event loop's thread.
    worker = threading.Thread(target=adapter.emit, args=(_make_event(),), kwargs={"colorize": False})
    worker.start()
    await asyncio.to_thread(worker.join, 5)


async def _await_a_segment_emitted_from_another_thread(adapter: AsyncQueueConsoleAdapter, queue: asyncio.Queue[str]) -> tuple[str, float]:
    # Only the consumer is awaited while the emit lands, so nothing but the adapter's own
    # hand-off can wake the sleeping loop; awaiting the emitting thread would wake it too
    # and hide a missing wake-up.
    emitter = threading.Timer(0.05, adapter.emit, args=(_make_event(),), kwargs={"colorize": False})
    started = time.monotonic()
    emitter.start()
    try:
        segment = await asyncio.wait_for(queue.get(), timeout=2)
    finally:
        emitter.join(5)
    return segment, time.monotonic() - started


@pytest.mark.asyncio
async def test_async_queue_console_wakes_a_waiting_consumer_when_emit_runs_on_another_thread() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    adapter = AsyncQueueConsoleAdapter(queue, export_style=ExportStyle.ANSI)
    segment, elapsed = await _await_a_segment_emitted_from_another_thread(adapter, queue)
    assert "hello" in segment
    assert elapsed < 1


@pytest.mark.asyncio
async def test_async_queue_console_reports_a_drop_for_a_full_queue_when_emit_runs_on_another_thread() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    drops: list[str] = []
    adapter = AsyncQueueConsoleAdapter(queue, export_style=ExportStyle.ANSI, on_drop=drops.append)
    await queue.put("occupied")
    await _emit_from_another_thread(adapter)
    await asyncio.sleep(0)
    assert len(drops) == 1
    assert "hello" in drops[0]
    assert queue.get_nowait() == "occupied"


def test_async_queue_console_counts_a_segment_for_a_closed_loop_as_a_drop() -> None:
    drops: list[str] = []

    async def build() -> tuple[AsyncQueueConsoleAdapter, asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        return AsyncQueueConsoleAdapter(queue, export_style=ExportStyle.ANSI, on_drop=drops.append), queue

    adapter, queue = asyncio.run(build())
    adapter.emit(_make_event(), colorize=False)
    assert len(drops) == 1
    assert queue.empty()


def test_async_queue_console_hands_segments_to_the_loop_it_was_given() -> None:
    loop = asyncio.new_event_loop()
    try:
        queue: asyncio.Queue[str] = asyncio.Queue()
        adapter = AsyncQueueConsoleAdapter(queue, export_style=ExportStyle.ANSI, loop=loop)

        segment, elapsed = loop.run_until_complete(_await_a_segment_emitted_from_another_thread(adapter, queue))
        assert "hello" in segment
        assert elapsed < 1
    finally:
        loop.close()
