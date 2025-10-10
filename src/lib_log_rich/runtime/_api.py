"""Runtime API surface for lib_log_rich.

This module hosts the high-level functions exposed by :mod:`lib_log_rich.runtime`.
Breaking the implementation out of ``__init__`` keeps the public façade thin and
focused.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Tuple

from lib_log_rich.domain import DumpFormat, LogLevel, build_dump_filter
from lib_log_rich.domain.dump_filter import FilterSpecValue

from ._composition import LoggerProxy, build_runtime, coerce_level
from ._settings import RuntimeConfig, build_runtime_settings
from ._state import (
    LoggingRuntime,
    clear_runtime,
    current_runtime,
    runtime_initialisation,
)


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
    drops_by_reason_and_level: Mapping[Tuple[str, LogLevel], int]


def inspect_runtime() -> RuntimeSnapshot:
    """Return a read-only snapshot of the current runtime state."""

    runtime = current_runtime()
    styles = runtime.console_styles or None
    readonly_styles: Mapping[str, str] | None
    if styles:
        readonly_styles = MappingProxyType(dict(styles))
    else:
        readonly_styles = None
    return RuntimeSnapshot(
        service=runtime.service,
        environment=runtime.environment,
        console_level=runtime.console_level,
        backend_level=runtime.backend_level,
        graylog_level=runtime.graylog_level,
        queue_present=runtime.queue is not None,
        theme=runtime.theme,
        console_styles=readonly_styles,
    )


def init(config: RuntimeConfig) -> None:
    """Compose the logging runtime according to configuration inputs."""

    with runtime_initialisation() as install_runtime:
        try:
            settings = build_runtime_settings(config=config)
        except ValueError as exc:
            raise ValueError(f"Invalid runtime settings: {exc}") from exc
        runtime = build_runtime(settings)
        install_runtime(runtime)


def get(name: str) -> LoggerProxy:
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
    monitor = runtime.severity_monitor
    return SeveritySnapshot(
        highest=monitor.highest(),
        total_events=monitor.total_events(),
        counts=MappingProxyType(dict(monitor.counts())),
        thresholds=MappingProxyType(dict(monitor.threshold_counts())),
        dropped_total=monitor.dropped_total(),
        drops_by_reason=MappingProxyType(dict(monitor.drops_by_reason())),
        drops_by_level=MappingProxyType(dict(monitor.drops_by_level())),
        drops_by_reason_and_level=MappingProxyType(dict(monitor.drops_by_reason_and_level())),
    )


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
    fmt = dump_format if isinstance(dump_format, DumpFormat) else DumpFormat.from_name(dump_format)
    target = Path(path) if path is not None else None
    min_level = coerce_level(level) if level is not None else None
    template = console_format_template
    resolved_theme = theme if theme is not None else runtime.theme
    resolved_styles = console_styles if console_styles is not None else runtime.console_styles
    dump_filter = None
    if any(spec is not None for spec in (context_filters, context_extra_filters, extra_filters)):
        dump_filter = build_dump_filter(
            context=_normalise_filter_spec(context_filters),
            context_extra=_normalise_filter_spec(context_extra_filters),
            extra=_normalise_filter_spec(extra_filters),
        )
    return runtime.capture_dump(
        dump_format=fmt,
        path=target,
        min_level=min_level,
        format_preset=console_format_preset,
        format_template=template,
        text_template=template,
        theme=resolved_theme,
        console_styles=resolved_styles,
        dump_filter=dump_filter,
        colorize=color,
    )


def _normalise_filter_spec(spec: Mapping[str, FilterSpecValue] | None) -> dict[str, FilterSpecValue]:
    """Return a mutable copy of the user-supplied filter mapping."""

    if spec is None:
        return {}
    return dict(spec)


def shutdown() -> None:
    """Flush adapters, stop the queue, and clear runtime state synchronously."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    else:
        if loop.is_running():
            raise RuntimeError(
                "lib_log_rich.shutdown() cannot run inside an active event loop; await lib_log_rich.shutdown_async() instead",
            )
    asyncio.run(shutdown_async())


async def shutdown_async() -> None:
    """Flush adapters, stop the queue, and clear runtime state asynchronously."""

    runtime = current_runtime()
    try:
        await _perform_shutdown(runtime)
    except Exception:
        raise
    else:
        clear_runtime()


async def _perform_shutdown(runtime: LoggingRuntime) -> None:
    """Coordinate shutdown hooks across adapters and use cases."""

    if runtime.queue is not None:
        runtime.queue.stop()
    result = runtime.shutdown_async()
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

    lines: list[str] = []

    def _capture(text: str) -> None:
        lines.append(text)

    __init__conf__.print_info(writer=_capture)
    return "".join(lines)


__all__ = [
    "RuntimeSnapshot",
    "SeveritySnapshot",
    "bind",
    "dump",
    "get",
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
