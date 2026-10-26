"""Runtime API surface for lib_log_rich.

This module hosts the high-level functions exposed by :mod:`lib_log_rich.runtime`.
Breaking the implementation out of ``__init__`` keeps the public façade thin and
focused.
"""

from __future__ import annotations

import asyncio
import inspect
import io
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar

from lib_log_rich.domain import DumpFilter, DumpFormat, LogLevel, build_dump_filter
from lib_log_rich.domain.enums import ConsoleTheme, FormatPreset, require_enum_member

from ._composition import build_runtime
from ._factories import LoggerProxy
from ._state import (
    LoggingRuntime,
    clear_runtime,
    current_runtime,
    runtime_initialisation,
)
from .settings import RuntimeConfig, build_runtime_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping, Sequence

    from lib_log_rich.adapters import QueueAdapter
    from lib_log_rich.domain.context import LogContext
    from lib_log_rich.domain.dump_filter import FilterSpecValue
    from lib_log_rich.domain.enums import DropReason

    from .settings import RuntimeSettings

TKey = TypeVar("TKey")
TValue = TypeVar("TValue")


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Immutable view over the active logging runtime."""

    service: str
    environment: str
    console_level: LogLevel
    backend_level: LogLevel
    graylog_level: LogLevel
    queue_present: bool
    theme: ConsoleTheme | None
    console_styles: Mapping[str, str] | None


@dataclass(frozen=True)
class SeveritySnapshot:
    """Read-only summary of accumulated severity metrics."""

    highest: LogLevel | None
    total_events: int
    counts: Mapping[LogLevel, int]
    thresholds: Mapping[LogLevel, int]
    dropped_total: int
    drops_by_reason: Mapping[DropReason, int]
    drops_by_level: Mapping[LogLevel, int]
    drops_by_reason_and_level: Mapping[tuple[DropReason, LogLevel], int]


@dataclass(frozen=True)
class _DumpRequest:
    """Describe a caller's dump request in explicit terms."""

    format: DumpFormat
    target: Path | None
    minimum: LogLevel | None
    preset: FormatPreset | None
    template: str | None
    theme: ConsoleTheme | None
    styles: Mapping[str, str] | None
    dump_filter: DumpFilter | None
    colorize: bool


def inspect_runtime() -> RuntimeSnapshot:
    """Return a read-only snapshot of the current runtime state."""
    runtime = current_runtime()
    return _build_runtime_snapshot(runtime)


def _build_runtime_snapshot(runtime: LoggingRuntime) -> RuntimeSnapshot:
    """Construct an immutable snapshot describing the active runtime."""
    return RuntimeSnapshot(
        service=runtime.service,
        environment=runtime.environment,
        console_level=runtime.console_level,
        backend_level=runtime.backend_level,
        graylog_level=runtime.graylog_level,
        queue_present=runtime.queue is not None,
        theme=runtime.theme,
        console_styles=_snapshot_console_styles(runtime),
    )


def _snapshot_console_styles(runtime: LoggingRuntime) -> Mapping[str, str] | None:
    """Return an immutable copy of console styles when available."""
    styles = runtime.console_styles or None
    if not styles:
        return None
    return MappingProxyType(dict(styles))


def init(config: RuntimeConfig) -> None:
    """Compose the logging runtime according to configuration inputs."""
    with runtime_initialisation() as install_runtime:
        settings = _resolve_settings(config)
        runtime = build_runtime(settings)
        install_runtime(runtime)


def validate_config(config: RuntimeConfig) -> None:
    """Refuse ``config`` exactly as :func:`init` would, without starting a runtime.

    Environment overrides are applied as :func:`init` applies them, so the
    verdict holds for an ``init`` call made in the same environment. A running
    runtime is neither consulted nor touched.

    It builds no adapters and reads no files, so it cannot see the two
    refusals made while the runtime is built: :func:`init` raises ``TypeError``
    when ``console_adapter_factory`` returns an object missing a ``ConsolePort``
    method, and ``ValueError`` when the ``ring_buffer_checkpoint`` file holds a
    record it cannot load.

    Args:
        config: The configuration to judge.

    Raises:
        ValueError: With the message :func:`init` would raise for ``config``.

    Example:
        >>> validate_config(RuntimeConfig(service="svc", environment="dev"))
        >>> validate_config(RuntimeConfig(service="svc", environment="dev", console_level="loud"))
        Traceback (most recent call last):
        ...
        ValueError: Invalid runtime settings: console_level: Unknown log level: 'loud'

    """
    _resolve_settings(config)


