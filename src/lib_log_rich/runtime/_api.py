"""Runtime API surface for lib_log_rich.

This module hosts the high-level functions exposed by :mod:`lib_log_rich.runtime`.
Breaking the implementation out of ``__init__`` keeps the public façade thin and
focused.
"""

from __future__ import annotations

import asyncio
import inspect
import io
from collections.abc import Callable, Mapping
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

from lib_log_rich.adapters import QueueAdapter
from lib_log_rich.domain import DumpFilter, DumpFormat, LogLevel, build_dump_filter
from lib_log_rich.domain.dump_filter import FilterSpecValue

from ._composition import LoggerProxy, build_runtime, coerce_level
from ._settings import RuntimeConfig, build_runtime_settings
from ._state import (
    LoggingRuntime,
    clear_runtime,
    current_runtime,
    runtime_initialisation,
)

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
    theme: str | None
    console_styles: Mapping[str, str] | None


@dataclass(frozen=True)
class SeveritySnapshot:
    """Read-only summary of accumulated severity metrics."""

    highest: LogLevel | None
    total_events: int
    counts: Mapping[LogLevel, int]
    thresholds: Mapping[LogLevel, int]
    dropped_total: int
    drops_by_reason: Mapping[str, int]
    drops_by_level: Mapping[LogLevel, int]
    drops_by_reason_and_level: Mapping[tuple[str, LogLevel], int]


@dataclass(frozen=True)
class _DumpRequest:
    """Describe a caller's dump request in explicit terms."""

    format: DumpFormat
    target: Path | None
    minimum: LogLevel | None
    preset: str | None
    template: str | None
    theme: str | None
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
        try:
            settings = build_runtime_settings(config=config)
        except ValueError as exc:
            raise ValueError(f"Invalid runtime settings: {exc}") from exc
        runtime = build_runtime(settings)
        install_runtime(runtime)


def getLogger(name: str) -> LoggerProxy:
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
def bind(**fields: Any):
    """Bind structured metadata for the current execution scope."""
    runtime = current_runtime()
    with runtime.binder.bind(**fields) as ctx:
        yield ctx


def dump(
    *,
    dump_format: str | DumpFormat = "text",
    path: str | Path | None = None,
    level: str | LogLevel | None = None,
    console_format_preset: str | None = None,
    console_format_template: str | None = None,
    theme: str | None = None,
    console_styles: Mapping[str, str] | None = None,
    context_filters: Mapping[str, FilterSpecValue] | None = None,
    context_extra_filters: Mapping[str, FilterSpecValue] | None = None,
    extra_filters: Mapping[str, FilterSpecValue] | None = None,
    color: bool = False,
) -> str:
    """Render the in-memory ring buffer into a textual artefact."""
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
    console_format_preset: str | None,
    console_format_template: str | None,
    theme: str | None,
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
    return coerce_level(level)


def _select_theme(runtime: LoggingRuntime, override: str | None) -> str | None:
    """Return the theme override or the runtime default."""
    return override if override is not None else runtime.theme


def _select_console_styles(
    runtime: LoggingRuntime,
    override: Mapping[str, str] | None,
) -> Mapping[str, str] | None:
    """Return console styles provided by the caller or runtime defaults."""
    return override if override is not None else runtime.console_styles


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
        text_template=request.template,
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
    _stop_queue(runtime.queue)
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


def hello_world() -> None:
    """Print the canonical smoke-test message used in docs and doctests."""
    print("Hello World")


def i_should_fail() -> None:
    """Raise ``RuntimeError`` to exercise failure handling in examples/tests."""
    raise RuntimeError("I should fail")


def summary_info() -> str:
    """Return the metadata banner used by the CLI entry point and docs."""
    from .. import __init__conf__

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
]
