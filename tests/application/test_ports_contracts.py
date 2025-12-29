from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from io import StringIO
from threading import Event
from typing import Callable

import pytest
from rich.console import Console

from lib_log_rich.adapters.console import RichConsoleAdapter
from lib_log_rich.adapters.dump import DumpAdapter
from lib_log_rich.adapters.graylog import GraylogAdapter
from lib_log_rich.adapters.queue import QueueAdapter
from lib_log_rich.adapters.rate_limiter import SlidingWindowRateLimiter
from lib_log_rich.adapters.scrubber import RegexScrubber
from lib_log_rich.application.ports.time import UnitOfWork
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.dump_filter import DumpFilter
from lib_log_rich.domain.enums import QueuePolicy
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.runtime._factories import SystemClock, SystemIdentityProvider, UuidProvider
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


@pytest.fixture
def example_event(bound_context: LogContext) -> LogEvent:
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=bound_context,
    )


def test_console_port_renders_event_with_rich_adapter(example_event: LogEvent) -> None:
    console = Console(file=StringIO(), record=True)
    # Use "full" preset to include logger_name in output
    adapter = RichConsoleAdapter(console=console, format_preset="full")
    adapter.emit(example_event, colorize=False)

    output = console.export_text()
    assert example_event.message in output
    assert example_event.logger_name in output


def test_dump_port_returns_payload_for_text_dump(example_event: LogEvent) -> None:
    adapter = DumpAdapter()
    payload = adapter.dump(
        [example_event],
        dump_format=DumpFormat.TEXT,
        filters=DumpFilter(),
        colorize=False,
    )

    assert "evt-1" in payload
    assert example_event.logger_name in payload


def test_graylog_port_flush_is_safe_when_disabled(example_event: LogEvent) -> None:
    adapter = GraylogAdapter(host="localhost", port=12201, enabled=False)
    adapter.emit(example_event)

    assert adapter._socket is None  # type: ignore[attr-defined]
    asyncio.run(adapter.flush())
    assert adapter._socket is None  # type: ignore[attr-defined]


def test_queue_port_processes_event_and_drains(example_event: LogEvent) -> None:
    processed: list[str] = []
    processed_event = Event()

    def worker(event: LogEvent) -> None:
        processed.append(event.event_id)
        processed_event.set()

    queue = QueueAdapter(worker=worker, maxsize=4, drop_policy=QueuePolicy.BLOCK, timeout=0.1, stop_timeout=0.2)
    queue.start()
    try:
        assert queue.put(example_event) is True
        assert processed_event.wait(timeout=1.0), "queue worker did not process event"
        assert processed == ["evt-1"]
    finally:
        queue.stop()

    debug = queue.debug()
    assert debug.queue_empty()
    assert debug.queue_size() == 0


def test_rate_limiter_blocks_after_capacity(example_event: LogEvent) -> None:
    limiter = SlidingWindowRateLimiter(max_events=1, interval=timedelta(seconds=30))
    assert limiter.allow(example_event) is True

    second = example_event.replace(event_id="evt-2", timestamp=example_event.timestamp + timedelta(seconds=1))
    assert limiter.allow(second) is False


def test_scrubber_redacts_matching_payloads(bound_context: LogContext) -> None:
    event = LogEvent(
        event_id="evt-3",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests.scrubber",
        level=LogLevel.WARNING,
        message="credentials leaked",
        context=bound_context.replace(extra={"token": "secret-123"}),
        extra={"password": "secret-456"},
    )
    scrubber = RegexScrubber(patterns={"token": "secret", "password": "secret"}, replacement="***")
    scrubbed = scrubber.scrub(event)

    assert scrubbed.extra["password"] == "***"
    assert scrubbed.context.extra["token"] == "***"


def test_system_identity_provider_resolves_current_process() -> None:
    provider = SystemIdentityProvider()
    identity = provider.resolve_identity()

    assert identity.process_id >= 0
    assert identity.hostname is None or identity.hostname.strip() != ""


def test_clock_port_returns_timezone_aware_utc() -> None:
    clock = SystemClock()
    now = clock.now()

    assert now.tzinfo is timezone.utc


def test_id_provider_generates_unique_tokens() -> None:
    provider = UuidProvider()
    first = provider()
    second = provider()

    assert isinstance(first, str) and isinstance(second, str)
    assert first != second
    assert len(first) == 32


class _ImmediateUnitOfWork(UnitOfWork[str]):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, fn: Callable[[], str]) -> str:
        self.calls.append("run")
        return fn()


def test_unit_of_work_runs_callable_and_returns_value() -> None:
    uow = _ImmediateUnitOfWork()

    def compute() -> str:
        return "done"

    result = uow.run(compute)
    assert result == "done"
    assert uow.calls == ["run"]
