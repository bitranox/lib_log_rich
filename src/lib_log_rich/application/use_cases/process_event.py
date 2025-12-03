"""Use case orchestrating the processing pipeline for a single log event.

Purpose
-------
Tie together context binding, ring buffer persistence, scrubbing, rate limiting,
and adapter fan-out as described in ``concept_architecture_plan.md``.

Contents
--------
* Helper functions for context management and fan-out.
* :func:`create_process_log_event` factory returning the runtime callable.

System Role
-----------
Application-layer orchestrator invoked by :func:`lib_log_rich.init` to turn the
configured dependencies into a callable logging pipeline.

Alignment Notes
---------------
Terminology and diagnostics align with ``docs/systemdesign/module_reference.md``
so that emitted payloads and observability hooks remain traceable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lib_log_rich.application.ports import (
    ClockPort,
    IdProvider,
    RateLimiterPort,
    ScrubberPort,
    SystemIdentityPort,
)
from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer, SeverityMonitor

if TYPE_CHECKING:
    from lib_log_rich.application.ports import QueuePort

from ._fan_out import build_fan_out_handlers
from ._payload_sanitizer import PayloadLimitsProtocol, PayloadSanitizer
from ._pipeline import build_diagnostic_emitter, prepare_event, refresh_context
from ._queue_dispatch import build_queue_dispatcher
from ._types import DiagnosticCallback, FanOutCallable, ProcessCallable, ProcessPipelineDependencies, ProcessResult

logger = logging.getLogger(__name__)


def create_process_log_event(dependencies: ProcessPipelineDependencies) -> ProcessCallable:
    """Build the orchestrator capturing the current dependency wiring.

    The composition root assembles a different set of adapters depending on
    configuration (e.g., queue vs. inline mode). This factory freezes those
    decisions into an efficient callable executed for every log event.

    Args:
        dependencies: :class:`ProcessPipelineDependencies` bundle describing the
            runtime collaborators, thresholds, diagnostics, and optional queue
            wiring.

    Returns:
        Function accepting ``logger_name``, ``level``, the unformatted ``message``
        and ``args`` pair, optional ``exc_info``/``stack_info`` payloads,
        ``stacklevel`` (accepted for API parity but currently ignored), and
        optional ``extra`` metadata. The callable returns a diagnostic
        dictionary describing delivery outcome.

    Note:
        End-to-end usage, including queue-enabled and inline variants, lives in
        ``tests/application/test_use_cases.py``. That suite shows how to assemble
        :class:`ProcessPipelineDependencies`, invoke the resulting callable, and
        assert on diagnostics without embedding large doctests here.

    """
    toolkit = _build_toolkit_from_dependencies(dependencies, logger)
    return _build_process_callable(toolkit)


def _build_toolkit_from_dependencies(dependencies: ProcessPipelineDependencies, logger: logging.Logger) -> _PipelineToolkit:
    emit = _create_diagnostic_emitter(dependencies.diagnostic)
    sanitizer = _create_sanitizer(dependencies.limits, emit)
    queue_dispatch = _create_queue_dispatcher(dependencies.queue, emit)
    fan_out_callable, finalize_fan_out = build_fan_out_handlers(
        console=dependencies.console,
        console_level=dependencies.console_level,
        structured_backends=dependencies.structured_backends,
        backend_level=dependencies.backend_level,
        graylog=dependencies.graylog,
        graylog_level=dependencies.graylog_level,
        emit=emit,
        colorize_console=dependencies.colorize_console,
        logger=logger,
    )

    return _PipelineToolkit(
        context_binder=dependencies.context_binder,
        ring_buffer=dependencies.ring_buffer,
        severity_monitor=dependencies.severity_monitor,
        scrubber=dependencies.scrubber,
        rate_limiter=dependencies.rate_limiter,
        queue_dispatch=queue_dispatch,
        finalize_fan_out=finalize_fan_out,
        sanitizer=sanitizer,
        emit=emit,
        clock=dependencies.clock,
        id_provider=dependencies.id_provider,
        identity=dependencies.identity,
        fan_out=fan_out_callable,
    )


@dataclass(frozen=True)
class _PipelineToolkit:
    """Internal bundle of collaborators for the processing pipeline."""

    context_binder: ContextBinder
    ring_buffer: RingBuffer
    severity_monitor: SeverityMonitor
    scrubber: ScrubberPort
    rate_limiter: RateLimiterPort
    queue_dispatch: Callable[[LogEvent], ProcessResult | None]
    finalize_fan_out: Callable[[LogEvent], ProcessResult]
    sanitizer: PayloadSanitizer
    emit: DiagnosticCallback
    clock: ClockPort
    id_provider: IdProvider
    identity: SystemIdentityPort
    fan_out: FanOutCallable


def _build_process_callable(toolkit: _PipelineToolkit) -> ProcessCallable:
    pipeline = _ProcessPipeline(toolkit)
    return pipeline


class _ProcessPipeline(ProcessCallable):
    """Callable that processes log events through the full pipeline."""

    fan_out: FanOutCallable

    def __init__(self, toolkit: _PipelineToolkit) -> None:
        """Initialize with the pipeline toolkit."""
        self._toolkit = toolkit
        self.fan_out = toolkit.fan_out

    def __call__(
        self,
        *,
        logger_name: str,
        level: LogLevel,
        message: object,
        args: tuple[object, ...] = (),
        exc_info: object | None = None,
        stack_info: object | None = None,
        stacklevel: int = 1,
        extra: Mapping[str, Any] | None = None,
    ) -> ProcessResult:
        """Process a log event through scrubbing, rate limiting, and fan-out."""
        event = _craft_event(
            self._toolkit,
            logger_name,
            level,
            message,
            args,
            exc_info,
            stack_info,
            stacklevel,
            extra,
        )
        event = _scrub_event(self._toolkit, event)
        if not _rate_limiter_allows(self._toolkit, event):
            return _reject_due_to_rate_limit(self._toolkit, event)
        _remember_event(self._toolkit, event)
        queue_answer = _offer_event_to_queue(self._toolkit, event)
        if queue_answer is not None:
            return _interpret_queue_outcome(self._toolkit, event, queue_answer)
        return _fan_out_event(self._toolkit, event)


def _create_diagnostic_emitter(
    diagnostic: DiagnosticCallback | None,
) -> DiagnosticCallback:
    return build_diagnostic_emitter(diagnostic)


def _create_sanitizer(
    limits: PayloadLimitsProtocol,
    emit: DiagnosticCallback,
) -> PayloadSanitizer:
    return PayloadSanitizer(limits, emit)


def _create_queue_dispatcher(
    queue: QueuePort | None,
    emit: DiagnosticCallback,
) -> Callable[[LogEvent], ProcessResult | None]:
    return build_queue_dispatcher(queue, emit)


def _craft_event(
    toolkit: _PipelineToolkit,
    logger_name: str,
    level: LogLevel,
    message: object,
    args: tuple[object, ...],
    exc_info: object | None,
    stack_info: object | None,
    stacklevel: int,
    extra: Mapping[str, Any] | None,
) -> LogEvent:
    event_id = toolkit.id_provider()
    return prepare_event(
        event_id=event_id,
        logger_name=logger_name,
        level=level,
        message=message,
        args=args,
        exc_info=exc_info,
        stack_info=stack_info,
        stacklevel=stacklevel,
        extra=extra,
        context_binder=toolkit.context_binder,
        identity=toolkit.identity,
        sanitizer=toolkit.sanitizer,
        clock=toolkit.clock,
        emit=toolkit.emit,
    )


def _scrub_event(toolkit: _PipelineToolkit, event: LogEvent) -> LogEvent:
    return toolkit.scrubber.scrub(event)


def _rate_limiter_allows(toolkit: _PipelineToolkit, event: LogEvent) -> bool:
    return toolkit.rate_limiter.allow(event)


def _reject_due_to_rate_limit(toolkit: _PipelineToolkit, event: LogEvent) -> ProcessResult:
    toolkit.severity_monitor.record_drop(event.level, "rate_limited")
    toolkit.emit(
        "rate_limited",
        {"event_id": event.event_id, "logger": event.logger_name, "level": event.level.name},
    )
    return ProcessResult(ok=False, reason="rate_limited", event_id=event.event_id)


def _remember_event(toolkit: _PipelineToolkit, event: LogEvent) -> None:
    toolkit.ring_buffer.append(event)
    toolkit.severity_monitor.record(event.level)


def _offer_event_to_queue(
    toolkit: _PipelineToolkit,
    event: LogEvent,
) -> ProcessResult | None:
    return toolkit.queue_dispatch(event)


def _interpret_queue_outcome(
    toolkit: _PipelineToolkit,
    event: LogEvent,
    queue_answer: ProcessResult,
) -> ProcessResult:
    if not queue_answer.ok:
        reason = queue_answer.reason or "queue_failure"
        toolkit.severity_monitor.record_drop(event.level, reason)
    return queue_answer


def _fan_out_event(toolkit: _PipelineToolkit, event: LogEvent) -> ProcessResult:
    outcome = toolkit.finalize_fan_out(event)
    if not outcome.ok:
        reason = outcome.reason or "adapter_error"
        toolkit.severity_monitor.record_drop(event.level, reason)
    return outcome


__all__ = ["create_process_log_event", "refresh_context"]
