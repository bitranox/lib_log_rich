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

import queue
import threading
import time
from collections.abc import Callable

from lib_log_rich.application.ports.queue import QueuePort
from lib_log_rich.domain.events import LogEvent


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
        timeout: float | None = None,
        stop_timeout: float | None = 5.0,
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
            Timeout (seconds) for producers when using the blocking policy.
        stop_timeout:
            Default drain deadline (seconds) applied when :meth:`stop` is called
            without an explicit ``timeout``. ``None`` disables the deadline.
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

    def start(self) -> None:
        """Start the background worker thread if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._drop_pending = False
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

        if thread.is_alive():
            self._thread = thread
            drop_pending = True
        else:
            self._thread = None
            self._stop_event.clear()

        self._drop_pending = drop_pending
        if drop_pending:
            self._drain_event.set()

    def put(self, event: LogEvent) -> bool:
        """Enqueue ``event`` for asynchronous processing.

        Returns ``True`` when the event was accepted, ``False`` when the queue
        was full and the configured drop policy discarded the payload."""
        accepted = False
        if self._drop_policy == "drop":
            try:
                self._queue.put(event, block=False)
            except queue.Full:
                self._handle_drop(event)
                return False
            accepted = True
        elif self._timeout is not None:
            try:
                self._queue.put(event, timeout=self._timeout)
            except queue.Full:
                self._handle_drop(event)
                return False
            accepted = True
        else:
            self._queue.put(event)
            accepted = True

        if accepted:
            self._drain_event.clear()
        return True

    def set_worker(self, worker: Callable[[LogEvent], None]) -> None:
        """Swap the worker callable used to process events."""
        self._worker = worker

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
                    self._worker(item)
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
        except Exception:
            pass

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
