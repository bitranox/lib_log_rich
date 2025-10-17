"""Data models shared by runtime settings resolution helpers."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain import LogLevel

DiagnosticHook = Optional[Callable[[str, dict[str, Any]], None]]


def coerce_console_styles_input(
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
    stream: str = "stderr"
    stream_target: object | None = None

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    @field_validator("styles")
    @classmethod
    def _normalise_styles(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        return {key.strip().upper(): val for key, val in value.items() if key.strip()}

    @field_validator("stream")
    @classmethod
    def _normalise_stream(cls, value: str) -> str:
        candidate = value.strip().lower()
        if candidate not in {"stdout", "stderr", "both", "custom", "none"}:
            raise ValueError("stream must be one of 'stdout', 'stderr', 'both', 'custom', or 'none'")
        return candidate

    @model_validator(mode="after")
    def _validate_stream_target(self) -> "ConsoleAppearance":
        if self.stream == "custom":
            if self.stream_target is None:
                raise ValueError("stream_target must be provided when stream='custom'")
            if not hasattr(self.stream_target, "write"):
                raise ValueError("stream_target must implement a write() method")
        elif self.stream_target is not None:
            raise ValueError("stream_target is only supported when stream='custom'")
        return self


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


class RuntimeConfig(BaseModel):
    """Declarative configuration consumed by :func:`lib_log_rich.init`."""

    service: str
    environment: str
    console_level: str | LogLevel = LogLevel.INFO
    backend_level: str | LogLevel = LogLevel.WARNING
    graylog_endpoint: tuple[str, int] | None = None
    graylog_level: str | LogLevel = LogLevel.WARNING
    enable_ring_buffer: bool = True
    ring_buffer_size: int = 25_000
    enable_journald: bool = False
    enable_eventlog: bool = False
    enable_graylog: bool = False
    graylog_protocol: str = "tcp"
    graylog_tls: bool = False
    queue_enabled: bool = True
    queue_maxsize: int = 2048
    queue_full_policy: str = "block"
    queue_put_timeout: float | None = 1.0
    queue_stop_timeout: float | None = 5.0
    force_color: bool = False
    no_color: bool = False
    console_styles: Mapping[str, str] | Mapping[LogLevel, str] | None = None
    console_theme: str | None = None
    console_format_preset: str | None = None
    console_format_template: str | None = None
    console_stream: str = "stderr"
    console_stream_target: object | None = None
    scrub_patterns: Optional[dict[str, str]] = None
    dump_format_preset: str | None = None
    dump_format_template: str | None = None
    rate_limit: Optional[tuple[int, float]] = None
    payload_limits: PayloadLimits | Mapping[str, Any] | None = None
    diagnostic_hook: DiagnosticHook = None
    console_adapter_factory: Callable[["ConsoleAppearance"], ConsolePort] | None = None

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)


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
    console_factory: Callable[["ConsoleAppearance"], ConsolePort] | None = None
    queue_maxsize: int = 2048
    queue_full_policy: str = Field(default="block")
    queue_put_timeout: float | None = 1.0
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


__all__ = [
    "ConsoleAppearance",
    "DEFAULT_SCRUB_PATTERNS",
    "DiagnosticHook",
    "DumpDefaults",
    "FeatureFlags",
    "GraylogSettings",
    "PayloadLimits",
    "RuntimeConfig",
    "RuntimeSettings",
    "coerce_console_styles_input",
]
