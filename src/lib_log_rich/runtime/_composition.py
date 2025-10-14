"""Runtime composition helpers wiring domain, application, and adapters.

Purpose
-------
Translate ``RuntimeSettings`` into the live ``LoggingRuntime`` singleton
referenced throughout the system design docs. The helpers here keep wiring
small, declarative, and testable.

Contents
--------
* Adapter selection (console, structured backends, Graylog).
* Process pipeline constructors with optional queue fan-out.
* Shutdown/dump helpers mirroring the runtime façade API.

System Role
-----------
Anchors the clean-architecture boundary: outer adapters live here, while
``lib_log_rich.runtime`` exposes only the façade documented in
``docs/systemdesign/module_reference.md``."""

from __future__ import annotations

from typing import Callable, Sequence

from lib_log_rich.adapters import GraylogAdapter, QueueAdapter, RegexScrubber
from lib_log_rich.application.ports import (
    ClockPort,
    ConsolePort,
    IdProvider,
    RateLimiterPort,
    StructuredBackendPort,
    SystemIdentityPort,
)
from lib_log_rich.application.use_cases.process_event import create_process_log_event
from lib_log_rich.application.use_cases._types import FanOutCallable, ProcessCallable
from lib_log_rich.application.use_cases.shutdown import create_shutdown
from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer, SeverityMonitor

from ._factories import (
    LoggerProxy,
    SystemClock,
    UuidProvider,
    coerce_level,
    create_console,
    create_dump_renderer,
    create_graylog_adapter,
    create_rate_limiter,
    create_ring_buffer,
    create_runtime_binder,
    create_scrubber,
    create_structured_backends,
    compute_thresholds,
    SystemIdentityProvider,
)
from ._settings import DiagnosticHook, PayloadLimits, RuntimeSettings
from ._state import LoggingRuntime


DROP_REASON_LABELS: tuple[str, str, str] = (
    "rate_limited",
    "queue_full",
    "adapter_error",
)
"""Stable drop-reason labels shared with docs/systemdesign/module_reference.md."""


__all__ = ["LoggerProxy", "build_runtime", "coerce_level"]


def build_runtime(settings: RuntimeSettings) -> LoggingRuntime:
    """Assemble the logging runtime from resolved settings."""

    identity_provider = SystemIdentityProvider()
    binder = create_runtime_binder(settings.service, settings.environment, identity_provider)
    severity_monitor = _create_severity_monitor()
    ring_buffer = create_ring_buffer(settings.flags.ring_buffer, settings.ring_buffer_size)
    console = _select_console_adapter(settings)
    structured_backends = create_structured_backends(settings.flags)
    graylog_adapter = create_graylog_adapter(settings.graylog)
    console_level, backend_level, graylog_level = compute_thresholds(settings, graylog_adapter)
    scrubber = create_scrubber(settings.scrub_patterns)
    limiter = create_rate_limiter(settings.rate_limit)
    clock: ClockPort = SystemClock()
    id_provider: IdProvider = UuidProvider()

    process, queue = _build_process_pipeline(
        binder=binder,
        ring_buffer=ring_buffer,
        severity_monitor=severity_monitor,
        console=console,
        console_level=console_level,
        structured_backends=structured_backends,
        backend_level=backend_level,
        graylog=graylog_adapter,
        graylog_level=graylog_level,
        scrubber=scrubber,
        rate_limiter=limiter,
        clock=clock,
        id_provider=id_provider,
        queue_enabled=settings.flags.queue,
        queue_maxsize=settings.queue_maxsize,
        queue_policy=settings.queue_full_policy,
        queue_timeout=settings.queue_put_timeout,
        queue_stop_timeout=settings.queue_stop_timeout,
        diagnostic=settings.diagnostic_hook,
        limits=settings.limits,
        identity_provider=identity_provider,
    )

    capture_dump = _create_dump_capture(ring_buffer, settings)
    shutdown_async = _create_shutdown_callable(queue, graylog_adapter, ring_buffer, settings)

    return LoggingRuntime(
        binder=binder,
        process=process,
        capture_dump=capture_dump,
        shutdown_async=shutdown_async,
        queue=queue,
        service=settings.service,
        environment=settings.environment,
        console_level=console_level,
        backend_level=backend_level,
        graylog_level=graylog_level,
        severity_monitor=severity_monitor,
        theme=settings.console.theme,
        console_styles=settings.console.styles,
        limits=settings.limits,
    )


