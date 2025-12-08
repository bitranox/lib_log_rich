from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from lib_log_rich.adapters._queue_worker import QueueWorkerState
from lib_log_rich.application.use_cases._types import DiagnosticPayload
from lib_log_rich.domain.enums import QueuePolicy
from lib_log_rich.domain.events import LogEvent
from tests.os_markers import OS_AGNOSTIC, POSIX_ONLY

pytestmark = [OS_AGNOSTIC]

EventFactory = Callable[[dict[str, Any] | None], LogEvent]
Diagnostics = list[tuple[str, DiagnosticPayload]]


def make_state(
    *,
    worker: Callable[[LogEvent], None] | None,
    maxsize: int = 1,
    drop_policy: QueuePolicy = QueuePolicy.BLOCK,
    on_drop: Callable[[LogEvent], None] | None = None,
    timeout: float | None = None,
    stop_timeout: float | None = 0.1,
    failure_reset_after: float | None = 0.05,
    diagnostics: Diagnostics | None = None,
) -> QueueWorkerState:
    records = diagnostics if diagnostics is not None else None

    def record(name: str, payload: DiagnosticPayload) -> None:
        assert records is not None
        records.append((name, payload))

    return QueueWorkerState(
        worker=worker,
        maxsize=maxsize,
        drop_policy=drop_policy,
        on_drop=on_drop,
        timeout=timeout,
        stop_timeout=stop_timeout,
        diagnostic=record if diagnostics is not None else None,
        failure_reset_after=failure_reset_after,
    )


def test_queue_worker_start_is_idempotent(event_factory: EventFactory) -> None:
    """Starting twice reuses the existing worker thread."""
    processed = threading.Event()

    def worker(_event: LogEvent) -> None:
        processed.set()

    state = make_state(worker=worker)
    state.start()
    first_thread = state.worker_thread()
    assert first_thread is not None and first_thread.is_alive()

    state.start()
    assert state.worker_thread() is first_thread

    state.put(event_factory(None))
    assert processed.wait(0.5)
    state.stop(timeout=0.5)


def test_queue_worker_stop_waits_without_timeout(event_factory: EventFactory) -> None:
    """Absence of a timeout blocks until the queue drains."""
    processed = threading.Event()

    def worker(_event: LogEvent) -> None:
        processed.set()

    state = make_state(worker=worker, stop_timeout=None)
    state.start()
    state.put(event_factory(None))
    assert processed.wait(0.5)

    state.stop()


def test_queue_worker_drop_handler_exception_emits_diagnostic(event_factory: EventFactory) -> None:
    """Drop handlers that fail surface via diagnostics."""
    diagnostics: Diagnostics = []

    def brittle_drop(_event: LogEvent) -> None:
        raise ValueError("nope")

    state = make_state(worker=None, drop_policy=QueuePolicy.DROP, on_drop=brittle_drop, diagnostics=diagnostics)
    state.put(event_factory(None))
    state.put(event_factory(None))

    assert any(name == "queue_drop_callback_error" for name, _ in diagnostics)

    state.set_worker(lambda _event: None)
    state.start()
    state.stop(timeout=0.5)


