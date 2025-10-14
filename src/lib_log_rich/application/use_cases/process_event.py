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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer, SeverityMonitor

from lib_log_rich.application.ports import (
    ClockPort,
    ConsolePort,
    GraylogPort,
    IdProvider,
    QueuePort,
    RateLimiterPort,
    ScrubberPort,
    StructuredBackendPort,
    SystemIdentityPort,
)

from ._fan_out import build_fan_out_handlers
from ._payload_sanitizer import PayloadLimitsProtocol, PayloadSanitizer
from ._pipeline import build_diagnostic_emitter, prepare_event, refresh_context
from ._queue_dispatch import build_queue_dispatcher
from ._types import FanOutCallable, ProcessCallable, ProcessResult

logger = logging.getLogger(__name__)


def create_process_log_event(
    *,
    context_binder: ContextBinder,
    ring_buffer: RingBuffer,
    severity_monitor: SeverityMonitor,
    console: ConsolePort,
    console_level: LogLevel,
    structured_backends: Sequence[StructuredBackendPort],
    backend_level: LogLevel,
    graylog: GraylogPort | None,
    graylog_level: LogLevel,
    scrubber: ScrubberPort,
    rate_limiter: RateLimiterPort,
    clock: ClockPort,
    id_provider: IdProvider,
    queue: QueuePort | None,
    limits: PayloadLimitsProtocol,
    colorize_console: bool = True,
    diagnostic: Callable[[str, dict[str, Any]], None] | None = None,
    identity: SystemIdentityPort,
) -> ProcessCallable:
    """Build the orchestrator capturing the current dependency wiring.

    Why
    ---
    The composition root assembles a different set of adapters depending on
    configuration (e.g., queue vs. inline mode). This factory freezes those
    decisions into an efficient callable executed for every log event.

    Parameters
    ----------
    context_binder:
        Shared :class:`ContextBinder` supplying contextual metadata.
    ring_buffer:
        :class:`RingBuffer` capturing recent events for dumps.
    severity_monitor:
        :class:`SeverityMonitor` tracking aggregate severities for the
        active runtime.
    console:
        Console adapter implementing :class:`ConsolePort`.
    console_level:
        Minimum level required for console emission.
    structured_backends:
        Sequence of adapters emitting to journald/EventLog/etc.
    backend_level:
        Minimum level required for structured backends.
    graylog:
        Optional Graylog adapter; ``None`` disables Graylog fan-out.
    graylog_level:
        Minimum level for Graylog emission.
    scrubber:
        Adapter implementing :class:`ScrubberPort` for sensitive-field masking.
    rate_limiter:
        Adapter controlling throughput before fan-out.
    clock:
        Provider of timezone-aware timestamps.
    id_provider:
        Callable returning unique event identifiers.
    queue:
        Optional :class:`QueuePort` enabling asynchronous fan-out.
    identity:
        Adapter implementing :class:`SystemIdentityPort` supplying refreshed
        system/user metadata for context propagation.
    colorize_console:
        When ``False`` the console adapter renders without colour.
    diagnostic:
        Optional callback invoked with pipeline milestones.
    limits:
        Boundaries applied to messages, extras, context metadata, and stack traces.

    Returns
    -------
    Callable[[str, LogLevel, str, dict[str, Any] | None], dict[str, Any]]
        Function accepting ``logger_name``, ``level``, ``message``, and optional
        ``extra`` metadata, returning a diagnostic dictionary.

    Examples
    --------
    >>> class DummyConsole(ConsolePort):
    ...     def __init__(self):
    ...         self.events = []
    ...     def emit(self, event: LogEvent, *, colorize: bool) -> None:
    ...         self.events.append((event.logger_name, colorize))
    >>> class DummyBackend(StructuredBackendPort):
    ...     def __init__(self):
    ...         self.events = []
    ...     def emit(self, event: LogEvent) -> None:
    ...         self.events.append(event.logger_name)
    >>> class DummyQueue(QueuePort):
    ...     def __init__(self):
    ...         self.events = []
    ...     def put(self, event: LogEvent) -> None:
    ...         self.events.append(event.logger_name)
    >>> class DummyClock(ClockPort):
    ...     def now(self):
    ...         from datetime import datetime, timezone
    ...         return datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc)
    >>> class DummyId(IdProvider):
    ...     def __call__(self) -> str:
    ...         return 'event-1'
    >>> class DummyScrubber(ScrubberPort):
    ...     def scrub(self, event: LogEvent) -> LogEvent:
    ...         return event
    >>> from lib_log_rich.domain.identity import SystemIdentity
    >>> class DummyIdentity(SystemIdentityPort):
    ...     def __init__(self) -> None:
    ...         self._identity = SystemIdentity(user_name='svc-user', hostname='svc-host', process_id=4321)
    ...     def resolve_identity(self) -> SystemIdentity:
    ...         return self._identity
    >>> class DummyLimiter(RateLimiterPort):
    ...     def allow(self, event: LogEvent) -> bool:
    ...         return True
    >>> class DummyLimits:
    ...     truncate_message = True
    ...     message_max_chars = 4096
    ...     extra_max_keys = 25
    ...     extra_max_value_chars = 512
    ...     extra_max_depth = 3
    ...     extra_max_total_bytes = 8192
    ...     context_max_keys = 20
    ...     context_max_value_chars = 256
    ...     stacktrace_max_frames = 10
    >>> binder = ContextBinder()
    >>> ring = RingBuffer(max_events=10)
    >>> monitor = SeverityMonitor()
    >>> console_adapter = DummyConsole()
    >>> backend_adapter = DummyBackend()
    >>> with binder.bind(service='svc', environment='prod', job_id='1'):
    ...     process = create_process_log_event(
    ...         context_binder=binder,
    ...         ring_buffer=ring,
    ...         severity_monitor=monitor,
    ...         console=console_adapter,
    ...         console_level=LogLevel.DEBUG,
    ...         structured_backends=[backend_adapter],
    ...         backend_level=LogLevel.INFO,
    ...         graylog=None,
    ...         graylog_level=LogLevel.ERROR,
    ...         scrubber=DummyScrubber(),
    ...         rate_limiter=DummyLimiter(),
    ...         clock=DummyClock(),
    ...         id_provider=DummyId(),
    ...         queue=None,
    ...         limits=DummyLimits(),
    ...         colorize_console=True,
    ...         diagnostic=None,
    ...         identity=DummyIdentity(),
    ...     )
    ...     result = process(logger_name='svc.worker', level=LogLevel.INFO, message='hello', extra=None)
    >>> result['ok'] and result['event_id'] == 'event-1'
    True
    >>> len(ring)
    1
    >>> console_adapter.events[0][0]
    'svc.worker'
    >>> backend_adapter.events[0]
    'svc.worker'
    """

    emit = _create_diagnostic_emitter(diagnostic)
    sanitizer = _create_sanitizer(limits, emit)
    queue_dispatch = _create_queue_dispatcher(queue, emit)
    fan_out_callable, finalize_fan_out = build_fan_out_handlers(
        console=console,
        console_level=console_level,
        structured_backends=structured_backends,
        backend_level=backend_level,
        graylog=graylog,
        graylog_level=graylog_level,
        emit=emit,
        colorize_console=colorize_console,
        logger=logger,
    )

    toolkit = _PipelineToolkit(
        context_binder=context_binder,
        ring_buffer=ring_buffer,
        severity_monitor=severity_monitor,
        scrubber=scrubber,
        rate_limiter=rate_limiter,
        queue_dispatch=queue_dispatch,
        finalize_fan_out=finalize_fan_out,
        sanitizer=sanitizer,
        emit=emit,
        clock=clock,
        id_provider=id_provider,
        identity=identity,
        fan_out=fan_out_callable,
    )
    return _build_process_callable(toolkit)


