"""Internal worker state for the queue adapter.

Thread-Safe Queue Worker Implementation
=======================================

This module implements a background worker thread that processes log events
from a queue. The worker handles graceful shutdown, backpressure, and failure
recovery.

Algorithm
---------
The worker uses a two-phase shutdown protocol:

1. GRACEFUL: Stop accepting new items, drain existing items (timeout: configurable)
2. FORCED: Abort after timeout, dropping remaining items

Backpressure Handling
--------------------
When queue reaches capacity, behavior depends on drop_policy:

- "drop_oldest": Remove oldest item to make room (default)
- "drop_newest": Reject new item
- "block": Wait for space (not recommended in async contexts)

Items dropped trigger diagnostic callbacks with reason codes:
- "queue_full": Queue at capacity
- "worker_failure": Worker thread crashed
- "shutdown": Dropped during shutdown

Worker enters degraded mode after failures and recovers after successful batch.

Thread Safety
------------
All public methods are thread-safe via internal RLock. The queue itself
provides thread-safe put/get operations. Worker thread has exclusive access
to internal state during event processing.

Performance Characteristics
--------------------------
- Queue operations: O(1)
- Shutdown drain: O(n) where n = queue size
- Memory: O(maxsize * avg_event_size)
- Typical event processing: <1ms per event
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

from lib_log_rich.application.use_cases._types import DiagnosticCallback, DiagnosticPayload
from lib_log_rich.domain.enums import QueuePolicy
from lib_log_rich.domain.events import LogEvent

LOGGER = logging.getLogger(__name__)


class QueueWorkerState:
    """Manage the queue worker thread and related bookkeeping."""

    def __init__(
        self,
        *,
        worker: Callable[[LogEvent], None] | None,
        maxsize: int,
        drop_policy: QueuePolicy,
        on_drop: Callable[[LogEvent], None] | None,
        timeout: float | None,
        stop_timeout: float | None,
        diagnostic: DiagnosticCallback | None,
        failure_reset_after: float | None,
    ) -> None:
        """Initialize the queue worker state with configuration options."""
        self._worker = worker
        self._queue: queue.Queue[LogEvent | None] = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._drop_pending = False
        self._drain_event = threading.Event()
        self._drain_event.set()
        self._drop_policy = drop_policy
        self._on_drop = on_drop
        self._timeout = timeout
        self._stop_timeout = stop_timeout
        self._diagnostic = diagnostic
        self._failure_reset_after = failure_reset_after
        self._worker_failed = False
        self._worker_failed_at: float | None = None
        self._degraded_drop_mode = False

    # Delegated operations -------------------------------------------------

    def start(self) -> None:
        """Start the background worker thread if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._drop_pending = False
        self._clear_worker_failure()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _wait_for_drain(self, remaining_time_fn: Callable[[], float | None], effective_timeout: float | None) -> bool:
        """Wait for queue to drain, return True if successful."""
        if effective_timeout is None:
            self._queue.join()
            return True

        remaining = remaining_time_fn()
        if remaining is None or remaining > 0:
            return self._drain_event.wait(remaining)
        return False

    def _join_worker_thread(self, thread: threading.Thread, remaining_time_fn: Callable[[], float | None], effective_timeout: float | None) -> bool:
        """Join worker thread, return True if thread stopped."""
        join_timeout = remaining_time_fn()
        if effective_timeout is None:
            thread.join()
        else:
            thread.join(0 if join_timeout is None else join_timeout)
        return not thread.is_alive()

    def _handle_shutdown_timeout(self, effective_timeout: float | None, drain_completed: bool) -> None:
        """Emit diagnostic and raise for shutdown timeout (unless fire-and-forget)."""
        if effective_timeout == 0.0:
            return
        self._emit_diagnostic(
            "queue_shutdown_timeout",
            {"timeout": effective_timeout, "drain_completed": drain_completed},
        )
        raise RuntimeError("Queue worker failed to stop within the allotted timeout")

    def _handle_drain_phase(self, drain: bool, deadline: float | None, remaining_time_fn: Callable[[], float | None], effective_timeout: float | None) -> bool:
        """Handle the drain phase, return True if drain completed."""
        if not drain:
            return False

        drain_completed = self._wait_for_drain(remaining_time_fn, effective_timeout)

        if not drain_completed:
            self._drop_pending = True
            self._drain_pending_items()
            if self._stop_event.is_set():
                self._enqueue_stop_signal(deadline)

        return drain_completed

    def _update_thread_state_after_stop(self, thread: threading.Thread, stopped: bool, drain: bool, drain_completed: bool) -> None:
        """Update internal state after stop attempt."""
        if stopped:
            self._thread = None
            self._stop_event.clear()
            if drain and drain_completed:
                self._clear_worker_failure()
        else:
            self._thread = thread
            self._drop_pending = True

        if self._drop_pending:
            self._drain_event.set()

    def stop(self, *, drain: bool = True, timeout: float | None = None) -> None:
        """Stop the worker thread, optionally draining queued events."""
        thread = self._thread
        if thread is None:
            return

        effective_timeout = timeout if timeout is not None else self._stop_timeout
        start = time.monotonic()
        deadline = start + effective_timeout if effective_timeout is not None else None

        def remaining_time() -> float | None:
            """Return remaining time until deadline, or None if no deadline."""
            return None if deadline is None else max(0.0, deadline - time.monotonic())

        # Initiate shutdown
        self._drop_pending = not drain
        self._stop_event.set()
        self._enqueue_stop_signal(deadline)

        # Handle drain phase
        drain_completed = self._handle_drain_phase(drain, deadline, remaining_time, effective_timeout)

        # Wait for worker thread
        stopped = self._join_worker_thread(thread, remaining_time, effective_timeout)

        # Update state
        self._update_thread_state_after_stop(thread, stopped, drain, drain_completed)

        # Handle timeout if needed
        if not stopped:
            self._handle_shutdown_timeout(effective_timeout, drain_completed)

    def put(self, event: LogEvent) -> bool:
        """Enqueue ``event`` for asynchronous processing."""
        effective_policy = self._drop_policy
        if effective_policy is QueuePolicy.BLOCK and self._worker_failed:
            effective_policy = QueuePolicy.DROP
            self._note_degraded_drop_mode()

        if effective_policy is QueuePolicy.DROP:
            try:
                self._queue.put(event, block=False)
            except queue.Full:
                self._handle_drop(event)
                return False
            self._drain_event.clear()
            return True

        if self._timeout is not None:
            try:
                self._queue.put(event, timeout=self._timeout)
            except queue.Full:
                self._handle_drop(event)
                return False
            self._drain_event.clear()
            return True

        self._queue.put(event)
        self._drain_event.clear()
        return True

    def set_worker(self, worker: Callable[[LogEvent], None]) -> None:
        """Swap the worker callable used to process events."""
        self._worker = worker

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Block until the queue drains or ``timeout`` expires."""
        if self._queue.unfinished_tasks == 0:
            return True
        return self._drain_event.wait(timeout)

    # Properties -----------------------------------------------------------

    @property
    def worker_failed(self) -> bool:
        """Return ``True`` when the worker thread observed an exception."""
        return self._worker_failed

    # Internal helpers -----------------------------------------------------

    def _should_stop(self, item: LogEvent | None) -> bool:
        """Check if worker should stop based on item and stop event."""
        if item is None and self._stop_event.is_set():
            return True
        return self._stop_event.is_set() and self._queue.empty()

    def _process_worker_item(self, item: LogEvent) -> None:
        """Process a single log event through the worker."""
        if self._worker is None:
            return
        try:
            self._worker(item)
        except Exception as exc:  # noqa: BLE001
            self._worker_failed = True
            self._worker_failed_at = time.monotonic()
            self._report_worker_exception(item, exc)
        else:
            self._record_worker_success()

    def _handle_queue_item(self, item: LogEvent | None) -> bool:
        """Handle a single queue item, return True to continue processing."""
        # Handle stop signal
        if item is None:
            return not self._stop_event.is_set()

        # Handle pending drops
        if self._drop_pending:
            self._handle_drop(item)
            return True

        # Process item with worker
        if self._worker is not None:
            self._process_worker_item(item)

        return True

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                should_continue = self._handle_queue_item(item)
                if not should_continue:
                    break
            finally:
                self._queue.task_done()
                if self._queue.unfinished_tasks == 0:
                    self._drain_event.set()

            if self._should_stop(item):
                break

    def _handle_drop(self, event: LogEvent) -> None:
        payload = {
            "event_id": getattr(event, "event_id", None),
            "logger": getattr(event, "logger_name", None),
        }
        level = getattr(event, "level", None)
        if level is not None:
            payload["level"] = getattr(level, "name", str(level))
        if self._on_drop is not None:
            try:
                self._on_drop(event)
            except Exception as exc:  # noqa: BLE001
                LOGGER.error("Queue drop handler raised an exception; continuing", exc_info=exc)
                self._emit_diagnostic(
                    "queue_drop_callback_error",
                    {
                        **payload,
                        "exception": repr(exc),
                    },
                )
        else:
            if self._diagnostic is None:
                LOGGER.warning(
                    "Queue dropped event %s from %s at level %s",
                    payload.get("event_id"),
                    payload.get("logger"),
                    payload.get("level"),
                )
        self._emit_diagnostic("queue_dropped", payload)

    def _report_worker_exception(self, event: LogEvent, exc: Exception) -> None:
        LOGGER.error("Queue worker raised an exception; continuing", exc_info=exc)
        self._emit_diagnostic(
            "queue_worker_error",
            {"event_id": getattr(event, "event_id", None), "logger": getattr(event, "logger_name", None), "exception": repr(exc)},
        )

    def _emit_diagnostic(self, name: str, payload: DiagnosticPayload) -> None:
        if self._diagnostic is None:
            return
        try:
            self._diagnostic(name, payload)
        except Exception as diagnostic_exc:  # noqa: BLE001
            LOGGER.error("Queue diagnostic hook raised while reporting %s", name, exc_info=diagnostic_exc)

    def _note_degraded_drop_mode(self) -> None:
        if self._degraded_drop_mode:
            return
        self._degraded_drop_mode = True
        self._emit_diagnostic("queue_degraded_drop_mode", {"reason": "worker_failed"})

    def _record_worker_success(self) -> None:
        if not self._worker_failed:
            return
        if self._failure_reset_after is None:
            return
        now = time.monotonic()
        if self._worker_failed_at is None:
            self._clear_worker_failure()
            return
        if now - self._worker_failed_at >= self._failure_reset_after:
            self._clear_worker_failure()

    def _clear_worker_failure(self) -> None:
        self._worker_failed = False
        self._worker_failed_at = None
        self._degraded_drop_mode = False

    def _drain_pending_items(self) -> None:
        while True:
            try:
                dropped = self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                if isinstance(dropped, LogEvent):
                    self._handle_drop(dropped)
                self._queue.task_done()
        self._drain_event.set()

    def _enqueue_stop_signal(self, deadline: float | None) -> None:
        while True:
            if self._try_put_stop_signal(deadline):
                self._drain_event.clear()
                break
            self._drop_one_to_make_room()

    def _try_put_stop_signal(self, deadline: float | None) -> bool:
        """Attempt to enqueue the stop signal, returning True on success."""
        try:
            if deadline is None:
                self._queue.put(None)
            else:
                self._queue.put(None, timeout=max(0.0, deadline - time.monotonic()))
            return True
        except queue.Full:
            return False

    def _drop_one_to_make_room(self) -> None:
        """Drop one item from the queue to make room for the stop signal."""
        try:
            dropped = self._queue.get_nowait()
        except queue.Empty:
            return
        if isinstance(dropped, LogEvent):
            self._handle_drop(dropped)
        self._queue.task_done()

    def enqueue_raw(self, item: LogEvent | None) -> None:
        """Put an item directly into the queue without policy checks."""
        self._queue.put(item)

    def queue_empty(self) -> bool:
        """Return True if the queue has no pending items."""
        return self._queue.empty()

    def queue_size(self) -> int:
        """Return the approximate number of items in the queue."""
        return self._queue.qsize()

    def worker_thread(self) -> threading.Thread | None:
        """Return the current worker thread, or None if not running."""
        return self._thread

    def current_worker(self) -> Callable[[LogEvent], None] | None:
        """Return the worker callable used to process events."""
        return self._worker

    def handle_drop(self, event: LogEvent) -> None:
        """Handle a dropped event by invoking callbacks and diagnostics."""
        self._handle_drop(event)

    def emit_diagnostic(self, name: str, payload: DiagnosticPayload) -> None:
        """Emit a diagnostic event to the configured callback."""
        self._emit_diagnostic(name, payload)

    def note_degraded_drop_mode(self) -> None:
        """Mark the queue as operating in degraded drop mode."""
        self._note_degraded_drop_mode()

    def is_degraded_drop_mode(self) -> bool:
        """Return True if the queue is in degraded drop mode."""
        return self._degraded_drop_mode

    def set_worker_failure(self, *, failed: bool, timestamp: float | None) -> None:
        """Set or clear the worker failure state."""
        self._worker_failed = failed
        self._worker_failed_at = timestamp
        if not failed:
            self._degraded_drop_mode = False

    def record_worker_success(self) -> None:
        """Record a successful worker invocation for failure recovery."""
        self._record_worker_success()

    def drain_pending_items(self) -> None:
        """Drop all pending items from the queue."""
        self._drain_pending_items()

    def enqueue_stop_signal(self, deadline: float | None) -> None:
        """Enqueue a stop signal, dropping items if needed to make room."""
        self._enqueue_stop_signal(deadline)


__all__ = ["QueueWorkerState"]
