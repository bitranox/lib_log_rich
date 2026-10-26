"""Fan-out helpers for the process pipeline.

The helpers defined here keep the adapter dispatch logic separate from
``create_process_log_event`` so each concern stays reviewable and independently
testable. They align with the clean architecture guidance documented in
``docs/systemdesign/concept_architecture.md`` by isolating adapter coordination
inside a small, side-effect-aware module.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from lib_log_rich.domain import DiagnosticEvent, DropReason, LogEvent, LogLevel

from ._types import FanOutCallable, ProcessResult

if TYPE_CHECKING:
    import logging

    from lib_log_rich.application.ports import ConsolePort, GraylogPort, StructuredBackendPort

    from ._types import DiagnosticEmitter

FanOutResultHandler = Callable[[LogEvent], ProcessResult]


def build_fan_out_handlers(
    *,
    console: ConsolePort,
    console_level: LogLevel,
    structured_backends: Sequence[StructuredBackendPort],
    backend_level: LogLevel,
    graylog: GraylogPort | None,
    graylog_level: LogLevel,
    emit: DiagnosticEmitter,
    colorize_console: bool,
    logger: logging.Logger,
) -> tuple[FanOutCallable, FanOutResultHandler]:
    """Return fan-out helpers that execute adapters and build diagnostics.

    The returned tuple contains:

    * ``fan_out`` - dispatches the event to configured adapters and returns the
      list of adapter names that failed.
    * ``finalise`` - wraps ``fan_out`` so callers receive the diagnostic payload
      expected by ``create_process_log_event``.
    """

    def fan_out(event: LogEvent) -> list[str]:
        """Dispatch event to adapters, returning names of any that failed."""
        failed: list[str] = []

        def _safe_emit(callable_: Callable[[], None], adapter_name: str) -> None:
            try:
                callable_()
            except Exception as exc:
                logger.exception(
                    "Adapter %s failed while emitting event %s: %s",
                    adapter_name,
                    event.event_id,
                    exc,
                )
                failed.append(adapter_name)
                emit(
                    DiagnosticEvent.ADAPTER_ERROR,
                    {
                        "adapter": adapter_name,
                        "event_id": event.event_id,
                        "logger": event.logger_name,
                        "level": event.level.name,
                        "error": str(exc),
                    },
                )

        if event.level >= console_level:
            _safe_emit(lambda: console.emit(event, colorize=colorize_console), console.__class__.__name__)

        if event.level >= backend_level:
            for backend in structured_backends:
                _safe_emit(lambda backend=backend: backend.emit(event), backend.__class__.__name__)

        if graylog is not None and event.level >= graylog_level:
            graylog_adapter = graylog
            _safe_emit(lambda: graylog_adapter.emit(event), graylog_adapter.__class__.__name__)

        return failed

    def finalise(event: LogEvent) -> ProcessResult:
        """Fan out and return ProcessResult with success/failure status."""
        failed_adapters = fan_out(event)
        if failed_adapters:
            return report_adapter_failure(emit, event, failed_adapters)

        emit(
            DiagnosticEvent.EMITTED,
            {
                "event_id": event.event_id,
                "logger": event.logger_name,
                "level": event.level.name,
            },
        )
        return ProcessResult(ok=True, event_id=event.event_id)

    return fan_out, finalise


def report_adapter_failure(emit: DiagnosticEmitter, event: LogEvent, failed_adapters: list[str]) -> ProcessResult:
    """Send the once-per-event ``adapter_error`` diagnostic and return the failed result.

    Shared by the inline path (``finalise``) and the queue worker, so a failure
    is reported with the same payload whichever path delivered the event.
    """
    emit(
        DiagnosticEvent.ADAPTER_ERROR,
        {
            "event_id": event.event_id,
            "logger": event.logger_name,
            "level": event.level.name,
            "adapters": failed_adapters,
        },
    )
    return ProcessResult(
        ok=False,
        reason=DropReason.ADAPTER_ERROR,
        event_id=event.event_id,
        failed_adapters=failed_adapters,
    )


__all__ = ["FanOutResultHandler", "build_fan_out_handlers", "report_adapter_failure"]
