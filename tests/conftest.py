"""Shared pytest fixtures supporting the logging backbone test suite.

The fixtures model deterministic clocks, ID providers, and console renderers so
that unit, contract, and integration tests can run repeatably while exercising
multiprocessing-aware code paths. Definitions live in ``conftest.py`` to keep
individual tests laser-focused on behavioral assertions instead of setup
plumbing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from rich.theme import Theme

from lib_log_rich.domain.context import ContextBinder, LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.ring_buffer import RingBuffer


def _aware(ts: datetime) -> datetime:
    """Normalize naive datetimes to UTC-aware inputs.

    >>> _aware(datetime(2025, 9, 23, 7, 45, 0)).tzinfo is timezone.utc
    True
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


@pytest.fixture
def tmp_ring_buffer(tmp_path: Path) -> Iterator[RingBuffer]:
    """Provide a temporary ring buffer bound to an on-disk checkpoint file.

    The buffer mirrors the production setup where the last ``n`` events live in
    memory while optional checkpoints persist to disk. Tests can inspect the
    buffer to ensure dump adapters access the correct sequence of events.
    """
    buffer = RingBuffer(max_events=128, checkpoint_path=tmp_path / "checkpoint.jsonl")
    yield buffer
    buffer.clear()


@pytest.fixture
def fake_clock() -> Callable[[], datetime]:
    """Return a monotonic clock advancing 10 milliseconds per invocation.

    >>> clock = fake_clock()  # doctest: +SKIP
    >>> first, second = clock(), clock()  # doctest: +SKIP
    >>> (second - first) == timedelta(milliseconds=10)  # doctest: +SKIP
    True
    """
    current = _aware(datetime(2025, 9, 23, 7, 45, 0))

    def _tick() -> datetime:
        nonlocal current
        result = current
        current = current + timedelta(milliseconds=10)
        return result

    return _tick


@pytest.fixture
def fake_id_provider() -> Callable[[], str]:
    """Yield a deterministic ID provider incrementing an integer suffix.

    >>> provider = fake_id_provider()  # doctest: +SKIP
    >>> provider(), provider()  # doctest: +SKIP
    ('evt-000001', 'evt-000002')
    """
    counter = 0

    def _next() -> str:
        nonlocal counter
        counter += 1
        return f"evt-{counter:06d}"

    return _next


@pytest.fixture
def context_binder() -> ContextBinder:
    """Return a fresh context binder used to assert propagation semantics."""
    return ContextBinder()


@pytest.fixture
def bound_context(context_binder: ContextBinder) -> Iterator[LogContext]:
    """Bind a canonical test context and clean up afterwards."""
    with context_binder.bind(
        service="svc",
        environment="test",
        job_id="job-001",
        request_id="req-001",
        user_id="42",
        user_name="tester",
        hostname="unittest",
        process_id=4321,
    ) as ctx:
        yield ctx


@pytest.fixture
def event_factory(fake_clock: Callable[[], datetime], fake_id_provider: Callable[[], str]) -> Callable[[dict[str, Any] | None], LogEvent]:
    """Produce ``LogEvent`` instances with overridable payloads.

    The factory hides timestamp and identifier generation so tests can focus on
    the values under scrutiny. Passing ``overrides`` mutates the default field
    set to support edge-case scenarios.
    """

    def _factory(overrides: dict[str, Any] | None = None) -> LogEvent:
        payload: dict[str, Any] = {
            "event_id": fake_id_provider(),
            "timestamp": fake_clock(),
            "logger_name": "tests.unit",
            "level": LogLevel.INFO,
            "message": "example",
            "context": LogContext(
                service="svc",
                environment="test",
                job_id="job-001",
                user_name="tester",
                hostname="unittest",
                process_id=4321,
                process_id_chain=(4320, 4321),
            ),
            "extra": {"key": "value"},
        }
        if overrides:
            payload.update(overrides)
        return LogEvent(**payload)

    return _factory


@pytest.fixture
def record_console() -> Iterator[Console]:
    """Capture Rich console output with a deterministic theme for snapshot tests."""
    theme = Theme(
        {
            "info": "cyan",
            "warning": "yellow",
            "error": "red",
            "critical": "bold red",
        }
    )
    console = Console(color_system="truecolor", theme=theme, record=True)
    yield console


@pytest.fixture
def temp_directory(tmp_path: Path) -> Path:
    """Alias fixture offering a pathlib path for readability in tests."""
    return tmp_path


@contextmanager
def restore_context(binder: ContextBinder) -> Iterator[None]:
    """Snapshot and restore the current context.

    Used in tests that mutate context variables temporarily.
    """
    snapshot = binder.serialize()
    try:
        yield
    finally:
        binder.deserialize(snapshot)
