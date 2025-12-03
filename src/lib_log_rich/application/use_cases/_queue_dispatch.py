"""Queue dispatch helpers for the process pipeline.

This module isolates queue hand-off diagnostics so the main use case stays
focused on orchestration. By constraining the queue behaviour to a thin
adapter-aware wrapper we preserve clean separation between synchronous fan-out
and asynchronous delivery paths.
"""

from __future__ import annotations

from collections.abc import Callable

from lib_log_rich.application.ports import QueuePort
from lib_log_rich.domain import LogEvent

from ._pipeline import DiagnosticEmitter
from ._types import ProcessResult

QueueDispatchResult = ProcessResult | None
QueueDispatcher = Callable[[LogEvent], QueueDispatchResult]


def build_queue_dispatcher(queue: QueuePort | None, emit: DiagnosticEmitter) -> QueueDispatcher:
    """Return a dispatcher that hands events to the queue when configured."""
    if queue is None:

        def _noop(_event: LogEvent) -> QueueDispatchResult:
            return None

        return _noop

    def _dispatch(event: LogEvent) -> QueueDispatchResult:
        queued = queue.put(event)
        if not queued:
            emit(
                "queue_full",
                {
                    "event_id": event.event_id,
                    "logger": event.logger_name,
                    "level": event.level.name,
                },
            )
            return ProcessResult(ok=False, reason="queue_full")

        emit("queued", {"event_id": event.event_id, "logger": event.logger_name})
        return ProcessResult(ok=True, event_id=event.event_id, queued=True)

    return _dispatch


__all__ = ["QueueDispatchResult", "QueueDispatcher", "build_queue_dispatcher"]
