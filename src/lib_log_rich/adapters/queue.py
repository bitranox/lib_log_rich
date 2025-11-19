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
from typing import Any, ClassVar, Optional

from lib_log_rich.application.ports.queue import QueuePort
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
        drop_policy: str = "block",
        on_drop: Callable[[LogEvent], None] | None = None,
        timeout: float | None = DEFAULT_QUEUE_PUT_TIMEOUT,
        stop_timeout: float | None = DEFAULT_QUEUE_STOP_TIMEOUT,
        diagnostic: Callable[[str, dict[str, Any]], None] | None = None,
        failure_reset_after: float | None = 30.0,
    ) -> None:
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
        self._state.start()

    def stop(self, *, drain: bool = True, timeout: float | None = None) -> None:
        self._state.stop(drain=drain, timeout=timeout)

    def put(self, event: LogEvent) -> bool:
        return self._state.put(event)

    def set_worker(self, worker: Callable[[LogEvent], None]) -> None:
        self._state.set_worker(worker)

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        return self._state.wait_until_idle(timeout)

    @property
    def worker_failed(self) -> bool:
        return self._state.worker_failed

    def debug(self) -> "QueueAdapterDebug":
        """Return a helper exposing diagnostic hooks for tests."""

        return self._debug_view

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - legacy warning path
        if name in self._LEGACY_INTERNALS:
            raise AttributeError(
                "QueueAdapter internals are hidden; use QueueAdapter.debug() for diagnostics",
            )
        raise AttributeError(name)


class QueueAdapterDebug:
    """Helper exposing diagnostic hooks for :class:`QueueAdapter`."""

    def __init__(self, state: QueueWorkerState) -> None:
        self._state = state

    def enqueue_raw(self, item: LogEvent | None) -> None:
        self._state.enqueue_raw(item)

    def queue_empty(self) -> bool:
        return self._state.queue_empty()

    def queue_size(self) -> int:
        return self._state.queue_size()

    def worker_thread(self) -> Optional[Any]:
        return self._state.worker_thread()

    def current_worker(self) -> Optional[Callable[[LogEvent], None]]:
        return self._state.current_worker()

    def handle_drop(self, event: LogEvent) -> None:
        self._state.handle_drop(event)

    def emit_diagnostic(self, name: str, payload: dict[str, Any]) -> None:
        self._state.emit_diagnostic(name, payload)

    def note_degraded_drop_mode(self) -> None:
        self._state.note_degraded_drop_mode()

    def is_degraded_drop_mode(self) -> bool:
        return self._state.is_degraded_drop_mode()

    def set_worker_failure(self, *, failed: bool, timestamp: float | None) -> None:
        self._state.set_worker_failure(failed=failed, timestamp=timestamp)

    def record_worker_success(self) -> None:
        self._state.record_worker_success()

    def drain_pending_items(self) -> None:
        self._state.drain_pending_items()

    def enqueue_stop_signal(self, deadline: float | None) -> None:
        self._state.enqueue_stop_signal(deadline)


__all__ = ["QueueAdapter", "QueueAdapterDebug"]
