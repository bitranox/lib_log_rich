"""Integration tests covering custom console adapter factories."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, cast

import pytest

from lib_log_rich.adapters.console.queue_console import AsyncQueueConsoleAdapter
from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain.enums import ConsoleStream
from lib_log_rich.runtime import ConsoleAppearance, RuntimeConfig, bind, getLogger, init, is_initialised, shutdown, shutdown_async
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from lib_log_rich.domain.events import LogEvent

pytestmark = [OS_AGNOSTIC]


@pytest.fixture(autouse=True)
def _cleanup_runtime() -> Iterator[None]:
    """Ensure each test tears down the runtime even on failure."""
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            shutdown()


_ = _cleanup_runtime


def test_console_adapter_factory_substitutes_console() -> None:
    """`RuntimeConfig.console_adapter_factory` should supply the console adapter."""
    appearances: list[ConsoleAppearance] = []
    events: list[tuple[str, bool]] = []

    class RecordingConsole(ConsolePort):
        def emit(self, event: LogEvent, *, colorize: bool) -> None:  # type: ignore[override]
            events.append((event.message, colorize))

        def flush(self) -> None:
            pass

    def console_factory(appearance: ConsoleAppearance) -> RecordingConsole:
        appearances.append(appearance)
        return RecordingConsole()

    init(
        RuntimeConfig(
            service="svc",
            environment="env",
            queue_enabled=False,
            enable_graylog=False,
            console_adapter_factory=console_factory,
        )
    )

    with bind(job_id="job", request_id="req"):
        getLogger("tests.console-factory").info("hello factory")

    assert len(appearances) == 1, "factory should be invoked exactly once"
    assert events == [("hello factory", True)]


class _ConsoleWithoutFlush:
    """Has emit() but not the flush() that ConsolePort also requires."""

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        del event, colorize


def _console_without_flush(_appearance: ConsoleAppearance) -> _ConsoleWithoutFlush:
    return _ConsoleWithoutFlush()


def _config_with_factory(factory: object) -> RuntimeConfig:
    # The factory deliberately breaks the ConsolePort contract, so its type cannot match.
    typed = cast("Callable[[ConsoleAppearance], ConsolePort]", factory)
    return RuntimeConfig(service="svc", environment="env", queue_enabled=False, console_adapter_factory=typed)


def test_init_refuses_a_factory_console_that_lacks_flush() -> None:
    with pytest.raises(TypeError, match=r"_ConsoleWithoutFlush.*flush"):
        init(_config_with_factory(_console_without_flush))

    assert is_initialised() is False


def test_a_refused_factory_console_leaves_init_usable() -> None:
    with contextlib.suppress(TypeError):
        init(_config_with_factory(_console_without_flush))

    init(RuntimeConfig(service="svc", environment="env", queue_enabled=False, console_stream=ConsoleStream.NONE))

    assert is_initialised() is True


@pytest.mark.asyncio
async def test_an_async_queue_console_wakes_its_consumer_when_the_runtime_queue_worker_emits() -> None:
    """With the runtime queue on, the console emit runs on the queue worker thread, not the loop's."""
    lines: asyncio.Queue[str] = asyncio.Queue()

    def console_factory(appearance: ConsoleAppearance) -> AsyncQueueConsoleAdapter:
        return AsyncQueueConsoleAdapter(lines, no_color=appearance.no_color)

    init(RuntimeConfig(service="svc", environment="env", queue_enabled=True, console_adapter_factory=console_factory))
    started = time.monotonic()
    getLogger("tests.async").warning("streamed from the worker")
    # Awaiting only the queue: nothing but the adapter's hand-off can wake the loop.
    line = await asyncio.wait_for(lines.get(), timeout=2)
    elapsed = time.monotonic() - started
    await shutdown_async()

    assert "streamed from the worker" in line
    assert elapsed < 1
