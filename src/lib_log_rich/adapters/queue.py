"""Thread-based queue adapter for log event fan-out.

Purpose
-------
Decouple producers from IO-bound adapters, satisfying the multiprocess
requirements captured in ``concept_architecture_plan.md``.

Contents
--------
* :class:`QueueAdapter` - background worker implementation of :class:`QueuePort`.

System Role
-----------
Executes adapter fan-out on a dedicated thread to keep host code responsive.

Alignment Notes
---------------
Implements the queue behaviour described in ``docs/systemdesign/module_reference.md``
(start-on-demand, drain-on-shutdown semantics).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from lib_log_rich.application.ports.queue import QueuePort
from lib_log_rich.domain.events import LogEvent


LOGGER = logging.getLogger(__name__)


class QueueAdapter(QueuePort):
    """Process log events on a background thread.

    Examples
    --------
    >>> processed = []
    >>> adapter = QueueAdapter(worker=lambda event: processed.append(event))
    >>> adapter.start()
    >>> from datetime import datetime, timezone
    >>> from lib_log_rich.domain.context import LogContext
    >>> from lib_log_rich.domain.levels import LogLevel
    >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
    >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
    >>> adapter.put(event)
    True
    >>> adapter.stop(drain=True)
    >>> processed[0].event_id
    'id'
    """

    def __init__(
        self,
        *,
        worker: Callable[[LogEvent], None] | None = None,
        maxsize: int = 2048,
        drop_policy: str = "block",
        on_drop: Callable[[LogEvent], None] | None = None,
        timeout: float | None = 1.0,
        stop_timeout: float | None = 5.0,
        diagnostic: Callable[[str, dict[str, Any]], None] | None = None,
        failure_reset_after: float | None = 30.0,
    ) -> None:
        """Create the queue with an optional initial worker and capacity.

        Parameters
        ----------
        worker:
            Callable invoked for each event; defaults to ``None`` until
            :meth:`set_worker` installs the fan-out closure.
        maxsize:
            Maximum number of queued events before backpressure or drops apply.
        drop_policy:
            Either ``"block"`` (producers wait) or ``"drop"`` (new events are
            rejected when the queue is full).
        on_drop:
            Optional callback invoked when events are dropped.
        timeout:
            Timeout (seconds) for producers when using the blocking policy. The runtime
            defaults to a 1-second wait so unhealthy workers cannot block callers
            forever; pass ``None`` to opt back into indefinite blocking.
        stop_timeout:
            Default drain deadline (seconds) applied when :meth:`stop` is called
            without an explicit ``timeout``. ``None`` disables the deadline.
        failure_reset_after:
            Seconds the worker must run without exceptions before the
            ``worker_failed`` health flag clears automatically. ``None`` keeps
            the flag latched until :meth:`start` is called.
        """
        self._worker = worker
        self._queue: queue.Queue[LogEvent | None] = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._drop_pending = False
        self._drain_event = threading.Event()
        self._drain_event.set()
        policy = drop_policy.lower()
        if policy not in {"block", "drop"}:
            raise ValueError("drop_policy must be 'block' or 'drop'")
        self._drop_policy = policy
        self._on_drop = on_drop
        self._timeout = timeout
        self._stop_timeout = stop_timeout
        self._diagnostic = diagnostic
        self._failure_reset_after = failure_reset_after
        self._worker_failed = False
        self._worker_failed_at: float | None = None
        self._degraded_drop_mode = False

    def start(self) -> None:
        """Start the background worker thread if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._drop_pending = False
        self._clear_worker_failure()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, *, drain: bool = True, timeout: float | None = None) -> None:
        """Stop the worker thread, optionally draining queued events.

        Parameters
        ----------
        drain:
            When ``True`` wait for queued events to finish processing before
            returning. When ``False`` pending events are dropped via the
            configured drop handler.
        timeout:
            Per-call override for the drain deadline. ``None`` falls back to the
            adapter default configured via :func:`lib_log_rich.init`
            (`queue_stop_timeout`). Use ``None`` at configuration time to wait
            indefinitely during shutdown.
        """
        thread = self._thread
        if thread is None:
            return

        effective_timeout = timeout if timeout is not None else self._stop_timeout
        start = time.monotonic()
        deadline = start + effective_timeout if effective_timeout is not None else None

        def remaining_time() -> float | None:
            if deadline is None:
                return None
            return max(0.0, deadline - time.monotonic())

        drop_pending = not drain
        self._drop_pending = drop_pending
        self._stop_event.set()
        self._enqueue_stop_signal(deadline)

        drain_completed = True
        if drain:
            if effective_timeout is None:
                self._queue.join()
            else:
                remaining = remaining_time()
                drained = False
                if remaining is None or remaining > 0:
                    drained = self._drain_event.wait(remaining)
                if not drained:
                    drain_completed = False

        if not drain or not drain_completed:
            drop_pending = True
            self._drain_pending_items()

        join_timeout = remaining_time()
        if effective_timeout is None:
            thread.join()
        else:
            thread.join(0 if join_timeout is None else join_timeout)

        still_running = thread.is_alive()
        if still_running:
            self._thread = thread
            drop_pending = True
        else:
            self._thread = None
            self._stop_event.clear()

        self._drop_pending = drop_pending
        if drop_pending:
            self._drain_event.set()
        if drain and drain_completed:
            self._clear_worker_failure()

        if still_running:
            self._emit_diagnostic(
                "queue_shutdown_timeout",
                {
                    "timeout": effective_timeout,
                    "drain_completed": drain_completed,
                },
            )
            raise RuntimeError("Queue worker failed to stop within the allotted timeout")

    def put(self, event: LogEvent) -> bool:
        """Enqueue ``event`` for asynchronous processing.

        Returns ``True`` when the event was accepted, ``False`` when the queue
        was full and the configured drop policy discarded the payload."""
        effective_policy = self._drop_policy
        if effective_policy == "block" and self._worker_failed:
            effective_policy = "drop"
            self._note_degraded_drop_mode()

        if effective_policy == "drop":
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
        """Block until all queued events are processed or ``timeout`` elapses.

        Returns ``True`` when the queue drains fully; ``False`` when the wait
        timed out (events might still be pending).
        """

        if self._queue.unfinished_tasks == 0:
            return True
        return self._drain_event.wait(timeout)

    @property
    def worker_failed(self) -> bool:
        """Return ``True`` when the worker thread observed an exception.

        The flag clears automatically once the worker runs without errors for
        ``failure_reset_after`` seconds, after a clean ``stop(drain=True)``, or
        when :meth:`start` restarts the adapter.
        """

        return self._worker_failed

    def _run(self) -> None:
        """Internal worker loop draining the queue until stopped."""
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    if self._stop_event.is_set():
                        break
                    continue
                if self._drop_pending:
                    self._handle_drop(item)
                    continue
                if self._worker is not None:
                    try:
                        self._worker(item)
                    except Exception as exc:  # noqa: BLE001
                        self._worker_failed = True
                        self._worker_failed_at = time.monotonic()
                        self._report_worker_exception(item, exc)
                    else:
                        self._record_worker_success()
            finally:
                self._queue.task_done()
                if self._queue.unfinished_tasks == 0:
                    self._drain_event.set()

            if self._stop_event.is_set() and self._queue.empty():
                break

    def _handle_drop(self, event: LogEvent) -> None:
        """Invoke the drop callback when the queue rejects an event."""
        if self._on_drop is None:
            return
        try:
            self._on_drop(event)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Queue drop handler raised an exception; continuing", exc_info=exc)
            self._emit_diagnostic(
                "queue_drop_callback_error",
                {
                    "event_id": getattr(event, "event_id", None),
                    "logger": getattr(event, "logger_name", None),
                    "exception": repr(exc),
                },
            )

    def _report_worker_exception(self, event: LogEvent, exc: Exception) -> None:
        """Log and surface worker failures without tearing down the thread."""

        LOGGER.error("Queue worker raised an exception; continuing", exc_info=exc)
        self._emit_diagnostic(
            "queue_worker_error",
            {"event_id": getattr(event, "event_id", None), "logger": getattr(event, "logger_name", None), "exception": repr(exc)},
        )

    def _emit_diagnostic(self, name: str, payload: dict[str, Any]) -> None:
        """Invoke the diagnostic hook while guarding against callback failures."""

        if self._diagnostic is None:
            return
        try:
            self._diagnostic(name, payload)
        except Exception as diagnostic_exc:  # noqa: BLE001
            LOGGER.error("Queue diagnostic hook raised while reporting %s", name, exc_info=diagnostic_exc)

    def _note_degraded_drop_mode(self) -> None:
        """Record the transition to drop mode after worker failure."""

        if self._degraded_drop_mode:
            return
        self._degraded_drop_mode = True
        self._emit_diagnostic("queue_degraded_drop_mode", {"reason": "worker_failed"})

    def _record_worker_success(self) -> None:
        """Clear worker failure flags when recovery criteria are met."""

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
        """Reset worker failure tracking state."""

        self._worker_failed = False
        self._worker_failed_at = None
        self._degraded_drop_mode = False

    def _drain_pending_items(self) -> None:
        """Remove any queued events left after a non-draining stop."""

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
        """Ensure the worker thread wakes up to observe the stop event."""

        while True:
            try:
                if deadline is None:
                    self._queue.put(None)
                else:
                    self._queue.put(None, timeout=max(0.0, deadline - time.monotonic()))
                self._drain_event.clear()
                break
            except queue.Full:
                try:
                    dropped = self._queue.get_nowait()
                except queue.Empty:
                    continue
                else:
                    if isinstance(dropped, LogEvent):
                        self._handle_drop(dropped)
                    self._queue.task_done()


__all__ = ["QueueAdapter"]
