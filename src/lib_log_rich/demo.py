"""Demonstration helpers for previewing logging output."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .domain import DumpFormat, LogLevel
from .domain.dump_filter import FilterSpecValue
from .domain.enums import ConsoleTheme, FormatPreset, GraylogProtocol, require_enum_member
from .domain.palettes import CONSOLE_STYLE_THEMES
from .runtime import RuntimeConfig, bind, dump, getLogger, init, is_initialised, shutdown

if TYPE_CHECKING:
    from .application.use_cases._types import ProcessResult


@dataclass(slots=True, frozen=True)
class BackendStatus:
    """Status of backend adapters enabled during demo.

    Indicates which logging backends were active during the demo run.
    """

    graylog: bool
    journald: bool
    eventlog: bool


@dataclass(slots=True, frozen=True)
class LogDemoResult:
    """Result of a logdemo() execution.

    Contains all outputs and metadata from running a demonstration
    of the logging system with a specific theme.
    """

    theme: ConsoleTheme
    styles: dict[str, str]
    events: list[ProcessResult]
    dump: str | None
    service: str
    environment: str
    backends: BackendStatus


def _demo_palette(theme: ConsoleTheme) -> dict[str, str]:
    """Return a copy of the palette logdemo styles its console with.

    Raises:
        ValueError: If ``theme`` names no palette (an untyped caller's plain string).
    """
    try:
        return dict(CONSOLE_STYLE_THEMES[theme])
    except KeyError as exc:
        raise ValueError(f"Unknown console theme: {str(theme)!r}") from exc


def _demo_identity(service: str | None, environment: str | None, theme: ConsoleTheme) -> tuple[str, str]:
    """Choose service/environment defaults that explain the demo purpose."""
    resolved_service = service or "logdemo"
    resolved_environment = environment or f"demo-{theme}"
    return resolved_service, resolved_environment


def _demo_graylog_endpoint(*, enable_graylog: bool, endpoint: tuple[str, int] | None) -> tuple[str, int] | None:
    """Pick a GELF endpoint when the demo toggles Graylog on."""
    if not enable_graylog:
        return None
    return endpoint or ("127.0.0.1", 12201)


def _demo_emit_events(theme: ConsoleTheme) -> list[ProcessResult]:
    """Emit one sample per severity inside a scoped binding."""
    samples = [
        (LogLevel.DEBUG, "Debug message"),
        (LogLevel.INFO, "Information message"),
        (LogLevel.WARNING, "Warning message"),
        (LogLevel.ERROR, "Error message"),
        (LogLevel.CRITICAL, "Critical message"),
    ]
    results: list[ProcessResult] = []
    with bind(job_id=f"logdemo-{theme}", request_id="demo"):
        logger = getLogger("logdemo")
        emitters = {
            LogLevel.DEBUG: logger.debug,
            LogLevel.INFO: logger.info,
            LogLevel.WARNING: logger.warning,
            LogLevel.ERROR: logger.error,
            LogLevel.CRITICAL: logger.critical,
        }
        for level, message in samples:
            payload = {
                # Extras are free-form payload data, so they carry the plain name.
                "theme": theme.value,
                "level": level.severity,
            }
            results.append(emitters[level](f"[{theme}] {message}", extra=payload))
    return results


FilterMapping = Mapping[str, FilterSpecValue]


def _demo_render_dump(
    *,
    dump_format: DumpFormat | None,
    dump_path: str | Path | None,
    color: bool | None,
    dump_format_preset: FormatPreset | None,
    dump_format_template: str | None,
    theme: ConsoleTheme,
    styles: Mapping[str, str],
    context_filters: FilterMapping | None = None,
    context_extra_filters: FilterMapping | None = None,
    extra_filters: FilterMapping | None = None,
) -> str | None:
    """Render and optionally persist a dump for the demo."""
    if dump_format is None:
        return None
    target = Path(dump_path) if dump_path is not None else None
    colorize = color if color is not None else dump_format in (DumpFormat.TEXT, DumpFormat.HTML_TXT)
    return dump(
        dump_format=dump_format,
        path=target,
        color=colorize,
        console_format_preset=dump_format_preset,
        console_format_template=dump_format_template,
        theme=theme,
        console_styles=styles,
        context_filters=context_filters,
        context_extra_filters=context_extra_filters,
        extra_filters=extra_filters,
    )


def logdemo(
    *,
    theme: ConsoleTheme = ConsoleTheme.CLASSIC,
    service: str | None = None,
    environment: str | None = None,
    dump_format: DumpFormat | None = None,
    dump_path: str | Path | None = None,
    color: bool | None = None,
    console_format_preset: FormatPreset | None = None,
    console_format_template: str | None = None,
    dump_format_preset: FormatPreset | None = None,
    dump_format_template: str | None = None,
    context_filters: FilterMapping | None = None,
    context_extra_filters: FilterMapping | None = None,
    extra_filters: FilterMapping | None = None,
    enable_graylog: bool = False,
    graylog_endpoint: tuple[str, int] | None = None,
    graylog_protocol: GraylogProtocol = GraylogProtocol.TCP,
    graylog_tls: bool = False,
    enable_journald: bool = False,
    enable_eventlog: bool = False,
) -> LogDemoResult:
    """Emit sample log entries and optionally hit real backends.

    Raises:
        TypeError: If ``theme``, ``console_format_preset`` or ``dump_format_preset`` is not
            a member of its enum (a plain string included).
        RuntimeError: If lib_log_rich is already initialised.

    """
    require_enum_member(theme, ConsoleTheme, parameter="theme")
    for parameter, preset in (("console_format_preset", console_format_preset), ("dump_format_preset", dump_format_preset)):
        if preset is not None:
            require_enum_member(preset, FormatPreset, parameter=parameter)
    if is_initialised():
        raise RuntimeError("logdemo() requires lib_log_rich to be uninitialised. Call shutdown() first.")

    styles = _demo_palette(theme)
    resolved_service, resolved_environment = _demo_identity(service, environment, theme)
    resolved_graylog_endpoint = _demo_graylog_endpoint(enable_graylog=enable_graylog, endpoint=graylog_endpoint)

    config = RuntimeConfig(
        service=resolved_service,
        environment=resolved_environment,
        console_level=LogLevel.DEBUG,
        backend_level=LogLevel.CRITICAL,
        enable_ring_buffer=False,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        enable_graylog=enable_graylog,
        graylog_endpoint=resolved_graylog_endpoint,
        graylog_protocol=graylog_protocol,
        graylog_tls=graylog_tls,
        queue_enabled=False,
        force_color=True,
        console_styles=styles,
        console_theme=theme,
        console_format_preset=console_format_preset,
        console_format_template=console_format_template,
        dump_format_preset=dump_format_preset,
        dump_format_template=dump_format_template,
    )
    init(config)

    events: list[ProcessResult] = []
    dump_payload: str | None = None
    try:
        events = _demo_emit_events(theme)
        dump_payload = _demo_render_dump(
            dump_format=dump_format,
            dump_path=dump_path,
            color=color,
            dump_format_preset=dump_format_preset,
            dump_format_template=dump_format_template,
            theme=theme,
            styles=styles,
            context_filters=context_filters,
            context_extra_filters=context_extra_filters,
            extra_filters=extra_filters,
        )
    finally:
        shutdown()

    return LogDemoResult(
        theme=theme,
        styles=dict(styles),
        events=events,
        dump=dump_payload,
        service=resolved_service,
        environment=resolved_environment,
        backends=BackendStatus(
            graylog=enable_graylog and resolved_graylog_endpoint is not None,
            journald=enable_journald,
            eventlog=enable_eventlog,
        ),
    )


__all__ = ["BackendStatus", "LogDemoResult", "logdemo"]