@dataclass(frozen=True)
class _PipelineToolkit:
    context_binder: ContextBinder
    ring_buffer: RingBuffer
    severity_monitor: SeverityMonitor
    scrubber: ScrubberPort
    rate_limiter: RateLimiterPort
    queue_dispatch: Callable[[LogEvent], ProcessResult | None]
    finalize_fan_out: Callable[[LogEvent], ProcessResult]
    sanitizer: PayloadSanitizer
    emit: Callable[[str, dict[str, Any]], None]
    clock: ClockPort
    id_provider: IdProvider
    identity: SystemIdentityPort
    fan_out: FanOutCallable


def _build_process_callable(toolkit: _PipelineToolkit) -> ProcessCallable:
    pipeline = _ProcessPipeline(toolkit)
    return pipeline


class _ProcessPipeline(ProcessCallable):
    fan_out: FanOutCallable

    def __init__(self, toolkit: _PipelineToolkit) -> None:
        self._toolkit = toolkit
        self.fan_out = toolkit.fan_out

    def __call__(
        self,
        *,
        logger_name: str,
        level: LogLevel,
        message: str,
        extra: Mapping[str, Any] | None = None,
    ) -> ProcessResult:
        event = _craft_event(self._toolkit, logger_name, level, message, extra)
        event = _scrub_event(self._toolkit, event)
        if not _rate_limiter_allows(self._toolkit, event):
            return _reject_due_to_rate_limit(self._toolkit, event)
        _remember_event(self._toolkit, event)
        queue_answer = _offer_event_to_queue(self._toolkit, event)
        if queue_answer is not None:
            return _interpret_queue_outcome(self._toolkit, event, queue_answer)
        return _fan_out_event(self._toolkit, event)


def _create_diagnostic_emitter(
    diagnostic: Callable[[str, dict[str, Any]], None] | None,
) -> Callable[[str, dict[str, Any]], None]:
    return build_diagnostic_emitter(diagnostic)


def _create_sanitizer(
    limits: PayloadLimitsProtocol,
    emit: Callable[[str, dict[str, Any]], None],
) -> PayloadSanitizer:
    return PayloadSanitizer(limits, emit)


def _create_queue_dispatcher(
    queue: QueuePort | None,
    emit: Callable[[str, dict[str, Any]], None],
) -> Callable[[LogEvent], ProcessResult | None]:
    return build_queue_dispatcher(queue, emit)


def _craft_event(
    toolkit: _PipelineToolkit,
    logger_name: str,
    level: LogLevel,
    message: str,
    extra: Mapping[str, Any] | None,
) -> LogEvent:
    event_id = toolkit.id_provider()
    return prepare_event(
        event_id=event_id,
        logger_name=logger_name,
        level=level,
        message=message,
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
    return {"ok": False, "reason": "rate_limited"}


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
    if not queue_answer.get("ok", False):
        reason = _reason_from(queue_answer, default="queue_failure")
        toolkit.severity_monitor.record_drop(event.level, reason)
    return queue_answer


def _fan_out_event(toolkit: _PipelineToolkit, event: LogEvent) -> ProcessResult:
    outcome = toolkit.finalize_fan_out(event)
    if not outcome.get("ok", False):
        reason = _reason_from(outcome, default="adapter_error")
        toolkit.severity_monitor.record_drop(event.level, reason)
    return outcome


def _reason_from(result: ProcessResult, *, default: str) -> str:
    reason = result.get("reason")
    return reason if isinstance(reason, str) else default


__all__ = ["create_process_log_event", "refresh_context"]
