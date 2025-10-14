from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from queue import Queue

import pytest
from rich.console import Console

from lib_log_rich.adapters.console import queue_console as queue_console_module
from lib_log_rich.adapters.console.queue_console import (
    AsyncQueueConsoleAdapter,
    QueueConsoleAdapter,
)
from lib_log_rich.domain.context import LogContext
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
    adapter = QueueConsoleAdapter(queue, export_style="ansi")
    adapter.emit(_make_event(), colorize=True)
    segment = queue.get(timeout=1)
    assert "hello" in segment


def test_queue_console_serves_html_when_the_listener_requests_markup() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style="html")
    adapter.emit(_make_event(), colorize=False)
    snippet = queue.get(timeout=1)
    assert "<pre" in snippet


def test_queue_console_wraps_long_lines_when_the_stage_is_narrow() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style="ansi", console_width=24)
    adapter.emit(_make_event(message="a meadow full of luminous fireflies"), colorize=False)
    segments: list[str] = []
    while not queue.empty():
        segments.append(queue.get(timeout=1))
    assert len(segments) > 1


def test_queue_console_keeps_text_plain_when_colour_is_banned() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style="ansi", no_color=True)
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
    adapter = QueueConsoleAdapter(queue, export_style="ansi")
    adapter.emit(_make_event(), colorize=False)
    assert queue.empty()


@pytest.mark.asyncio
async def test_async_queue_console_offers_a_segment_without_delay() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue()
    adapter = AsyncQueueConsoleAdapter(queue, export_style="ansi")
    adapter.emit(_make_event(), colorize=True)
    segment = await asyncio.wait_for(queue.get(), timeout=1)
    assert "hello" in segment


@pytest.mark.asyncio
async def test_async_queue_console_calls_the_drop_hook_when_space_runs_out() -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    drops: list[str] = []
    adapter = AsyncQueueConsoleAdapter(queue, export_style="ansi", on_drop=drops.append)
    await queue.put("occupied")
    adapter.emit(_make_event(), colorize=True)
    assert drops
    while not queue.empty():
        queue.get_nowait()


@pytest.mark.asyncio
async def test_async_queue_console_warns_loudly_when_no_drop_hook_is_provided(caplog: pytest.LogCaptureFixture) -> None:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    adapter = AsyncQueueConsoleAdapter(queue, export_style="ansi")
    await queue.put("occupied")
    with caplog.at_level(logging.WARNING):
        adapter.emit(_make_event(), colorize=True)
    message = " ".join(record.message for record in caplog.records)
    assert "queue full" in message
    while not queue.empty():
        queue.get_nowait()
