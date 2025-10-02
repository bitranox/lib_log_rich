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
from typing import Any, Mapping, Optional

from lib_log_rich.domain import DumpFormat, LogLevel, build_dump_filter
from lib_log_rich.domain.dump_filter import FilterSpecValue

from ._composition import LoggerProxy, build_runtime, coerce_level
from ._settings import DiagnosticHook, build_runtime_settings
from ._state import LoggingRuntime, clear_runtime, current_runtime, is_initialised, set_runtime


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


def init(
    *,
    service: str,
    environment: str,
    console_level: str | LogLevel = LogLevel.INFO,
    backend_level: str | LogLevel = LogLevel.WARNING,
    graylog_endpoint: tuple[str, int] | None = None,
    graylog_level: str | LogLevel = LogLevel.WARNING,
    enable_ring_buffer: bool = True,
    ring_buffer_size: int = 25_000,
    enable_journald: bool = False,
    enable_eventlog: bool = False,
    enable_graylog: bool = False,
    graylog_protocol: str = "tcp",
    graylog_tls: bool = False,
    queue_enabled: bool = True,
    queue_maxsize: int = 2048,
    queue_full_policy: str = "block",
    queue_put_timeout: float | None = None,
    queue_stop_timeout: float | None = None,
    force_color: bool = False,
    no_color: bool = False,
    console_styles: Mapping[str, str] | None = None,
    console_theme: str | None = None,
    console_format_preset: str | None = None,
    console_format_template: str | None = None,
    scrub_patterns: Optional[dict[str, str]] = None,
    dump_format_preset: str | None = None,
    dump_format_template: str | None = None,
    rate_limit: Optional[tuple[int, float]] = None,
    diagnostic_hook: DiagnosticHook = None,
) -> None:
    """Compose the logging runtime according to configuration inputs."""

    if is_initialised():
        raise RuntimeError(
            "lib_log_rich.init() cannot be called twice without shutdown(); call lib_log_rich.shutdown() first",
        )

    settings = build_runtime_settings(
        service=service,
        environment=environment,
        console_level=console_level,
        backend_level=backend_level,
        graylog_endpoint=graylog_endpoint,
        graylog_level=graylog_level,
        enable_ring_buffer=enable_ring_buffer,
        ring_buffer_size=ring_buffer_size,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        enable_graylog=enable_graylog,
        graylog_protocol=graylog_protocol,
        graylog_tls=graylog_tls,
        queue_enabled=queue_enabled,
        queue_maxsize=queue_maxsize,
        queue_full_policy=queue_full_policy,
        queue_put_timeout=queue_put_timeout,
        queue_stop_timeout=queue_stop_timeout,
        force_color=force_color,
        no_color=no_color,
        console_styles=console_styles,
        console_theme=console_theme,
        console_format_preset=console_format_preset,
        console_format_template=console_format_template,
        scrub_patterns=scrub_patterns,
        dump_format_preset=dump_format_preset,
        dump_format_template=dump_format_template,
        rate_limit=rate_limit,
        diagnostic_hook=diagnostic_hook,
    )
    runtime = build_runtime(settings)
    set_runtime(runtime)


def get(name: str) -> LoggerProxy:
    """Return a logger proxy bound to the configured runtime."""

    runtime = current_runtime()
    return LoggerProxy(name, runtime.process)


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
    await _perform_shutdown(runtime)
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
    "bind",
    "dump",
    "get",
    "hello_world",
    "i_should_fail",
    "init",
    "inspect_runtime",
    "shutdown",
    "shutdown_async",
    "summary_info",
]
