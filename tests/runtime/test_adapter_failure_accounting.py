"""An adapter failure is counted the same way with the queue on as without it.

Inline, a raising adapter is recorded as an ``adapter_error`` drop and reported
by one aggregate ``adapter_error`` diagnostic naming the failed adapters. The
queue worker used to run the bare fan-out and discard its list of failed
adapters, so with the queue on neither happened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from lib_log_rich.domain import DropReason
from lib_log_rich.runtime import RuntimeConfig, flush, getLogger, init, is_initialised, severity_snapshot, shutdown
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from lib_log_rich.application.ports import ConsolePort
    from lib_log_rich.domain import LogEvent
    from lib_log_rich.runtime.settings import ConsoleAppearance

pytestmark = [OS_AGNOSTIC]


class ExplodingConsole:
    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        raise RuntimeError("console exploded")

    def flush(self) -> None:
        return None


def _exploding_console(appearance: ConsoleAppearance) -> ConsolePort:
    return ExplodingConsole()


@pytest.fixture(autouse=True)
def no_runtime_left_behind() -> Iterator[None]:
    yield
    if is_initialised():
        shutdown()


@pytest.mark.parametrize("queue_enabled", [True, False], ids=["queue", "inline"])
def test_a_raising_adapter_is_counted_as_one_adapter_error_drop(queue_enabled: bool) -> None:
    diagnostics: list[tuple[str, Mapping[str, object]]] = []
    init(
        RuntimeConfig(
            service="accounting",
            environment="tests",
            queue_enabled=queue_enabled,
            enable_graylog=False,
            enable_journald=False,
            enable_eventlog=False,
            console_adapter_factory=_exploding_console,
            diagnostic_hook=lambda name, payload: diagnostics.append((name, payload)),
        )
    )

    getLogger("tests.accounting").warning("hello")
    flush()

    drops_by_reason = severity_snapshot().drops_by_reason
    assert drops_by_reason[DropReason.ADAPTER_ERROR] == 1
    # A 6.x caller indexes with the plain string; the member hashes as its value.
    assert cast("Mapping[str, int]", drops_by_reason)["adapter_error"] == 1
    aggregate = [payload for name, payload in diagnostics if name == "adapter_error" and "adapters" in payload]
    assert [payload["adapters"] for payload in aggregate] == [["ExplodingConsole"]]


class QuietConsole:
    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        return None

    def flush(self) -> None:
        return None


def test_a_queued_event_that_reaches_every_adapter_sends_no_emitted_diagnostic() -> None:
    # DIAGNOSTIC.md documents ``emitted`` for the inline path only; a hook that
    # hands each diagnostic to a UI thread would deadlock against a shutdown
    # that waits for the worker if every queued event produced one.
    diagnostics: list[str] = []
    init(
        RuntimeConfig(
            service="accounting",
            environment="tests",
            queue_enabled=True,
            enable_graylog=False,
            enable_journald=False,
            enable_eventlog=False,
            console_adapter_factory=lambda appearance: QuietConsole(),
            diagnostic_hook=lambda name, payload: diagnostics.append(name),
        )
    )

    getLogger("tests.accounting").warning("hello")
    flush()

    assert "queued" in diagnostics
    assert "emitted" not in diagnostics
    assert severity_snapshot().drops_by_reason[DropReason.ADAPTER_ERROR] == 0
