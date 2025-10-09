"""Runtime composition helpers wiring domain, application, and adapters."""

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
from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer

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


__all__ = ["LoggerProxy", "build_runtime", "coerce_level"]


def build_runtime(settings: RuntimeSettings) -> LoggingRuntime:
    """Assemble the logging runtime from resolved settings."""

    identity_provider = SystemIdentityProvider()
    binder = create_runtime_binder(settings.service, settings.environment, identity_provider)
    ring_buffer = create_ring_buffer(settings.flags.ring_buffer, settings.ring_buffer_size)
    if settings.console_factory is not None:
        console = settings.console_factory(settings.console)
    else:
        console = create_console(settings.console)
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

    capture_dump = create_dump_renderer(
        ring_buffer=ring_buffer,
        dump_defaults=settings.dump,
        theme=settings.console.theme,
        console_styles=settings.console.styles,
    )

    shutdown_async = create_shutdown(
        queue=queue,
        graylog=graylog_adapter,
        ring_buffer=ring_buffer if settings.flags.ring_buffer else None,
    )

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
        theme=settings.console.theme,
        console_styles=settings.console.styles,
        limits=settings.limits,
    )


def _build_process_pipeline(
    *,
    binder: ContextBinder,
    ring_buffer: RingBuffer,
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

    def _make(queue: QueueAdapter | None) -> ProcessCallable:
        return create_process_log_event(
            context_binder=binder,
            ring_buffer=ring_buffer,
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

    process = _make(queue=None)
    queue: QueueAdapter | None = None

    if queue_enabled:
        queue = QueueAdapter(
            worker=_fan_out_callable(process),
            maxsize=queue_maxsize,
            drop_policy=queue_policy,
            timeout=queue_timeout,
            stop_timeout=queue_stop_timeout,
            diagnostic=diagnostic,
        )
        queue.start()
        process = _make(queue=queue)
        queue.set_worker(_fan_out_callable(process))
    return process, queue


def _fan_out_callable(process: ProcessCallable) -> Callable[[LogEvent], None]:
    """Extract the fan-out helper exposed by the process use case."""

    worker: FanOutCallable = process.fan_out

    def _worker(event: LogEvent) -> None:
        worker(event)

    return _worker
