from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from lib_log_rich.adapters.queue import QueueAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

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
