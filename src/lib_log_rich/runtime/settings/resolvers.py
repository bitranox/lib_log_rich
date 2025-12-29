"""Functions that merge configuration inputs into runtime settings."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from pydantic import ValidationError

from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.enums import ConsoleStream, GraylogProtocol, QueuePolicy
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

from .models import (  # pyright: ignore[reportPrivateUsage]
    DEFAULT_SCRUB_PATTERNS,
    ConsoleAppearance,
    DumpDefaults,
    FeatureFlags,
    GraylogSettings,
    PayloadLimits,
    RuntimeConfig,
    RuntimeSettings,
    coerce_console_styles_input,
)


def _resolve_ring_buffer_size(config: RuntimeConfig) -> int:
    """Resolve ring buffer size from config or environment, with validation."""
    ring_buffer_env = os.getenv("LOG_RING_BUFFER_SIZE")
    if ring_buffer_env is not None:
        try:
            ring_size = int(ring_buffer_env)
        except ValueError as exc:
            raise ValueError("LOG_RING_BUFFER_SIZE must be an integer") from exc
        source_label = "LOG_RING_BUFFER_SIZE"
    else:
        ring_size = config.ring_buffer_size
        source_label = "ring_buffer_size"

    if ring_size <= 0:
        raise ValueError(f"{source_label} must be positive")
    return ring_size


def _resolve_payload_limits(config: RuntimeConfig) -> PayloadLimits:
    """Normalize payload_limits to PayloadLimits instance."""
    if config.payload_limits is None:
        return PayloadLimits()
    if isinstance(config.payload_limits, PayloadLimits):
        return config.payload_limits
    return PayloadLimits(**dict(config.payload_limits))


def _resolve_queue_settings(config: RuntimeConfig) -> tuple[int, QueuePolicy, float | None, float | None]:
    """Resolve all queue-related settings."""
    queue_size = resolve_queue_maxsize(config.queue_maxsize)
    queue_policy = resolve_queue_policy(QueuePolicy.from_str(config.queue_full_policy))
    queue_timeout = resolve_queue_timeout(config.queue_put_timeout)
    queue_stop_timeout = resolve_queue_stop_timeout(config.queue_stop_timeout)
    return queue_size, queue_policy, queue_timeout, queue_stop_timeout


def _resolve_adapters(config: RuntimeConfig, graylog_level: str | LogLevel) -> tuple[Any, Any, Any]:
    """Resolve console, dump, and graylog adapter settings."""
    console_model = resolve_console(
        force_color=config.force_color,
        no_color=config.no_color,
        console_theme=config.console_theme,
        console_styles=config.console_styles,
        console_format_preset=config.console_format_preset,
        console_format_template=config.console_format_template,
        console_stream=config.console_stream,
        console_stream_target=config.console_stream_target,
    )
    dump_defaults = resolve_dump_defaults(
        dump_format_preset=config.dump_format_preset,
        dump_format_template=config.dump_format_template,
    )
    graylog_settings = resolve_graylog(
        enable_graylog=config.enable_graylog,
        graylog_endpoint=config.graylog_endpoint,
        graylog_protocol=config.graylog_protocol,
        graylog_tls=config.graylog_tls,
        graylog_level=graylog_level,
    )
    return console_model, dump_defaults, graylog_settings


def build_runtime_settings(*, config: RuntimeConfig) -> RuntimeSettings:
    """Blend a RuntimeConfig with environment overrides and platform guards."""
    service_value, environment_value = service_and_environment(config.service, config.environment)
    console_level, backend_level, graylog_level = resolve_levels(config.console_level, config.backend_level, config.graylog_level)

    ring_size = _resolve_ring_buffer_size(config)
    flags = resolve_feature_flags(
        enable_ring_buffer=config.enable_ring_buffer,
        enable_journald=config.enable_journald,
        enable_eventlog=config.enable_eventlog,
        queue_enabled=config.queue_enabled,
    )
    queue_size, queue_policy, queue_timeout, queue_stop_timeout = _resolve_queue_settings(config)
    console_model, dump_defaults, graylog_settings = _resolve_adapters(config, graylog_level)

    try:
        return RuntimeSettings(
            service=service_value,
            environment=environment_value,
            console_level=console_level,
            backend_level=backend_level,
            graylog_level=graylog_level,
            ring_buffer_size=ring_size,
            console=console_model,
            dump=dump_defaults,
            graylog=graylog_settings,
            flags=flags,
            rate_limit=resolve_rate_limit(config.rate_limit),
            limits=_resolve_payload_limits(config),
            scrub_patterns=resolve_scrub_patterns(config.scrub_patterns),
            diagnostic_hook=config.diagnostic_hook,
            console_factory=config.console_adapter_factory,
            queue_maxsize=queue_size,
            queue_full_policy=queue_policy,
            queue_put_timeout=queue_timeout,
            queue_stop_timeout=queue_stop_timeout,
        )
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def service_and_environment(service: str, environment: str) -> tuple[str, str]:
    """Return service/environment after environment overrides."""
    return os.getenv("LOG_SERVICE", service), os.getenv("LOG_ENVIRONMENT", environment)


def resolve_levels(
    console_level: str | LogLevel,
    backend_level: str | LogLevel,
    graylog_level: str | LogLevel,
) -> tuple[str | LogLevel, str | LogLevel, str | LogLevel]:
    """Apply environment overrides to severity thresholds."""
    return (
        os.getenv("LOG_CONSOLE_LEVEL", console_level),
        os.getenv("LOG_BACKEND_LEVEL", backend_level),
        os.getenv("LOG_GRAYLOG_LEVEL", graylog_level),
    )


def resolve_feature_flags(
    *,
    enable_ring_buffer: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    queue_enabled: bool,
) -> FeatureFlags:
    """Determine adapter feature flags with platform guards."""
    ring_buffer = env_bool("LOG_RING_BUFFER_ENABLED", enable_ring_buffer)
    journald = env_bool("LOG_ENABLE_JOURNALD", enable_journald)
    eventlog = env_bool("LOG_ENABLE_EVENTLOG", enable_eventlog)
    queue = env_bool("LOG_QUEUE_ENABLED", queue_enabled)
    if sys.platform.startswith("win"):
        journald = False
    else:
        eventlog = False
    return FeatureFlags(queue=queue, ring_buffer=ring_buffer, journald=journald, eventlog=eventlog)


def _resolve_console_stream(
    console_stream: str,
    console_stream_target: object | None,
) -> tuple[ConsoleStream, object | None]:
    """Resolve and validate console stream settings."""
    stream_candidate = os.getenv("LOG_CONSOLE_STREAM") or console_stream
    stream_input = stream_candidate.strip().lower() if stream_candidate else ConsoleStream.STDERR.value
    stream_enum = ConsoleStream.from_str(stream_input)
    stream_target = console_stream_target if stream_enum is ConsoleStream.CUSTOM else None
    if stream_enum is ConsoleStream.CUSTOM and stream_target is None:
        raise ValueError("console_stream_target must be provided when console stream is 'custom'")
    return stream_enum, stream_target


def resolve_console(
    *,
    force_color: bool,
    no_color: bool,
    console_theme: str | None,
    console_styles: Mapping[str, str] | Mapping[LogLevel, str] | None,
    console_format_preset: str | None,
    console_format_template: str | None,
    console_stream: str,
    console_stream_target: object | None,
) -> ConsoleAppearance:
    """Blend console formatting inputs with environment overrides."""
    force = env_bool("LOG_FORCE_COLOR", force_color)
    no = env_bool("LOG_NO_COLOR", no_color)
    env_styles = parse_console_styles(os.getenv("LOG_CONSOLE_STYLES"))
    theme = os.getenv("LOG_CONSOLE_THEME") or console_theme
    preset = os.getenv("LOG_CONSOLE_FORMAT_PRESET") or console_format_preset
    template = os.getenv("LOG_CONSOLE_FORMAT_TEMPLATE") or console_format_template
    stream_value, stream_target = _resolve_console_stream(console_stream, console_stream_target)
    explicit_styles = coerce_console_styles_input(console_styles)
    resolved_theme, resolved_styles = resolve_console_palette(theme, explicit_styles, env_styles)

    return ConsoleAppearance(
        force_color=force,
        no_color=no,
        theme=resolved_theme,
        styles=resolved_styles,
        format_preset=preset,
        format_template=template,
        stream=stream_value,
        stream_target=stream_target,
    )


def resolve_dump_defaults(
    *,
    dump_format_preset: str | None,
    dump_format_template: str | None,
) -> DumpDefaults:
    """Determine dump format defaults respecting environment overrides."""
    preset = os.getenv("LOG_DUMP_FORMAT_PRESET") or dump_format_preset or "full"
    template = os.getenv("LOG_DUMP_FORMAT_TEMPLATE") or dump_format_template
    return DumpDefaults(format_preset=preset, format_template=template)


def resolve_graylog(
    *,
    enable_graylog: bool,
    graylog_endpoint: tuple[str, int] | None,
    graylog_protocol: str,
    graylog_tls: bool,
    graylog_level: str | LogLevel,
) -> GraylogSettings:
    """Resolve Graylog adapter settings with environment overrides."""
    enabled = env_bool("LOG_ENABLE_GRAYLOG", enable_graylog)
    protocol_str = (os.getenv("LOG_GRAYLOG_PROTOCOL") or graylog_protocol).lower()
    protocol = GraylogProtocol.from_str(protocol_str)
    tls = env_bool("LOG_GRAYLOG_TLS", graylog_tls)
    endpoint = coerce_graylog_endpoint(os.getenv("LOG_GRAYLOG_ENDPOINT"), graylog_endpoint)
    return GraylogSettings(enabled=enabled, endpoint=endpoint, protocol=protocol, tls=tls, level=graylog_level)


def resolve_queue_maxsize(default: int) -> int:
    """Return the configured queue capacity."""
    candidate = os.getenv("LOG_QUEUE_MAXSIZE")
    if candidate is None:
        return default
    try:
        value = int(candidate)
    except ValueError:
        return default
    return default if value <= 0 else value


def resolve_queue_policy(default: QueuePolicy) -> QueuePolicy:
    """Normalise queue full handling policy."""
    candidate = os.getenv("LOG_QUEUE_FULL_POLICY")
    if candidate is None:
        return default
    try:
        return QueuePolicy.from_str(candidate.strip().lower())
    except ValueError:
        return default


def resolve_queue_timeout(default: float | None) -> float | None:
    """Resolve queue put timeout from environment overrides."""
    candidate = os.getenv("LOG_QUEUE_PUT_TIMEOUT")
    if candidate is None:
        return default
    try:
        value = float(candidate)
    except ValueError:
        return default
    return None if value <= 0 else value


def resolve_queue_stop_timeout(default: float | None) -> float | None:
    """Resolve queue stop timeout from environment overrides."""
    candidate = os.getenv("LOG_QUEUE_STOP_TIMEOUT")
    if candidate is None:
        return default
    try:
        value = float(candidate)
    except ValueError:
        return default
    if value <= 0:
        return None
    return value


def resolve_rate_limit(value: tuple[int, float] | None) -> tuple[int, float] | None:
    """Return the effective rate limit tuple after env overrides."""
    return coerce_rate_limit(os.getenv("LOG_RATE_LIMIT"), value)


def resolve_scrub_patterns(custom: dict[str, str] | None) -> dict[str, str]:
    """Combine default, custom, and environment-provided scrub patterns."""
    merged = dict(DEFAULT_SCRUB_PATTERNS)
    if custom:
        merged.update(custom)
    env_patterns = parse_scrub_patterns(os.getenv("LOG_SCRUB_PATTERNS"))
    if env_patterns:
        merged.update(env_patterns)
    return merged


def env_bool(name: str, default: bool) -> bool:
    """Interpret an environment variable as a boolean flag."""
    candidate = os.getenv(name)
    if candidate is None:
        return default
    value = candidate.strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _split_kv_entries(raw: str) -> list[str]:
    """Split comma-separated key=value entries."""
    return [segment.strip() for segment in raw.split(",") if segment.strip()]


def _parse_kv_entry(entry: str) -> tuple[str, str] | None:
    """Parse a single key=value entry, returning None if invalid."""
    if "=" not in entry:
        return None
    key, value = entry.split("=", 1)
    key = key.strip()
    return (key, value.strip()) if key else None


@lru_cache(maxsize=8)
def parse_console_styles(raw: str | None) -> dict[str, str] | None:
    """Parse environment-provided console styles."""
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for entry in _split_kv_entries(raw):
        parsed = _parse_kv_entry(entry)
        if parsed:
            mapping[parsed[0].upper()] = parsed[1]
    return mapping or None


@lru_cache(maxsize=8)
def parse_scrub_patterns(raw: str | None) -> dict[str, str] | None:
    """Parse environment-provided scrub patterns.

    Format: ``field=regex`` pairs separated by commas.
    """
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for entry in _split_kv_entries(raw):
        parsed = _parse_kv_entry(entry)
        if parsed:
            mapping[parsed[0]] = parsed[1] or r".+"
    return mapping or None


def coerce_graylog_endpoint(env_value: str | None, fallback: tuple[str, int] | None) -> tuple[str, int] | None:
    """Coerce Graylog endpoint definitions from env or fallback."""
    value = env_value or None
    if value is None:
        return fallback
    if ":" not in value:
        raise ValueError("LOG_GRAYLOG_ENDPOINT must be HOST:PORT")
    host, port_text = value.split(":", 1)
    host = host.strip()
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("LOG_GRAYLOG_ENDPOINT port must be an integer") from exc
    if port <= 0:
        raise ValueError("LOG_GRAYLOG_ENDPOINT port must be positive")
    return host, port


def coerce_rate_limit(env_value: str | None, fallback: tuple[int, float] | None) -> tuple[int, float] | None:
    """Coerce rate limit tuples from environment overrides."""
    if not env_value:
        return fallback
    if ":" not in env_value:
        raise ValueError("LOG_RATE_LIMIT must be MAX:WINDOW_SECONDS")
    max_text, window_text = env_value.split(":", 1)
    try:
        max_events = int(max_text)
        window = float(window_text)
    except ValueError as exc:
        raise ValueError("LOG_RATE_LIMIT must be MAX:WINDOW_SECONDS with numeric values") from exc
    if max_events <= 0 or window <= 0:
        raise ValueError("LOG_RATE_LIMIT values must be positive")
    return max_events, window


def _merge_styles(
    explicit_styles: dict[str, str] | None,
    env_styles: dict[str, str] | None,
) -> dict[str, str]:
    """Merge explicit and environment styles into a single dict."""
    styles: dict[str, str] = {}
    if explicit_styles:
        styles.update(explicit_styles)
    if env_styles:
        styles.update(env_styles)
    return styles


def _apply_theme_defaults(styles: dict[str, str], theme: str) -> None:
    """Apply theme palette defaults to styles dict without overwriting existing."""
    theme_key = theme.strip().lower()
    palette = CONSOLE_STYLE_THEMES.get(theme_key)
    if palette:
        for level, value in palette.items():
            styles.setdefault(level.upper(), value)


def resolve_console_palette(
    theme: str | None,
    explicit_styles: dict[str, str] | None,
    env_styles: dict[str, str] | None,
) -> tuple[str | None, dict[str, str] | None]:
    """Resolve final console theme and styles."""
    styles = _merge_styles(explicit_styles, env_styles)
    resolved_theme = theme or (os.getenv("LOG_CONSOLE_THEME") if not styles else None)
    if resolved_theme:
        _apply_theme_defaults(styles, resolved_theme)
    return resolved_theme, styles or None


__all__ = [
    "build_runtime_settings",
    "service_and_environment",
    "resolve_levels",
    "resolve_feature_flags",
    "resolve_console",
    "resolve_dump_defaults",
    "resolve_graylog",
    "resolve_queue_maxsize",
    "resolve_queue_policy",
    "resolve_queue_timeout",
    "resolve_queue_stop_timeout",
    "resolve_rate_limit",
    "resolve_scrub_patterns",
    "env_bool",
    "parse_console_styles",
    "parse_scrub_patterns",
    "coerce_graylog_endpoint",
    "coerce_rate_limit",
    "resolve_console_palette",
]