def _create_severity_monitor() -> SeverityMonitor:
    """Build the shared severity monitor seeded with documented drop reasons.

    Why:
        Observability dashboards rely on the labels in ``DROP_REASON_LABELS`` to
        chart rate limiting, queue back pressure, and adapter failures. Exposing
        them here keeps runtime wiring consistent with ``docs/systemdesign``.
    Returns:
        SeverityMonitor: Instance with stable drop-reason labels so call sites
        and docs stay aligned.
    """

    return SeverityMonitor(drop_reasons=DROP_REASON_LABELS)


def _select_console_adapter(settings: RuntimeSettings) -> ConsolePort:
    """Resolve the console adapter abiding by the clean-architecture boundary.

    Why:
        Hosts may inject a bespoke console via ``console_factory``. Falling back
        to ``create_console`` keeps adapter selection consistent with the
        defaults documented in the system design without leaking Rich specifics
        into callers.
    Returns:
        ConsolePort: Concrete adapter chosen either from caller injection or the
        default factory.
    """

    if settings.console_factory is not None:
        return settings.console_factory(settings.console)
    return create_console(settings.console)


def _create_dump_capture(ring_buffer: RingBuffer, settings: RuntimeSettings) -> Callable[..., str]:
    """Bind dump collaborators into the runtime capture callable.

    Why:
        The runtime façade delegates to a single callable that honours dump
        defaults, theming, and style overrides. Centralising the wiring keeps
        the behaviour aligned with ``docs/systemdesign/module_reference.md`` and
        allows tests to swap in fakes.
    Returns:
        Callable[..., str]: Prepared renderer that snapshots the ring buffer and
        applies the correct formatting contract.
    """

    return create_dump_renderer(
        ring_buffer=ring_buffer,
        dump_defaults=settings.dump,
        theme=settings.console.theme,
        console_styles=settings.console.styles,
    )


def _create_shutdown_callable(
    queue: QueueAdapter | None,
    graylog: GraylogAdapter | None,
    ring_buffer: RingBuffer,
    settings: RuntimeSettings,
):
    """Construct the asynchronous shutdown hook for the runtime.

    Why:
        Shutdown must flush queues and ring-buffer snapshots exactly as the
        system design promises. Building the hook here keeps lifecycle
        orchestration declarative and testable.
    Returns:
        Awaitable | None: Callable mirroring the application use case contract
        produced by ``create_shutdown``.
    """

    ring_buffer_target = ring_buffer if settings.flags.ring_buffer else None
    return create_shutdown(queue=queue, graylog=graylog, ring_buffer=ring_buffer_target)


def _create_process_callable(
    *,
    binder: ContextBinder,
    ring_buffer: RingBuffer,
    severity_monitor: SeverityMonitor,
    console: ConsolePort,
    console_level: LogLevel,
    structured_backends: Sequence[StructuredBackendPort],
    backend_level: LogLevel,
    graylog: GraylogAdapter | None,
    graylog_level: LogLevel,
    scrubber: RegexScrubber,
    rate_limiter: RateLimiterPort,
    clock: ClockPort,
    id_provider: IdProvider,
    queue: QueueAdapter | None,
    diagnostic: DiagnosticHook,
    limits: PayloadLimits,
    identity_provider: SystemIdentityPort,
) -> ProcessCallable:
    """Create the log-processing use case with explicit dependencies.

    Why:
        Keeps queue/no-queue variants declarative and testable while mirroring
        the orchestration diagram in ``docs/systemdesign/module_reference.md``.
    Returns:
        ProcessCallable: Application-layer callable that performs binding,
        filtering, scrubbing, fan-out, and diagnostics.
    Side Effects:
        Mutates severity counters and writes to configured queues/backends when
        invoked.
    """

    return create_process_log_event(
        context_binder=binder,
        ring_buffer=ring_buffer,
        severity_monitor=severity_monitor,
        console=console,
        console_level=console_level,
        structured_backends=structured_backends,
        backend_level=backend_level,
        graylog=graylog,
        graylog_level=graylog_level,
        scrubber=scrubber,
        rate_limiter=rate_limiter,
        clock=clock,
        id_provider=id_provider,
        queue=queue,
        diagnostic=diagnostic,
        limits=limits,
        identity=identity_provider,
    )