def test_queue_worker_diagnostic_hook_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Diagnostic callbacks raising exceptions are logged as errors."""

    def raising(_name: str, _payload: DiagnosticPayload) -> None:
        raise RuntimeError("diag boom")

    state = QueueWorkerState(
        worker=None,
        maxsize=1,
        drop_policy=QueuePolicy.DROP,
        on_drop=None,
        timeout=None,
        stop_timeout=0.1,
        diagnostic=raising,
        failure_reset_after=0.01,
    )

    with caplog.at_level(logging.ERROR, logger="lib_log_rich.adapters._queue_worker"):
        state.emit_diagnostic("demo", {})

    assert any("Queue diagnostic hook raised" in record.message for record in caplog.records)


@POSIX_ONLY
def test_queue_worker_reports_shutdown_timeout_when_worker_hangs(event_factory: EventFactory) -> None:
    """A blocked worker triggers the shutdown timeout diagnostic."""
    diagnostics: Diagnostics = []
    worker_started = threading.Event()
    release_worker = threading.Event()

    def blocking_worker(_event: LogEvent) -> None:
        worker_started.set()
        release_worker.wait()

    state = make_state(worker=blocking_worker, diagnostics=diagnostics, stop_timeout=0.05)
    state.start()
    state.put(event_factory(None))
    assert worker_started.wait(0.5)

    with pytest.raises(RuntimeError):
        state.stop(timeout=0.01)

    assert any(name == "queue_shutdown_timeout" for name, _ in diagnostics)

    release_worker.set()
    state.stop(timeout=0.5)


def test_queue_worker_drop_policy_reports_drop_diagnostic(event_factory: EventFactory) -> None:
    """Dropping due to a full queue emits the drop diagnostic and callback."""
    diagnostics: Diagnostics = []
    dropped: list[str] = []
    processed = threading.Event()

    def drop_collector(event: LogEvent) -> None:
        dropped.append(event.event_id)

    def worker(event: LogEvent) -> None:
        processed.set()

    state = make_state(
        worker=worker,
        drop_policy=QueuePolicy.DROP,
        on_drop=drop_collector,
        diagnostics=diagnostics,
        maxsize=1,
    )

    first = event_factory(None)
    second = event_factory(None)
    assert state.put(first) is True
    assert state.put(second) is False
    assert dropped == [second.event_id]
    assert any(name == "queue_dropped" for name, _ in diagnostics)

    state.start()
    assert processed.wait(0.5)
    state.stop(drain=False, timeout=0.2)
    state.stop(timeout=0.5)


def test_queue_worker_enters_degraded_mode_after_worker_failure(event_factory: EventFactory) -> None:
    """Worker exceptions switch block policy into degraded drop mode."""
    diagnostics: Diagnostics = []
    failure_seen = threading.Event()

    def failing_worker(_event: LogEvent) -> None:
        failure_seen.set()
        raise RuntimeError("boom")

    state = make_state(worker=failing_worker, diagnostics=diagnostics, maxsize=2)
    state.start()
    state.put(event_factory(None))
    assert failure_seen.wait(0.5)
    state.wait_until_idle(0.5)
    assert state.worker_failed is True

    result = state.put(event_factory(None))
    assert result is True
    assert any(name == "queue_degraded_drop_mode" for name, _ in diagnostics)

    state.stop(timeout=0.5)


def test_queue_worker_success_resets_failure_after_recovery(event_factory: EventFactory) -> None:
    """A successful run after the reset interval clears failure flags."""
    diagnostics: Diagnostics = []
    failure_seen = threading.Event()
    success_seen = threading.Event()

    def failing_worker(_event: LogEvent) -> None:
        failure_seen.set()
        raise RuntimeError("boom")

    state = make_state(worker=failing_worker, diagnostics=diagnostics, failure_reset_after=0.01, maxsize=2)
    state.start()
    state.put(event_factory(None))
    assert failure_seen.wait(0.5)
    state.wait_until_idle(0.5)
    assert state.worker_failed is True

    def succeeding_worker(_event: LogEvent) -> None:
        success_seen.set()

    state.set_worker(succeeding_worker)
    time.sleep(0.02)
    assert state.put(event_factory(None)) is True
    assert success_seen.wait(0.5)
    state.wait_until_idle(0.5)
    assert state.worker_failed is False

    state.stop(timeout=0.5)


def test_queue_worker_manual_drain_drops_pending_items(event_factory: EventFactory) -> None:
    """Manual draining hands pending events to the drop handler."""
    diagnostics: Diagnostics = []
    dropped_ids: list[str] = []

    def on_drop(event: LogEvent) -> None:
        dropped_ids.append(event.event_id)

    state = make_state(worker=None, diagnostics=diagnostics, on_drop=on_drop, drop_policy=QueuePolicy.DROP, maxsize=3)
    state.put(event_factory(None))
    state.put(event_factory(None))
    state.put(event_factory(None))

    state.drain_pending_items()

    assert len(dropped_ids) == 3
    assert any(name == "queue_dropped" for name, _ in diagnostics)


def test_queue_worker_enqueue_stop_signal_drops_when_queue_full(event_factory: EventFactory) -> None:
    """Stop signals drop queued events when the buffer is already full."""
    dropped: list[str] = []

    def on_drop(event: LogEvent) -> None:
        dropped.append(event.event_id)

    state = make_state(worker=None, drop_policy=QueuePolicy.DROP, on_drop=on_drop)
    state.put(event_factory(None))
    state.enqueue_stop_signal(deadline=time.monotonic())

    assert dropped


def test_queue_worker_success_without_reset_interval_preserves_failure(event_factory: EventFactory) -> None:
    """Without a reset interval worker failures stay sticky."""
    failure_seen = threading.Event()
    success_seen = threading.Event()

    def failing_worker(_event: LogEvent) -> None:
        failure_seen.set()
        raise RuntimeError("boom")

    state = make_state(worker=failing_worker, failure_reset_after=None)
    state.start()
    state.put(event_factory(None))
    assert failure_seen.wait(0.5)
    state.wait_until_idle(0.5)
    assert state.worker_failed is True

    def succeeding_worker(_event: LogEvent) -> None:
        success_seen.set()

    state.set_worker(succeeding_worker)
    state.put(event_factory(None))
    assert success_seen.wait(0.5)
    state.wait_until_idle(0.5)
    assert state.worker_failed is True

    state.stop(timeout=0.5)


def test_queue_worker_success_with_missing_timestamp_clears_failure(event_factory: EventFactory) -> None:
    """Success clears failure when the failure timestamp was missing."""
    success_seen = threading.Event()

    def succeeding_worker(_event: LogEvent) -> None:
        success_seen.set()

    state = make_state(worker=succeeding_worker, failure_reset_after=0.01)
    state.set_worker_failure(failed=True, timestamp=None)
    state.start()
    state.put(event_factory(None))
    assert success_seen.wait(0.5)
    state.wait_until_idle(0.5)
    assert state.worker_failed is False

    state.stop(timeout=0.5)


def test_queue_worker_stop_respects_explicit_timeout(event_factory: EventFactory) -> None:
    """Stopping with a positive timeout waits for the queue to drain."""
    processed = threading.Event()

    def worker(_event: LogEvent) -> None:
        processed.set()

    state = make_state(worker=worker, stop_timeout=0.5)
    state.start()
    state.put(event_factory(None))
    assert processed.wait(0.5)

    state.stop(timeout=0.2)


def test_queue_worker_stop_without_drain_requeues_signal(event_factory: EventFactory) -> None:
    """Opting out of draining requeues the stop sentinel immediately."""
    state = make_state(worker=lambda _e: None)
    state.start()
    state.put(event_factory(None))
    state.wait_until_idle(0.5)

    state.stop(drain=False, timeout=0.2)


def test_queue_worker_handle_drop_includes_level(event_factory: EventFactory) -> None:
    """Drop diagnostics record the event level when available."""
    diagnostics: Diagnostics = []

    state = make_state(worker=None, diagnostics=diagnostics, drop_policy=QueuePolicy.DROP)
    event = event_factory(None)
    state.handle_drop(event)

    payload = diagnostics[-1][1]
    assert "level" in payload


def test_queue_worker_sentinel_ignored_before_stop(event_factory: EventFactory) -> None:
    """A queued sentinel is ignored when stop has not been requested."""
    processed = threading.Event()

    def worker(_event: LogEvent) -> None:
        processed.set()

    state = make_state(worker=worker)
    state.start()
    state.enqueue_stop_signal(deadline=None)
    state.put(event_factory(None))
    assert processed.wait(0.5)

    state.stop(timeout=0.5)


def test_queue_worker_enqueue_stop_signal_handles_empty_queue(monkeypatch: pytest.MonkeyPatch, event_factory: EventFactory) -> None:
    """Stop signal retries when the queue drains during the attempt."""
    state = make_state(worker=None, drop_policy=QueuePolicy.DROP)
    state.put(event_factory(None))

    original_get_nowait = state._queue.get_nowait  # type: ignore[attr-defined]
    attempts = {"count": 0}

    def fake_get_nowait() -> LogEvent | None:  # type: ignore[override]
        if attempts["count"] == 0:
            attempts["count"] += 1
            raise queue.Empty
        return original_get_nowait()

    monkeypatch.setattr(state._queue, "get_nowait", fake_get_nowait)  # type: ignore[attr-defined]
    state.enqueue_stop_signal(deadline=time.monotonic() + 0.05)

    assert attempts["count"] == 1


def test_queue_worker_queue_size_reports_items(event_factory: EventFactory) -> None:
    """Queue size helper exposes the current number of buffered events."""
    state = make_state(worker=None, maxsize=2)
    state.put(event_factory(None))
    assert state.queue_size() == 1


def test_queue_worker_clear_failure_flag_resets_degraded_mode() -> None:
    """Clearing worker failure resets degraded drop mode as well."""
    state = make_state(worker=None, maxsize=1)
    state.note_degraded_drop_mode()
    state.set_worker_failure(failed=False, timestamp=None)

    assert state.is_degraded_drop_mode() is False


def test_queue_worker_stop_zero_timeout_skips_wait(event_factory: EventFactory) -> None:
    """Zero timeout avoids waiting for drain completion."""
    diagnostics: Diagnostics = []
    state = make_state(worker=lambda _e: None, diagnostics=diagnostics)
    state.start()
    state.stop(timeout=0.0)
    assert diagnostics == []


def test_queue_worker_run_skips_processing_without_worker(event_factory: EventFactory) -> None:
    """The worker loop simply acknowledges events when no worker is set."""
    state = make_state(worker=None, maxsize=1)
    state.start()
    state.put(event_factory(None))
    assert state.wait_until_idle(0.5)
    state.stop(timeout=0.5)


def test_queue_worker_handle_drop_without_level(event_factory: EventFactory) -> None:
    """Dropping an event without level omits the level payload."""
    diagnostics: Diagnostics = []
    event = event_factory({"level": None})
    state = make_state(worker=None, diagnostics=diagnostics, drop_policy=QueuePolicy.DROP)
    state.handle_drop(event)

    payload = diagnostics[-1][1]
    assert "level" not in payload


def test_queue_worker_enqueue_stop_signal_ignores_none_payload() -> None:
    """Stop signal drops non-event placeholders without invoking handlers."""
    state = make_state(worker=None, drop_policy=QueuePolicy.DROP)
    state.enqueue_raw(None)
    state.enqueue_stop_signal(deadline=time.monotonic())
