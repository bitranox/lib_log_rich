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

from collections.abc import Callable
from typing import Any, ClassVar

from lib_log_rich.application.ports.queue import QueuePort
from lib_log_rich.application.use_cases._types import DiagnosticCallback, DiagnosticPayload
from lib_log_rich.domain.enums import QueuePolicy
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.runtime.settings.models import DEFAULT_QUEUE_MAXSIZE, DEFAULT_QUEUE_PUT_TIMEOUT, DEFAULT_QUEUE_STOP_TIMEOUT

from ._queue_worker import QueueWorkerState


class QueueAdapter(QueuePort):
    """Process log events on a background thread."""

    _LEGACY_INTERNALS: ClassVar[set[str]] = {
        "_worker",
        "_queue",
        "_thread",
        "_stop_event",
        "_drop_pending",
        "_drain_event",
        "_drop_policy",
        "_on_drop",
        "_timeout",
        "_stop_timeout",
        "_diagnostic",
        "_failure_reset_after",
        "_worker_failed",
        "_worker_failed_at",
        "_degraded_drop_mode",
    }

    def __init__(
        self,
        *,
        worker: Callable[[LogEvent], None] | None = None,
        maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        drop_policy: QueuePolicy = QueuePolicy.BLOCK,
        on_drop: Callable[[LogEvent], None] | None = None,
        timeout: float | None = DEFAULT_QUEUE_PUT_TIMEOUT,
        stop_timeout: float | None = DEFAULT_QUEUE_STOP_TIMEOUT,
        diagnostic: DiagnosticCallback | None = None,
        failure_reset_after: float | None = 30.0,
    ) -> None:
        """Initialize the queue adapter with worker and backpressure settings."""
        state = QueueWorkerState(
            worker=worker,
            maxsize=maxsize,
            drop_policy=drop_policy,
            on_drop=on_drop,
            timeout=timeout,
            stop_timeout=stop_timeout,
            diagnostic=diagnostic,
            failure_reset_after=failure_reset_after,
        )
        self._state = state
        self._debug_view = QueueAdapterDebug(state)

    def start(self) -> None:
        """Start the background worker thread."""
        self._state.start()

    def stop(self, *, drain: bool = True, timeout: float | None = None) -> None:
        """Stop the worker thread, optionally draining pending events."""
        self._state.stop(drain=drain, timeout=timeout)

    def put(self, event: LogEvent) -> bool:
        """Enqueue an event for asynchronous processing."""
        return self._state.put(event)

    def set_worker(self, worker: Callable[[LogEvent], None]) -> None:
        """Replace the worker callable used to process events."""
        self._state.set_worker(worker)

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Block until the queue drains or timeout expires."""
        return self._state.wait_until_idle(timeout)

    @property
    def worker_failed(self) -> bool:
        """Return True if the worker thread observed an exception."""
        return self._state.worker_failed

    def debug(self) -> QueueAdapterDebug:
        """Return a helper exposing diagnostic hooks for tests."""
        return self._debug_view

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - legacy warning path
        """Raise AttributeError for hidden internals with guidance."""
        if name in self._LEGACY_INTERNALS:
            raise AttributeError(
                "QueueAdapter internals are hidden; use QueueAdapter.debug() for diagnostics",
            )
        raise AttributeError(name)


class QueueAdapterDebug:
    """Helper exposing diagnostic hooks for :class:`QueueAdapter`."""

    def __init__(self, state: QueueWorkerState) -> None:
        """Initialize with the internal worker state."""
        self._state = state

    def enqueue_raw(self, item: LogEvent | None) -> None:
        """Put an item directly into the queue without policy checks."""
        self._state.enqueue_raw(item)

    def queue_empty(self) -> bool:
        """Return True if the queue has no pending items."""
        return self._state.queue_empty()

    def queue_size(self) -> int:
        """Return the approximate number of items in the queue."""
        return self._state.queue_size()

    def worker_thread(self) -> Any | None:
        """Return the current worker thread, or None if not running."""
        return self._state.worker_thread()

    def current_worker(self) -> Callable[[LogEvent], None] | None:
        """Return the worker callable used to process events."""
        return self._state.current_worker()

    def handle_drop(self, event: LogEvent) -> None:
        """Handle a dropped event by invoking callbacks and diagnostics."""
        self._state.handle_drop(event)

    def emit_diagnostic(self, name: str, payload: DiagnosticPayload) -> None:
        """Emit a diagnostic event to the configured callback."""
        self._state.emit_diagnostic(name, payload)

    def note_degraded_drop_mode(self) -> None:
        """Mark the queue as operating in degraded drop mode."""
        self._state.note_degraded_drop_mode()

    def is_degraded_drop_mode(self) -> bool:
        """Return True if the queue is in degraded drop mode."""
        return self._state.is_degraded_drop_mode()

    def set_worker_failure(self, *, failed: bool, timestamp: float | None) -> None:
        """Set or clear the worker failure state."""
        self._state.set_worker_failure(failed=failed, timestamp=timestamp)

    def record_worker_success(self) -> None:
        """Record a successful worker invocation for failure recovery."""
        self._state.record_worker_success()

    def drain_pending_items(self) -> None:
        """Drop all pending items from the queue."""
        self._state.drain_pending_items()

    def enqueue_stop_signal(self, deadline: float | None) -> None:
        """Enqueue a stop signal, dropping items if needed to make room."""
        self._state.enqueue_stop_signal(deadline)


__all__ = ["QueueAdapter", "QueueAdapterDebug"]
