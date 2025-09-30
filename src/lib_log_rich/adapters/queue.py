"""Thread-based queue adapter for log event fan-out.

Purpose
-------
Decouple producers from IO-bound adapters, satisfying the multiprocess
requirements captured in ``konzept_architecture_plan.md``.

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
    >>> adapter.stop(drain=True)
    >>> processed[0].event_id
    'id'
    """

    def __init__(self, *, worker: Callable[[LogEvent], None] | None = None, maxsize: int = 2048) -> None:
        """Create the queue with an optional initial worker and capacity."""
        self._worker = worker
        self._queue: queue.Queue[LogEvent | None] = queue.Queue(maxsize=maxsize)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start the background worker thread if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, *, drain: bool = True) -> None:
        """Stop the worker thread, optionally draining queued events."""
        if not drain:
            with self._queue.mutex:
                self._queue.queue.clear()
        self._stop_event.set()
        self._queue.put(None)
        if drain:
            self._queue.join()
        if self._thread is not None:
            self._thread.join(timeout=1)

    def put(self, event: LogEvent) -> None:
        """Enqueue ``event`` for asynchronous processing."""
        self._queue.put(event)

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
                    break
                continue
            try:
                if self._worker is not None:
                    self._worker(item)
            finally:
                self._queue.task_done()


__all__ = ["QueueAdapter"]
