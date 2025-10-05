from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pytest

from lib_log_rich.adapters.queue import QueueAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    import pytest

pytestmark = [OS_AGNOSTIC]

Worker = Callable[[LogEvent], None]


def build_event(index: int) -> LogEvent:
    return LogEvent(
        event_id=f"evt-{index}",
        timestamp=datetime(2025, 9, 23, 12, index, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message=f"message-{index}",
        context=LogContext(service="svc", environment="test", job_id="job"),
    )


def start_queue(worker: Worker) -> QueueAdapter:
    adapter = QueueAdapter(worker=worker)
    adapter.start()
    return adapter


def test_queue_processes_events_in_order() -> None:
    processed: list[str] = []
    lock = threading.Lock()

    def worker(event: LogEvent) -> None:
        with lock:
            processed.append(event.event_id)

    adapter = start_queue(worker)
    for index in range(5):
        adapter.put(build_event(index))
    adapter.stop()
    assert processed == [f"evt-{index}" for index in range(5)]


def test_queue_drop_policy_invokes_callback() -> None:
    dropped: list[str] = []

    adapter = QueueAdapter(worker=None, maxsize=1, drop_policy="drop", on_drop=lambda event: dropped.append(event.event_id))

    assert adapter.put(build_event(0)) is True
    assert adapter.put(build_event(1)) is False
    assert dropped == ["evt-1"]


def test_queue_block_policy_timeout_triggers_drop() -> None:
    dropped: list[str] = []

    adapter = QueueAdapter(
        worker=None,
        maxsize=1,
        drop_policy="block",
        on_drop=lambda event: dropped.append(event.event_id),
        timeout=0.01,
    )

    assert adapter.put(build_event(0)) is True
    assert adapter.put(build_event(1)) is False
    assert dropped == ["evt-1"]


def test_queue_stop_drain_flushes_pending_events() -> None:
    processed: list[str] = []
    lock = threading.Lock()

    def worker(event: LogEvent) -> None:
        with lock:
            processed.append(event.event_id)

    adapter = start_queue(worker)
    for index in range(3):
        adapter.put(build_event(index))
    adapter.stop(drain=True)
    assert len(processed) == 3


def test_queue_stop_without_drain_resets_unfinished_tasks() -> None:
    """Stopping without drain drops queued events and shuts down cleanly."""

    processed: list[str] = []
    first_event_started = threading.Event()
    release_first_event = threading.Event()

    def worker(event: LogEvent) -> None:
        if event.event_id == "evt-0":
            first_event_started.set()
            if not release_first_event.wait(timeout=1.0):  # pragma: no cover - defensive
                raise AssertionError("Worker gate was not released")
        processed.append(event.event_id)

    adapter = start_queue(worker)
    adapter.put(build_event(0))
    assert first_event_started.wait(timeout=1.0)

    # Queue additional events that should be dropped once stop(drain=False) executes.
    adapter.put(build_event(1))
    adapter.put(build_event(2))

    stop_started = threading.Event()
    stop_finished = threading.Event()

    def invoke_stop() -> None:
        stop_started.set()
        adapter.stop(drain=False)
        stop_finished.set()

    stopper = threading.Thread(target=invoke_stop)
    stopper.start()
    assert stop_started.wait(timeout=1.0)

    release_first_event.set()
    stopper.join(timeout=2.0)
    assert stop_finished.wait(timeout=0.1)

    assert processed == ["evt-0"]

    replayed: list[str] = []

    def replay_worker(event: LogEvent) -> None:
        replayed.append(event.event_id)

    adapter.set_worker(replay_worker)
    adapter.start()
    adapter.stop(drain=True)
    assert replayed == []

    adapter.start()
    adapter.put(build_event(9))
    adapter.stop(drain=True)
    assert replayed == ["evt-9"]


def test_queue_stop_respects_timeout() -> None:
    gate = threading.Event()
    started = threading.Event()

    def worker(event: LogEvent) -> None:  # noqa: ARG001 - timing only
        started.set()
        gate.wait()

    adapter = start_queue(worker)
    adapter.put(build_event(0))
    assert started.wait(timeout=1.0)

    begin = time.perf_counter()
    with pytest.raises(RuntimeError):
        adapter.stop(drain=True, timeout=0.05)
    elapsed = time.perf_counter() - begin
    assert elapsed < 0.5

    gate.set()
    adapter.stop()


def test_queue_stop_without_drain_invokes_drop_callback() -> None:
    dropped: list[str] = []
    first_started = threading.Event()
    release_first = threading.Event()

    def worker(event: LogEvent) -> None:
        if event.event_id == "evt-0":
            first_started.set()
            release_first.wait(timeout=1.0)

    adapter = QueueAdapter(worker=worker, on_drop=lambda event: dropped.append(event.event_id))
    adapter.start()

    adapter.put(build_event(0))
    assert first_started.wait(timeout=1.0)

    adapter.put(build_event(1))
    adapter.put(build_event(2))

    adapter.stop(drain=False)
    release_first.set()
    adapter.stop()

    assert set(dropped) == {"evt-1", "evt-2"}


def test_queue_reports_degraded_drop_mode_after_worker_failure() -> None:
    diagnostics: list[tuple[str, dict[str, object]]] = []
    worker_error = threading.Event()
    degraded_mode = threading.Event()

    def diagnostic(name: str, payload: dict[str, object]) -> None:
        diagnostics.append((name, payload))
        if name == "queue_worker_error":
            worker_error.set()
        if name == "queue_degraded_drop_mode":
            degraded_mode.set()

    def failing_worker(event: LogEvent) -> None:
        raise RuntimeError(f"boom {event.event_id}")

    adapter = QueueAdapter(
        worker=failing_worker,
        maxsize=1,
        drop_policy="block",
        timeout=0.05,
        diagnostic=diagnostic,
    )
    adapter.start()

    assert adapter.put(build_event(0)) is True
    assert worker_error.wait(timeout=1.0)

    assert adapter.put(build_event(1)) is True
    assert degraded_mode.wait(timeout=1.0)

    adapter.set_worker(lambda event: None)
    adapter.stop(drain=True)

    assert any(name == "queue_degraded_drop_mode" for name, _ in diagnostics)


def test_queue_stop_raises_when_worker_cannot_finish() -> None:
    gate = threading.Event()
    release = threading.Event()
    diagnostics: list[tuple[str, dict[str, object]]] = []

    def worker(event: LogEvent) -> None:  # noqa: ARG001 - blocks intentionally
        gate.set()
        release.wait()

    adapter = QueueAdapter(
        worker=worker,
        timeout=0.01,
        stop_timeout=0.05,
        diagnostic=lambda name, payload: diagnostics.append((name, dict(payload))),
    )
    adapter.start()
    adapter.put(build_event(0))

    assert gate.wait(timeout=1.0)

    with pytest.raises(RuntimeError, match="Queue worker failed to stop"):
        adapter.stop(drain=True, timeout=0.05)

    assert any(name == "queue_shutdown_timeout" for name, _ in diagnostics)

    release.set()
    adapter.stop()


def test_queue_worker_exception_reports_and_continues() -> None:
    processed: list[str] = []
    diagnostics: list[tuple[str, dict[str, object]]] = []
    failed_once = False

    def worker(event: LogEvent) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("boom")
        processed.append(event.event_id)

    adapter = QueueAdapter(worker=worker, diagnostic=lambda name, payload: diagnostics.append((name, payload)))
    adapter.start()
    adapter.put(build_event(0))
    adapter.put(build_event(1))
    adapter.put(build_event(2))
    assert adapter.wait_until_idle(timeout=1.0) is True

    assert adapter.worker_failed is True

    adapter.stop(drain=True)
    assert adapter.worker_failed is False
    assert processed == ["evt-1", "evt-2"]
    worker_errors = [payload for name, payload in diagnostics if name == "queue_worker_error"]
    assert worker_errors
    assert worker_errors[0].get("event_id") == "evt-0"


def test_queue_drop_callback_failure_reports(caplog: "pytest.LogCaptureFixture") -> None:
    diagnostics: list[tuple[str, dict[str, object]]] = []

    def broken_drop(_: LogEvent) -> None:
        raise RuntimeError("drop failure")

    adapter = QueueAdapter(
        worker=None,
        maxsize=1,
        drop_policy="drop",
        on_drop=broken_drop,
        diagnostic=lambda name, payload: diagnostics.append((name, payload)),
    )

    assert adapter.put(build_event(0)) is True
    with caplog.at_level(logging.ERROR):
        accepted = adapter.put(build_event(1))
    assert accepted is False
    assert any(name == "queue_drop_callback_error" for name, _ in diagnostics)
    assert any("Queue drop handler raised an exception" in message for message in caplog.messages)


def test_queue_worker_failed_resets_on_clean_stop() -> None:
    failed_once = False

    def worker(event: LogEvent) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("boom")

    adapter = QueueAdapter(worker=worker)
    adapter.start()

    adapter.put(build_event(0))
    assert adapter.wait_until_idle(timeout=1.0) is True
    assert adapter.worker_failed is True

    adapter.put(build_event(1))
    assert adapter.wait_until_idle(timeout=1.0) is True
    assert adapter.worker_failed is True

    adapter.stop(drain=True)
    assert adapter.worker_failed is False

    adapter.start()
    assert adapter.worker_failed is False
    adapter.stop()


def test_queue_worker_failure_auto_resets_after_success() -> None:
    processed: list[str] = []
    failed_once = False

    def worker(event: LogEvent) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("boom")
        processed.append(event.event_id)

    adapter = QueueAdapter(worker=worker, failure_reset_after=0.0)
    adapter.start()

    adapter.put(build_event(0))
    assert adapter.wait_until_idle(timeout=1.0) is True
    assert adapter.worker_failed is True

    adapter.put(build_event(1))
    assert adapter.wait_until_idle(timeout=1.0) is True
    assert processed == ["evt-1"]
    assert adapter.worker_failed is False

    adapter.stop(drain=True)


def test_queue_block_policy_degrades_when_worker_failed() -> None:
    adapter = QueueAdapter(worker=None, maxsize=1, drop_policy="block", timeout=1.0)

    adapter.put(build_event(0))

    adapter._worker_failed = True  # type: ignore[attr-defined]

    begin = time.perf_counter()
    accepted = adapter.put(build_event(1))
    elapsed = time.perf_counter() - begin

    assert accepted is False
    assert adapter._degraded_drop_mode is True  # type: ignore[attr-defined]
    assert elapsed < 0.1

    adapter.stop()
