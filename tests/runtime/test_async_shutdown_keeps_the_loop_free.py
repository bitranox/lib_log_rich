"""``shutdown_async()`` and ``flush_async()`` wait for the queue worker without blocking the loop.

A diagnostic hook may hand each diagnostic to the event loop and wait for it, as a
Textual app does with ``call_from_thread``. The queue worker calls the hook from
its own thread. If the awaitable shutdown or flush then waited for that worker
on the loop's thread, the loop could not serve the hook, the worker could not
finish, and both sat out the whole stop timeout.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from lib_log_rich.adapters import QueueAdapter
from lib_log_rich.application.use_cases.shutdown import create_shutdown
from lib_log_rich.domain import LogContext, LogEvent, LogLevel
from lib_log_rich.runtime import RuntimeConfig, flush_async, getLogger, init, is_initialised, shutdown, shutdown_async
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from lib_log_rich.application.ports import ConsolePort
    from lib_log_rich.runtime.settings import ConsoleAppearance

pytestmark = [OS_AGNOSTIC]

# Long enough that a stalled loop is obvious against the elapsed-time bound below.
_STOP_TIMEOUT = 3.0
_PROMPT = 1.5


class ExplodingConsole:
    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        raise RuntimeError("console exploded")

    def flush(self) -> None:
        return None


def _exploding_console(appearance: ConsoleAppearance) -> ConsolePort:
    return ExplodingConsole()


async def _noop() -> None:
    return None


def _loop_hopping_hook(loop: asyncio.AbstractEventLoop, received: list[str]) -> Callable[[str, Mapping[str, object]], None]:
    """Return a hook that, off the loop thread, waits for the loop to run a step first."""
    loop_thread = threading.get_ident()

    def hook(name: str, payload: Mapping[str, object]) -> None:
        if threading.get_ident() != loop_thread:
            asyncio.run_coroutine_threadsafe(_noop(), loop).result(timeout=_STOP_TIMEOUT * 2)
        received.append(name)

    return hook


def _init_with(hook: Callable[[str, Mapping[str, object]], None]) -> None:
    init(
        RuntimeConfig(
            service="async-shutdown",
            environment="tests",
            queue_enabled=True,
            queue_stop_timeout=_STOP_TIMEOUT,
            enable_graylog=False,
            enable_journald=False,
            enable_eventlog=False,
            console_adapter_factory=_exploding_console,
            diagnostic_hook=hook,
        )
    )


@pytest.fixture(autouse=True)
def no_runtime_left_behind() -> Iterator[None]:
    yield
    if is_initialised():
        shutdown()


@pytest.mark.asyncio
async def test_shutdown_async_serves_a_hook_that_waits_for_the_loop() -> None:
    received: list[str] = []
    _init_with(_loop_hopping_hook(asyncio.get_running_loop(), received))

    getLogger("tests.async_shutdown").warning("hello")
    started = time.monotonic()
    await shutdown_async()
    elapsed = time.monotonic() - started

    assert elapsed < _PROMPT
    assert "adapter_error" in received


@pytest.mark.asyncio
async def test_flush_async_serves_a_hook_that_waits_for_the_loop() -> None:
    received: list[str] = []
    _init_with(_loop_hopping_hook(asyncio.get_running_loop(), received))

    getLogger("tests.async_flush").warning("hello")
    started = time.monotonic()
    await flush_async(timeout=_STOP_TIMEOUT)
    elapsed = time.monotonic() - started

    assert elapsed < _PROMPT
    assert "adapter_error" in received
    await shutdown_async()


@pytest.mark.asyncio
async def test_the_shutdown_use_case_stops_its_queue_off_the_loop() -> None:
    """The use case on its own, without the runtime stopping the queue before it."""
    loop = asyncio.get_running_loop()
    handled: list[str] = []

    def worker(event: LogEvent) -> None:
        asyncio.run_coroutine_threadsafe(_noop(), loop).result(timeout=_STOP_TIMEOUT * 2)
        handled.append(event.event_id)

    queue = QueueAdapter(worker=worker, stop_timeout=_STOP_TIMEOUT)
    queue.start()
    queue.put(
        LogEvent(
            event_id="evt-0",
            timestamp=datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc),
            logger_name="tests",
            level=LogLevel.INFO,
            message="hello",
            context=LogContext(service="svc", environment="test", job_id="job"),
        )
    )
    shutdown_use_case = create_shutdown(queue=queue, console=None, graylog=None, ring_buffer=None)

    started = time.monotonic()
    await shutdown_use_case()
    elapsed = time.monotonic() - started

    assert elapsed < _PROMPT
    assert handled == ["evt-0"]
