"""Settings resolution helpers for the logging runtime."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

DiagnosticHook = Optional[Callable[[str, dict[str, Any]], None]]


def _coerce_console_styles_input(
    styles: Mapping[str, str] | Mapping[LogLevel, str] | None,
) -> dict[str, str] | None:
    """Normalise console style mappings to uppercase string keys."""

    if not styles:
        return None
    normalised: dict[str, str] = {}
    for key, value in styles.items():
        if isinstance(key, LogLevel):
            normalised[key.name] = value
        else:
            candidate = key.strip().upper()
            if candidate:
                normalised[candidate] = value
    return normalised


DEFAULT_SCRUB_PATTERNS: dict[str, str] = {
    "password": r".+",
    "secret": r".+",
    "token": r".+",
}


class FeatureFlags(BaseModel):
    """Toggle blocks that influence adapter wiring."""

    queue: bool
    ring_buffer: bool
    journald: bool
    eventlog: bool

    model_config = ConfigDict(frozen=True)


class ConsoleAppearance(BaseModel):
    """Console styling knobs resolved from parameters and environment."""

    force_color: bool = False
    no_color: bool = False
    theme: str | None = None
    styles: dict[str, str] | None = None
    format_preset: str | None = None
    format_template: str | None = None

    model_config = ConfigDict(frozen=True)

    @field_validator("styles")
    @classmethod
    def _normalise_styles(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        return {key.strip().upper(): val for key, val in value.items() if key.strip()}


class DumpDefaults(BaseModel):
    """Default dump formatting derived from configuration."""

    format_preset: str
    format_template: str | None = None

    model_config = ConfigDict(frozen=True)


class GraylogSettings(BaseModel):
    """Options required to initialise the Graylog adapter."""

    enabled: bool
    endpoint: tuple[str, int] | None = None
    protocol: str = Field(default="tcp")
    tls: bool = False
    level: str | LogLevel = Field(default=LogLevel.WARNING)

    model_config = ConfigDict(frozen=True)

    @field_validator("protocol")
    @classmethod
    def _validate_protocol(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in {"tcp", "udp"}:
            raise ValueError("protocol must be 'tcp' or 'udp'")
        return candidate

    @field_validator("endpoint")
    @classmethod
    def _validate_endpoint(cls, value: tuple[str, int] | None) -> tuple[str, int] | None:
        if value is None:
            return None
        host, port = value
        if not host:
            raise ValueError("Graylog endpoint host must be non-empty")
        if port <= 0:
            raise ValueError("Graylog endpoint port must be positive")
        return host, port


class PayloadLimits(BaseModel):
    """Configuration for guarding per-event payload sizes."""

    truncate_message: bool = True
    message_max_chars: int = 4096
    extra_max_keys: int = 25
    extra_max_value_chars: int = 512
    extra_max_depth: int = 3
    extra_max_total_bytes: int | None = 8192
    context_max_keys: int = 20
    context_max_value_chars: int = 256
    stacktrace_max_frames: int = 10

    model_config = ConfigDict(frozen=True)

    @field_validator(
        "message_max_chars",
        "extra_max_keys",
        "extra_max_value_chars",
        "extra_max_depth",
        "context_max_keys",
        "context_max_value_chars",
        "stacktrace_max_frames",
    )
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("payload limit values must be positive")
        return value

    @field_validator("extra_max_total_bytes")
    @classmethod
    def _positive_or_none(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("extra_max_total_bytes must be positive or None")
        return value


class RuntimeSettings(BaseModel):
    """Snapshot of resolved configuration passed into the composition root."""

    service: str
    environment: str
    console_level: str | LogLevel
    backend_level: str | LogLevel
    graylog_level: str | LogLevel
    ring_buffer_size: int
    console: ConsoleAppearance
    dump: DumpDefaults
    graylog: GraylogSettings
    flags: FeatureFlags
    rate_limit: Optional[tuple[int, float]] = None
    limits: PayloadLimits = Field(default_factory=PayloadLimits)
    scrub_patterns: dict[str, str] = Field(default_factory=dict)
    diagnostic_hook: DiagnosticHook = None
    queue_maxsize: int = 2048
    queue_full_policy: str = Field(default="block")
    queue_put_timeout: float | None = None
    queue_stop_timeout: float | None = None

    model_config = ConfigDict(frozen=True)

    @field_validator("service")
    @classmethod
    def _require_service(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("service must not be empty")
        return stripped

    @field_validator("environment")
    @classmethod
    def _require_environment(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("environment must not be empty")
        return stripped

    @field_validator("ring_buffer_size")
    @classmethod
    def _positive_ring_buffer(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("ring_buffer_size must be positive")
        return value

    @field_validator("queue_maxsize")
    @classmethod
    def _positive_queue_maxsize(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("queue_maxsize must be positive")
        return value

    @field_validator("queue_full_policy")
    @classmethod
    def _validate_policy(cls, value: str) -> str:
        policy = value.strip().lower()
        if policy not in {"block", "drop"}:
            raise ValueError("queue_full_policy must be 'block' or 'drop'")
        return policy

    @field_validator("queue_put_timeout", "queue_stop_timeout")
    @classmethod
    def _normalise_timeout(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0:
            return None
        return value

    @field_validator("rate_limit")
    @classmethod
    def _validate_rate_limit(cls, value: Optional[tuple[int, float]]) -> Optional[tuple[int, float]]:
        if value is None:
            return None
        max_events, window = value
        if max_events <= 0:
            raise ValueError("rate_limit[0] must be positive")
        if window <= 0:
            raise ValueError("rate_limit[1] must be positive")
        return max_events, window

    @field_validator("scrub_patterns")
    @classmethod
    def _normalise_patterns(cls, value: dict[str, str]) -> dict[str, str]:
        return {str(key): str(pattern) for key, pattern in value.items() if str(key)}


def build_runtime_settings(
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
    queue_stop_timeout: float | None = 5.0,
    force_color: bool = False,
    no_color: bool = False,
    console_styles: Mapping[str, str] | Mapping[LogLevel, str] | None = None,
    console_theme: str | None = None,
    console_format_preset: str | None = None,
    console_format_template: str | None = None,
    scrub_patterns: Optional[dict[str, str]] = None,
    dump_format_preset: str | None = None,
    dump_format_template: str | None = None,
    rate_limit: Optional[tuple[int, float]] = None,
    payload_limits: PayloadLimits | Mapping[str, Any] | None = None,
    diagnostic_hook: DiagnosticHook = None,
) -> RuntimeSettings:
    """Blend parameters, environment overrides, and platform guards."""

    service_value, environment_value = _service_and_environment(service, environment)
    console_level_value, backend_level_value, graylog_level_value = _resolve_levels(
        console_level,
        backend_level,
        graylog_level,
    )

    ring_buffer_env = os.getenv("LOG_RING_BUFFER_SIZE")
    if ring_buffer_env is not None:
        try:
            ring_size = int(ring_buffer_env)
        except ValueError as exc:  # pragma: no cover - defensive guards
            raise ValueError("LOG_RING_BUFFER_SIZE must be an integer") from exc
        source_label = "LOG_RING_BUFFER_SIZE"
    else:
        ring_size = ring_buffer_size
        source_label = "ring_buffer_size"
    if ring_size <= 0:
        raise ValueError(f"{source_label} must be positive")

    flags = _resolve_feature_flags(
        enable_ring_buffer=enable_ring_buffer,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        queue_enabled=queue_enabled,
    )
    queue_size = _resolve_queue_maxsize(queue_maxsize)
    queue_policy = _resolve_queue_policy(queue_full_policy)
    queue_timeout_value = _resolve_queue_timeout(queue_put_timeout)
    queue_stop_timeout_value = _resolve_queue_stop_timeout(queue_stop_timeout)
    console_model = _resolve_console(
        force_color=force_color,
        no_color=no_color,
        console_theme=console_theme,
        console_styles=console_styles,
        console_format_preset=console_format_preset,
        console_format_template=console_format_template,
    )
    dump_defaults = _resolve_dump_defaults(
        dump_format_preset=dump_format_preset,
        dump_format_template=dump_format_template,
    )
    graylog_settings = _resolve_graylog(
        enable_graylog=enable_graylog,
        graylog_endpoint=graylog_endpoint,
        graylog_protocol=graylog_protocol,
        graylog_tls=graylog_tls,
        graylog_level=graylog_level_value,
    )
    rate_limit_value = _resolve_rate_limit(rate_limit)
    patterns = _resolve_scrub_patterns(scrub_patterns)
    if payload_limits is None:
        limits_model = PayloadLimits()
    elif isinstance(payload_limits, PayloadLimits):
        limits_model = payload_limits
    else:
        limits_model = PayloadLimits(**dict(payload_limits))

    try:
        return RuntimeSettings(
            service=service_value,
            environment=environment_value,
            console_level=console_level_value,
            backend_level=backend_level_value,
            graylog_level=graylog_level_value,
            ring_buffer_size=ring_size,
            console=console_model,
            dump=dump_defaults,
            graylog=graylog_settings,
            flags=flags,
            rate_limit=rate_limit_value,
            limits=limits_model,
            scrub_patterns=patterns,
            diagnostic_hook=diagnostic_hook,
            queue_maxsize=queue_size,
            queue_full_policy=queue_policy,
            queue_put_timeout=queue_timeout_value,
            queue_stop_timeout=queue_stop_timeout_value,
        )
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def _service_and_environment(service: str, environment: str) -> tuple[str, str]:
    """Return service/environment after environment overrides."""

    return os.getenv("LOG_SERVICE", service), os.getenv("LOG_ENVIRONMENT", environment)


def _resolve_levels(
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


def _resolve_feature_flags(
    *,
    enable_ring_buffer: bool,
    enable_journald: bool,
    enable_eventlog: bool,
    queue_enabled: bool,
) -> FeatureFlags:
    """Determine adapter feature flags with platform guards."""

    ring_buffer = _env_bool("LOG_RING_BUFFER_ENABLED", enable_ring_buffer)
    journald = _env_bool("LOG_ENABLE_JOURNALD", enable_journald)
    eventlog = _env_bool("LOG_ENABLE_EVENTLOG", enable_eventlog)
    queue = _env_bool("LOG_QUEUE_ENABLED", queue_enabled)
    if sys.platform.startswith("win"):
        journald = False
    else:
        eventlog = False
    return FeatureFlags(queue=queue, ring_buffer=ring_buffer, journald=journald, eventlog=eventlog)


def _resolve_console(
    *,
    force_color: bool,
    no_color: bool,
    console_theme: str | None,
    console_styles: Mapping[str, str] | Mapping[LogLevel, str] | None,
    console_format_preset: str | None,
    console_format_template: str | None,
) -> ConsoleAppearance:
    """Blend console formatting inputs with environment overrides."""

    force = _env_bool("LOG_FORCE_COLOR", force_color)
    no = _env_bool("LOG_NO_COLOR", no_color)
    env_styles = _parse_console_styles(os.getenv("LOG_CONSOLE_STYLES"))
    theme_override = os.getenv("LOG_CONSOLE_THEME")
    theme = theme_override or console_theme
    preset = os.getenv("LOG_CONSOLE_FORMAT_PRESET") or console_format_preset
    template = os.getenv("LOG_CONSOLE_FORMAT_TEMPLATE") or console_format_template
    explicit_styles = _coerce_console_styles_input(console_styles)
    resolved_theme, resolved_styles = _resolve_console_palette(theme, explicit_styles, env_styles)

    return ConsoleAppearance(
        force_color=force,
        no_color=no,
        theme=resolved_theme,
        styles=resolved_styles,
        format_preset=preset,
        format_template=template,
    )


def _resolve_dump_defaults(
    *,
    dump_format_preset: str | None,
    dump_format_template: str | None,
) -> DumpDefaults:
    """Determine dump format defaults respecting environment overrides."""

    preset = os.getenv("LOG_DUMP_FORMAT_PRESET") or dump_format_preset or "full"
    template = os.getenv("LOG_DUMP_FORMAT_TEMPLATE") or dump_format_template
    return DumpDefaults(format_preset=preset, format_template=template)


def _resolve_graylog(
    *,
    enable_graylog: bool,
    graylog_endpoint: tuple[str, int] | None,
    graylog_protocol: str,
    graylog_tls: bool,
    graylog_level: str | LogLevel,
) -> GraylogSettings:
    """Resolve Graylog adapter settings with environment overrides."""

    enabled = _env_bool("LOG_ENABLE_GRAYLOG", enable_graylog)
    protocol = (os.getenv("LOG_GRAYLOG_PROTOCOL") or graylog_protocol).lower()
    tls = _env_bool("LOG_GRAYLOG_TLS", graylog_tls)
    endpoint = _coerce_graylog_endpoint(os.getenv("LOG_GRAYLOG_ENDPOINT"), graylog_endpoint)
    return GraylogSettings(enabled=enabled, endpoint=endpoint, protocol=protocol, tls=tls, level=graylog_level)


def _resolve_queue_maxsize(default: int) -> int:
    """Return the configured queue capacity."""

    candidate = os.getenv("LOG_QUEUE_MAXSIZE")
    if candidate is None:
        return default
    try:
        value = int(candidate)
    except ValueError:
        return default
    return default if value <= 0 else value


def _resolve_queue_policy(default: str) -> str:
    """Normalise queue full handling policy."""

    candidate = os.getenv("LOG_QUEUE_FULL_POLICY")
    policy = (candidate or default).strip().lower()
    return policy if policy in {"block", "drop"} else default.lower()


def _resolve_queue_timeout(default: float | None) -> float | None:
    """Resolve queue put timeout from environment overrides."""

    candidate = os.getenv("LOG_QUEUE_PUT_TIMEOUT")
    if candidate is None:
        return default
    try:
        value = float(candidate)
    except ValueError:
        return default
    return None if value <= 0 else value


def _resolve_queue_stop_timeout(default: float | None) -> float | None:
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


def _resolve_rate_limit(value: Optional[tuple[int, float]]) -> Optional[tuple[int, float]]:
    """Return the effective rate limit tuple after env overrides."""

    return _coerce_rate_limit(os.getenv("LOG_RATE_LIMIT"), value)


def _resolve_scrub_patterns(custom: Optional[dict[str, str]]) -> dict[str, str]:
    """Combine default, custom, and environment-provided scrub patterns."""

    merged = dict(DEFAULT_SCRUB_PATTERNS)
    if custom:
        merged.update(custom)
    env_patterns = _parse_scrub_patterns(os.getenv("LOG_SCRUB_PATTERNS"))
    if env_patterns:
        merged.update(env_patterns)
    return merged


def _env_bool(name: str, default: bool) -> bool:
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


def _parse_console_styles(raw: str | None) -> dict[str, str] | None:
    """Parse environment-provided console styles."""

    if not raw:
        return None
    entries = [segment.strip() for segment in raw.split(",") if segment.strip()]
    mapping: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip().upper()
        if key:
            mapping[key] = value.strip()
    return mapping or None


def _parse_scrub_patterns(raw: str | None) -> dict[str, str] | None:
    """Parse environment-provided scrub patterns.

    Format: ``field=regex`` pairs separated by commas.
    """

    if not raw:
        return None
    entries = [segment.strip() for segment in raw.split(",") if segment.strip()]
    mapping: dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        if key:
            mapping[key] = value.strip() or r".+"
    return mapping or None


def _coerce_graylog_endpoint(env_value: str | None, fallback: tuple[str, int] | None) -> tuple[str, int] | None:
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


def _coerce_rate_limit(env_value: str | None, fallback: Optional[tuple[int, float]]) -> Optional[tuple[int, float]]:
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


def _resolve_console_palette(
    theme: str | None,
    explicit_styles: dict[str, str] | None,
    env_styles: dict[str, str] | None,
) -> tuple[str | None, dict[str, str] | None]:
    """Resolve final console theme and styles."""

    styles: dict[str, str] = {}
    if explicit_styles:
        styles.update(explicit_styles)
    if env_styles:
        styles.update(env_styles)

    resolved_theme = theme
    if not resolved_theme and not styles:
        session_theme = os.getenv("LOG_CONSOLE_THEME")
        resolved_theme = session_theme if session_theme else None

    if resolved_theme:
        theme_key = resolved_theme.strip().lower()
        palette = CONSOLE_STYLE_THEMES.get(theme_key)
        if palette:
            for level, value in palette.items():
                styles.setdefault(level.upper(), value)
    return resolved_theme, styles or None


__all__ = [
    "ConsoleAppearance",
    "DEFAULT_SCRUB_PATTERNS",
    "DiagnosticHook",
    "DumpDefaults",
    "FeatureFlags",
    "GraylogSettings",
    "PayloadLimits",
    "RuntimeSettings",
    "build_runtime_settings",
]