def _create_queue_adapter(
    *,
    seed_process: ProcessCallable,
    maxsize: int,
    drop_policy: str,
    timeout: float | None,
    stop_timeout: float | None,
    diagnostic: DiagnosticHook,
) -> QueueAdapter:
    """Instantiate the queue adapter that fans out log events.

    Why:
        Queue configuration (size, policy, timeouts) forms part of the runtime
        contract; concentrating creation here keeps the behaviour aligned with
        the system design and simplifies diagnostics.
    Returns:
        QueueAdapter: Worker-ready adapter that bridges the synchronous process
        callable into the asynchronous queue pipeline.
    Side Effects:
        The returned adapter starts a worker thread once ``start()`` is invoked.
    """

    return QueueAdapter(
        worker=_fan_out_callable(seed_process),
        maxsize=maxsize,
        drop_policy=drop_policy,
        timeout=timeout,
        stop_timeout=stop_timeout,
        diagnostic=diagnostic,
    )


def _build_process_pipeline(
    *,
    binder: ContextBinder,
    ring_buffer: RingBuffer,
    severity_monitor: SeverityMonitor,
    console: ConsolePort,
    console_level: LogLevel,
    structured_backends: Sequence[StructuredBackendPort],
    backend_level: LogLevel,
    graylog: GraylogAdapter | None,
    graylog_level: LogLevel,
    scrubber: RegexScrubber,
    rate_limiter: RateLimiterPort,
    clock: ClockPort,
    id_provider: IdProvider,
    queue_enabled: bool,
    queue_maxsize: int,
    queue_policy: str,
    queue_timeout: float | None,
    queue_stop_timeout: float | None,
    diagnostic: DiagnosticHook,
    limits: PayloadLimits,
    identity_provider: SystemIdentityPort,
) -> tuple[ProcessCallable, QueueAdapter | None]:
    """Construct the log-processing callable and optional queue adapter."""

    process_without_queue = _create_process_callable(
        binder=binder,
        ring_buffer=ring_buffer,
        severity_monitor=severity_monitor,
        console=console,
        console_level=console_level,
        structured_backends=structured_backends,
        backend_level=backend_level,
        graylog=graylog,
        graylog_level=graylog_level,
        scrubber=scrubber,
        rate_limiter=rate_limiter,
        clock=clock,
        id_provider=id_provider,
        queue=None,
        diagnostic=diagnostic,
        limits=limits,
        identity_provider=identity_provider,
    )

    if not queue_enabled:
        return process_without_queue, None

    queue = _create_queue_adapter(
        seed_process=process_without_queue,
        maxsize=queue_maxsize,
        drop_policy=queue_policy,
        timeout=queue_timeout,
        stop_timeout=queue_stop_timeout,
        diagnostic=diagnostic,
    )
    queue.start()

    process_with_queue = _create_process_callable(
        binder=binder,
        ring_buffer=ring_buffer,
        severity_monitor=severity_monitor,
        console=console,
        console_level=console_level,
        structured_backends=structured_backends,
        backend_level=backend_level,
        graylog=graylog,
        graylog_level=graylog_level,
        scrubber=scrubber,
        rate_limiter=rate_limiter,
        clock=clock,
        id_provider=id_provider,
        queue=queue,
        diagnostic=diagnostic,
        limits=limits,
        identity_provider=identity_provider,
    )
    queue.set_worker(_fan_out_callable(process_with_queue))
    return process_with_queue, queue


def _fan_out_callable(process: ProcessCallable) -> Callable[[LogEvent], None]:
    """Extract the fan-out helper exposed by the process use case."""

    worker: FanOutCallable = process.fan_out

    def _worker(event: LogEvent) -> None:
        worker(event)

    return _worker
