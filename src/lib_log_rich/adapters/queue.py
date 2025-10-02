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
    ) -> None:
        """Create the queue with an optional initial worker and capacity."""
        self._worker = worker
        self._queue: queue.Queue[LogEvent | None] = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._drop_pending = False
        policy = drop_policy.lower()
        if policy not in {"block", "drop"}:
            raise ValueError("drop_policy must be 'block' or 'drop'")
        self._drop_policy = policy
        self._on_drop = on_drop
        self._timeout = timeout

    def start(self) -> None:
        """Start the background worker thread if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._drop_pending = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, *, drain: bool = True) -> None:
        """Stop the worker thread, optionally draining queued events."""
        if self._thread is None:
            return

        self._drop_pending = not drain
        self._stop_event.set()
        self._queue.put(None)
        if drain:
            self._queue.join()
        thread = self._thread
        self._thread = None
        try:
            thread.join()
        finally:
            if not drain:
                self._drain_pending_items()
            self._stop_event.clear()
            self._drop_pending = False

    def put(self, event: LogEvent) -> bool:
        """Enqueue ``event`` for asynchronous processing.

        Returns ``True`` when the event was accepted, ``False`` when the queue
        was full and the configured drop policy discarded the payload."""
        if self._drop_policy == "drop":
            try:
                self._queue.put(event, block=False)
            except queue.Full:
                self._handle_drop(event)
                return False
            return True
        if self._timeout is not None:
            try:
                self._queue.put(event, timeout=self._timeout)
            except queue.Full:
                self._handle_drop(event)
                return False
            return True
        self._queue.put(event)
        return True

    def set_worker(self, worker: Callable[[LogEvent], None]) -> None:
        """Swap the worker callable used to process events."""
        self._worker = worker

    def _run(self) -> None:
        """Internal worker loop draining the queue until stopped."""
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                self._queue.task_done()
                if self._stop_event.is_set():
                    if self._drop_pending:
                        continue
                    break
                continue
            try:
                if self._drop_pending:
                    continue
                if self._worker is not None:
                    self._worker(item)
            finally:
                self._queue.task_done()

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
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()


__all__ = ["QueueAdapter"]