def _resolve_settings(config: RuntimeConfig) -> RuntimeSettings:
    """Resolve ``config`` into settings, framing a refusal the way callers see it.

    :func:`init` and :func:`validate_config` both go through here, so the two
    cannot disagree about which configurations are valid or how a refusal reads.
    """
    try:
        return build_runtime_settings(config=config)
    except ValueError as exc:
        raise ValueError(f"Invalid runtime settings: {exc}") from exc


def getLogger(name: str) -> LoggerProxy:  # noqa: N802 - mirrors stdlib logging.getLogger for drop-in familiarity
    """Return a logger proxy bound to the configured runtime."""
    runtime = current_runtime()
    return LoggerProxy(name, runtime.process)


def max_level_seen() -> LogLevel | None:
    """Return the highest severity observed since initialisation."""
    runtime = current_runtime()
    return runtime.severity_monitor.highest()


def severity_snapshot() -> SeveritySnapshot:
    """Return counters summarising severities processed so far."""
    runtime = current_runtime()
    return _build_severity_snapshot(runtime)


def _build_severity_snapshot(runtime: LoggingRuntime) -> SeveritySnapshot:
    """Create a read-only severity summary from the active monitor."""
    monitor = runtime.severity_monitor
    return SeveritySnapshot(
        highest=monitor.highest(),
        total_events=monitor.total_events(),
        counts=_readonly(monitor.counts()),
        thresholds=_readonly(monitor.threshold_counts()),
        dropped_total=monitor.dropped_total(),
        drops_by_reason=_readonly(monitor.drops_by_reason()),
        drops_by_level=_readonly(monitor.drops_by_level()),
        drops_by_reason_and_level=_readonly(monitor.drops_by_reason_and_level()),
    )


def _readonly(mapping: Mapping[TKey, TValue]) -> Mapping[TKey, TValue]:
    """Return an immutable defensive copy of ``mapping``."""
    return MappingProxyType(dict(mapping))


def reset_severity_metrics() -> None:
    """Clear accumulated severity counters for the active runtime."""
    runtime = current_runtime()
    runtime.severity_monitor.reset()


