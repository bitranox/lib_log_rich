"""Runtime composition helpers wiring domain, application, and adapters."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from lib_log_rich.adapters import GraylogAdapter, QueueAdapter, RegexScrubber
from lib_log_rich.application.ports import ClockPort, ConsolePort, IdProvider, RateLimiterPort, StructuredBackendPort
from lib_log_rich.application.use_cases.process_event import create_process_log_event
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
)
from ._settings import DiagnosticHook, RuntimeSettings
from ._state import LoggingRuntime


__all__ = ["LoggerProxy", "build_runtime", "coerce_level"]


def build_runtime(settings: RuntimeSettings) -> LoggingRuntime:
    """Assemble the logging runtime from resolved settings."""

    binder = create_runtime_binder(settings.service, settings.environment)
    ring_buffer = create_ring_buffer(settings.flags.ring_buffer, settings.ring_buffer_size)
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
) -> tuple[Callable[..., dict[str, Any]], QueueAdapter | None]:
    """Construct the log-processing callable and optional queue adapter."""

    def _make(queue: QueueAdapter | None) -> Callable[..., dict[str, Any]]:
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
        )

    process = _make(queue=None)
    queue: QueueAdapter | None = None

    drop_handler_fn: Callable[[LogEvent], None] | None = None
    if diagnostic is not None:

        def _handle_queue_drop(event: LogEvent) -> None:
            diagnostic(
                "queue_dropped",
                {"event_id": event.event_id, "logger": event.logger_name, "level": event.level.name},
            )

        drop_handler_fn = _handle_queue_drop

    if queue_enabled:
        queue = QueueAdapter(
            worker=_fan_out_callable(process),
            maxsize=queue_maxsize,
            drop_policy=queue_policy,
            on_drop=drop_handler_fn,
            timeout=queue_timeout,
            stop_timeout=queue_stop_timeout,
        )
        queue.start()
        process = _make(queue=queue)
        queue.set_worker(_fan_out_callable(process))
    return process, queue


def _fan_out_callable(process: Callable[..., dict[str, Any]]) -> Callable[[LogEvent], None]:
    """Extract the fan-out helper exposed by the process use case."""

    try:
        worker = getattr(process, "fan_out")
    except AttributeError as exc:  # pragma: no cover - defensive guard
        raise AttributeError("Process use case missing fan_out helper") from exc

    def _worker(event: LogEvent) -> None:
        worker(event)

    return _worker
