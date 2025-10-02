"""Demonstration helpers for previewing logging output."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .domain import DumpFormat, LogLevel
from .domain.dump_filter import FilterSpecValue
from .domain.palettes import CONSOLE_STYLE_THEMES
from .runtime import bind, dump, get, init, is_initialised, shutdown


def _resolve_demo_theme(theme: str) -> tuple[str, dict[str, str]]:
    """Return the normalised theme key and palette for logdemo."""

    key = theme.strip().lower()
    try:
        palette = CONSOLE_STYLE_THEMES[key]
    except KeyError as exc:
        raise ValueError(f"Unknown console theme: {theme!r}") from exc
    return key, dict(palette)


def _demo_identity(service: str | None, environment: str | None, theme_key: str) -> tuple[str, str]:
    """Choose service/environment defaults that explain the demo purpose."""

    resolved_service = service or "logdemo"
    resolved_environment = environment or f"demo-{theme_key}"
    return resolved_service, resolved_environment


def _demo_graylog_endpoint(enable_graylog: bool, endpoint: tuple[str, int] | None) -> tuple[str, int] | None:
    """Pick a GELF endpoint when the demo toggles Graylog on."""

    if not enable_graylog:
        return None
    return endpoint or ("127.0.0.1", 12201)


def _demo_emit_events(theme_key: str) -> list[dict[str, Any]]:
    """Emit one sample per severity inside a scoped binding."""

    samples = [
        (LogLevel.DEBUG, "Debug message"),
        (LogLevel.INFO, "Information message"),
        (LogLevel.WARNING, "Warning message"),
        (LogLevel.ERROR, "Error message"),
        (LogLevel.CRITICAL, "Critical message"),
    ]
    results: list[dict[str, Any]] = []
    with bind(job_id=f"logdemo-{theme_key}", request_id="demo"):
        logger = get("logdemo")
        emitters = {
            LogLevel.DEBUG: logger.debug,
            LogLevel.INFO: logger.info,
            LogLevel.WARNING: logger.warning,
            LogLevel.ERROR: logger.error,
            LogLevel.CRITICAL: logger.critical,
        }
        for level, message in samples:
            payload = {
                "theme": theme_key,
                "level": level.severity,
            }
            results.append(emitters[level](f"[{theme_key}] {message}", extra=payload))
    return results


FilterMapping = Mapping[str, FilterSpecValue]


def _demo_render_dump(
    *,
    dump_format: str | DumpFormat | None,
    dump_path: str | Path | None,
    color: bool | None,
    dump_format_preset: str | None,
    dump_format_template: str | None,
    theme: str,
    styles: Mapping[str, str],
    context_filters: FilterMapping | None = None,
    context_extra_filters: FilterMapping | None = None,
    extra_filters: FilterMapping | None = None,
) -> str | None:
    """Render and optionally persist a dump for the demo."""

    if dump_format is None:
        return None
    fmt = dump_format if isinstance(dump_format, DumpFormat) else DumpFormat.from_name(str(dump_format))
    target = Path(dump_path) if dump_path is not None else None
    colorize = color if color is not None else fmt in (DumpFormat.TEXT, DumpFormat.HTML_TXT)
    return dump(
        dump_format=fmt,
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
    theme: str = "classic",
    service: str | None = None,
    environment: str | None = None,
    dump_format: str | DumpFormat | None = None,
    dump_path: str | Path | None = None,
    color: bool | None = None,
    console_format_preset: str | None = None,
    console_format_template: str | None = None,
    dump_format_preset: str | None = None,
    dump_format_template: str | None = None,
    context_filters: FilterMapping | None = None,
    context_extra_filters: FilterMapping | None = None,
    extra_filters: FilterMapping | None = None,
    enable_graylog: bool = False,
    graylog_endpoint: tuple[str, int] | None = None,
    graylog_protocol: str = "tcp",
    graylog_tls: bool = False,
    enable_journald: bool = False,
    enable_eventlog: bool = False,
) -> dict[str, Any]:
    """Emit sample log entries and optionally hit real backends."""

    if is_initialised():
        raise RuntimeError("logdemo() requires lib_log_rich to be uninitialised. Call shutdown() first.")

    theme_key, styles = _resolve_demo_theme(theme)
    resolved_service, resolved_environment = _demo_identity(service, environment, theme_key)
    resolved_graylog_endpoint = _demo_graylog_endpoint(enable_graylog, graylog_endpoint)

    init(
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

    events: list[dict[str, Any]] = []
    dump_payload: str | None = None
    try:
        events = _demo_emit_events(theme_key)
        dump_payload = _demo_render_dump(
            dump_format=dump_format,
            dump_path=dump_path,
            color=color,
            dump_format_preset=dump_format_preset,
            dump_format_template=dump_format_template,
            theme=theme_key,
            styles=styles,
            context_filters=context_filters,
            context_extra_filters=context_extra_filters,
            extra_filters=extra_filters,
        )
    finally:
        shutdown()

    return {
        "theme": theme_key,
        "styles": dict(styles),
        "events": events,
        "dump": dump_payload,
        "service": resolved_service,
        "environment": resolved_environment,
        "backends": {
            "graylog": enable_graylog and resolved_graylog_endpoint is not None,
            "journald": enable_journald,
            "eventlog": enable_eventlog,
        },
    }


__all__ = ["logdemo"]