@contextmanager
def bind(
    *,
    service: str | None = None,
    environment: str | None = None,
    job_id: str | None = None,
    request_id: str | None = None,
    user_id: str | None = None,
    user_name: str | None = None,
    hostname: str | None = None,
    process_id: int | None = None,
    process_id_chain: Sequence[int] | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> Generator[LogContext, None, None]:
    """Bind structured metadata for the current execution scope.

    The fields repeat :meth:`ContextBinder.bind` one for one, so a misspelt or
    mistyped field is a type error at the call site rather than a runtime
    ``TypeError``. ``None`` inherits the parent frame's value.

    Args:
        service: Service identifier.
        environment: Deployment environment.
        job_id: Job or task identifier.
        request_id: Request correlation identifier.
        user_id: Acting user's identifier.
        user_name: Acting user's name.
        hostname: Host name to record.
        process_id: Process id to record.
        process_id_chain: Ancestor process ids, oldest first.
        trace_id: Distributed trace identifier.
        span_id: Distributed span identifier.
        extra: Free-form metadata merged into the frame.

    Yields:
        The bound :class:`LogContext`.
    """
    runtime = current_runtime()
    with runtime.binder.bind(
        service=service,
        environment=environment,
        job_id=job_id,
        request_id=request_id,
        user_id=user_id,
        user_name=user_name,
        hostname=hostname,
        process_id=process_id,
        process_id_chain=process_id_chain,
        trace_id=trace_id,
        span_id=span_id,
        extra=extra,
    ) as ctx:
        yield ctx


def dump(
    *,
    dump_format: str | DumpFormat = "text",
    path: str | Path | None = None,
    level: str | LogLevel | None = None,
    console_format_preset: FormatPreset | None = None,
    console_format_template: str | None = None,
    theme: ConsoleTheme | None = None,
    console_styles: Mapping[str, str] | None = None,
    context_filters: Mapping[str, FilterSpecValue] | None = None,
    context_extra_filters: Mapping[str, FilterSpecValue] | None = None,
    extra_filters: Mapping[str, FilterSpecValue] | None = None,
    color: bool = False,
) -> str:
    """Render the in-memory ring buffer into a textual artefact.

    Raises:
        TypeError: If ``theme`` or ``console_format_preset`` is not a member of its enum
            (a plain string included); ``dump_format`` and ``level`` also take value strings.

    """
    if theme is not None:
        require_enum_member(theme, ConsoleTheme, parameter="theme")
    if console_format_preset is not None:
        require_enum_member(console_format_preset, FormatPreset, parameter="console_format_preset")
    runtime = current_runtime()
    request = _build_dump_request(
        runtime=runtime,
        dump_format=dump_format,
        path=path,
        level=level,
        console_format_preset=console_format_preset,
        console_format_template=console_format_template,
        theme=theme,
        console_styles=console_styles,
        context_filters=context_filters,
        context_extra_filters=context_extra_filters,
        extra_filters=extra_filters,
        color=color,
    )
    return _render_dump(runtime, request)


def _build_dump_request(
    *,
    runtime: LoggingRuntime,
    dump_format: str | DumpFormat,
    path: str | Path | None,
    level: str | LogLevel | None,
    console_format_preset: FormatPreset | None,
    console_format_template: str | None,
    theme: ConsoleTheme | None,
    console_styles: Mapping[str, str] | None,
    context_filters: Mapping[str, FilterSpecValue] | None,
    context_extra_filters: Mapping[str, FilterSpecValue] | None,
    extra_filters: Mapping[str, FilterSpecValue] | None,
    color: bool,
) -> _DumpRequest:
    """Translate caller inputs into a fully-resolved dump request."""
    return _DumpRequest(
        format=_resolve_dump_format(dump_format),
        target=_resolve_dump_target(path),
        minimum=_resolve_minimum_level(level),
        preset=console_format_preset,
        template=console_format_template,
        theme=_select_theme(runtime, theme),
        styles=_select_console_styles(runtime, console_styles),
        dump_filter=_compose_dump_filter(
            context_filters=context_filters,
            context_extra_filters=context_extra_filters,
            extra_filters=extra_filters,
        ),
        colorize=color,
    )


def _resolve_dump_format(value: str | DumpFormat) -> DumpFormat:
    """Return the :class:`DumpFormat` requested by the caller."""
    if isinstance(value, DumpFormat):
        return value
    return DumpFormat.from_name(value)


def _resolve_dump_target(path: str | Path | None) -> Path | None:
    """Convert ``path`` into a :class:`Path` when provided."""
    if path is None:
        return None
    return Path(path)


def _resolve_minimum_level(level: str | LogLevel | None) -> LogLevel | None:
    """Normalise the optional minimum level to the domain enum."""
    if level is None:
        return None
    return LogLevel.coerce(level)


def _select_theme(runtime: LoggingRuntime, override: ConsoleTheme | None) -> ConsoleTheme | None:
    """Return the theme override or the runtime default."""
    return override if override is not None else runtime.theme


def _select_console_styles(
    runtime: LoggingRuntime,
    override: Mapping[str, str] | None,
) -> Mapping[str, str] | None:
    """Return the caller's console styles, else the ones the user configured.

    Never the runtime's theme-filled styles: those would beat the dump's theme
    for every level and leave ``dump(theme=...)`` without effect.
    """
    return override if override is not None else runtime.configured_console_styles


def _compose_dump_filter(
    *,
    context_filters: Mapping[str, FilterSpecValue] | None,
    context_extra_filters: Mapping[str, FilterSpecValue] | None,
    extra_filters: Mapping[str, FilterSpecValue] | None,
) -> DumpFilter | None:
    """Build a dump filter when the caller supplies any filter input."""
    if not any((context_filters, context_extra_filters, extra_filters)):
        return None
    return build_dump_filter(
        context=_copy_filter_spec(context_filters),
        context_extra=_copy_filter_spec(context_extra_filters),
        extra=_copy_filter_spec(extra_filters),
    )


def _copy_filter_spec(spec: Mapping[str, FilterSpecValue] | None) -> dict[str, FilterSpecValue]:
    """Return a defensive copy of a filter mapping for downstream use."""
    if spec is None:
        return {}
    return dict(spec)


def _render_dump(runtime: LoggingRuntime, request: _DumpRequest) -> str:
    """Delegate to the runtime's dump renderer using the resolved request."""
    return runtime.capture_dump(
        dump_format=request.format,
        path=request.target,
        min_level=request.minimum,
        format_preset=request.preset,
        format_template=request.template,
        theme=request.theme,
        console_styles=request.styles,
        dump_filter=request.dump_filter,
        colorize=request.colorize,
    )


def shutdown() -> None:
    """Flush adapters, stop the queue, and clear runtime state synchronously."""
    _ensure_shutdown_allowed()
    asyncio.run(shutdown_async())


async def shutdown_async() -> None:
    """Flush adapters, stop the queue, and clear runtime state asynchronously."""
    runtime = current_runtime()
    await _shutdown_runtime(runtime)


def _ensure_shutdown_allowed() -> None:
    """Guard against shutting down from within a running event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop.is_running():
        raise RuntimeError(
            "lib_log_rich.shutdown() cannot run inside an active event loop; await lib_log_rich.shutdown_async() instead",
        )


async def _shutdown_runtime(runtime: LoggingRuntime) -> None:
    """Execute the asynchronous shutdown flow and clear global state."""
    await _perform_shutdown(runtime)
    clear_runtime()


async def _perform_shutdown(runtime: LoggingRuntime) -> None:
    """Coordinate shutdown hooks across adapters and use cases."""
    # Joining the worker on the loop's thread would stall the loop for up to the stop
    # timeout, and deadlock a diagnostic hook that hands its work to this loop.
    await asyncio.to_thread(_stop_queue, runtime.queue)
    await _await_shutdown_result(runtime.shutdown_async())


def _stop_queue(queue: QueueAdapter | None) -> None:
    """Stop the queue worker when queueing is enabled."""
    if queue is None:
        return
    queue.stop()


async def _await_shutdown_result(result: object) -> None:
    """Await shutdown hooks when they return awaitables."""
    if inspect.isawaitable(result):
        await result


def flush(timeout: float | None = None, *, flush_ring_buffer: bool = False) -> None:
    """Drain queues and flush adapters synchronously without terminating runtime.

    Blocks until the queue is empty and all adapters have flushed. Unlike
    :func:`shutdown`, the logging system remains active after this call completes.

    Args:
        timeout: Maximum seconds to wait for queue drain. Default: 5.0s.
        flush_ring_buffer: Whether to persist the ring buffer. Default ``False``.

    Raises:
        TimeoutError: If flush doesn't complete within timeout.
        RuntimeError: If called from within an active event loop.

    Example:
        >>> import lib_log_rich  # doctest: +SKIP
        >>> lib_log_rich.init(lib_log_rich.RuntimeConfig(service="demo", environment="dev"))  # doctest: +SKIP
        >>> logger = lib_log_rich.getLogger(__name__)  # doctest: +SKIP
        >>> logger.info("event 1")  # doctest: +SKIP
        >>> lib_log_rich.flush()  # drains queue, keeps runtime active  # doctest: +SKIP
        >>> logger.info("event 2")  # still works  # doctest: +SKIP
        >>> lib_log_rich.shutdown()  # doctest: +SKIP

    """
    _ensure_flush_allowed()
    asyncio.run(flush_async(timeout, flush_ring_buffer=flush_ring_buffer))


async def flush_async(timeout: float | None = None, *, flush_ring_buffer: bool = False) -> None:
    """Drain queues and flush adapters asynchronously without terminating runtime.

    Waits until the queue is empty and all adapters have flushed. Unlike
    :func:`shutdown_async`, the logging system remains active after this completes.

    Args:
        timeout: Maximum seconds to wait for queue drain. Default: 5.0s.
        flush_ring_buffer: Whether to persist the ring buffer. Default ``False``.

    Raises:
        TimeoutError: If flush doesn't complete within timeout.

    """
    runtime = current_runtime()
    await runtime.flush_async(timeout, flush_ring_buffer)


def _ensure_flush_allowed() -> None:
    """Guard against flushing from within a running event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop.is_running():
        raise RuntimeError(
            "lib_log_rich.flush() cannot run inside an active event loop; await lib_log_rich.flush_async() instead",
        )


def hello_world() -> None:
    """Print the canonical smoke-test message used in docs and doctests."""
    print("Hello World")  # noqa: T201 - printing is this function's entire purpose


def i_should_fail() -> None:
    """Raise ``RuntimeError`` to exercise failure handling in examples/tests."""
    raise RuntimeError("I should fail")


def summary_info() -> str:
    """Return the metadata banner used by the CLI entry point and docs."""
    from .. import __init__conf__  # noqa: PLC0415 - avoids importing the package __init__conf__ during runtime package init

    return _capture_stdout(__init__conf__.print_info)


def _capture_stdout(printer: Callable[[], None]) -> str:
    """Capture stdout produced by ``printer`` and return it as text."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        printer()
    return buffer.getvalue()


__all__ = [
    "RuntimeSnapshot",
    "SeveritySnapshot",
    "bind",
    "dump",
    "flush",
    "flush_async",
    "getLogger",
    "hello_world",
    "i_should_fail",
    "init",
    "inspect_runtime",
    "max_level_seen",
    "reset_severity_metrics",
    "severity_snapshot",
    "shutdown",
    "shutdown_async",
    "summary_info",
    "validate_config",
]
