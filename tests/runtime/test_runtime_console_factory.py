"""Integration tests covering custom console adapter factories."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.runtime import ConsoleAppearance, RuntimeConfig, bind, getLogger, init, shutdown
from tests.os_markers import OS_AGNOSTIC

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
