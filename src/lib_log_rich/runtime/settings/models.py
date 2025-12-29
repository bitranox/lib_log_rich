"""Data models shared by runtime settings resolution helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.application.use_cases._types import DiagnosticCallback
from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.enums import ConsoleStream, GraylogProtocol, QueuePolicy

DiagnosticHook = DiagnosticCallback | None


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

# PayloadLimits default values
# These limits protect against unbounded memory growth and ensure predictable performance
DEFAULT_MESSAGE_MAX_CHARS = 4096  # Maximum message length before truncation
DEFAULT_EXTRA_MAX_KEYS = 25  # Maximum number of extra fields per event
DEFAULT_EXTRA_MAX_VALUE_CHARS = 512  # Maximum string length for each extra field value
DEFAULT_EXTRA_MAX_DEPTH = 3  # Maximum nesting depth for nested extra structures
DEFAULT_EXTRA_MAX_TOTAL_BYTES = 8192  # Maximum total bytes for all extra fields combined
DEFAULT_CONTEXT_MAX_KEYS = 20  # Maximum number of context fields per event
DEFAULT_CONTEXT_MAX_VALUE_CHARS = 256  # Maximum string length for context field values
DEFAULT_STACKTRACE_MAX_FRAMES = 10  # Maximum stack frames to include in exception traces

# Queue and ring buffer defaults
# These values balance memory usage against event retention and throughput
DEFAULT_RING_BUFFER_SIZE = 25_000  # Number of recent events retained in memory for dumps
DEFAULT_QUEUE_MAXSIZE = 2048  # Maximum queued events before backpressure policies apply
DEFAULT_QUEUE_PUT_TIMEOUT = 1.0  # Seconds to wait when enqueuing (block policy)
DEFAULT_QUEUE_STOP_TIMEOUT = 5.0  # Seconds to wait for graceful queue drain on shutdown
DEFAULT_RING_BUFFER_FALLBACK = 1024  # Fallback ring buffer size when disabled


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
    stream: ConsoleStream = ConsoleStream.STDERR
    stream_target: object | None = None

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    @field_validator("styles")
    @classmethod
    def _normalise_styles(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        return {key.strip().upper(): val for key, val in value.items() if key.strip()}

    @model_validator(mode="after")
    def _validate_stream_target(self) -> ConsoleAppearance:
        if self.stream is ConsoleStream.CUSTOM:
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
    protocol: GraylogProtocol = Field(default=GraylogProtocol.TCP)
    tls: bool = False
    level: str | LogLevel = Field(default=LogLevel.WARNING)

    model_config = ConfigDict(frozen=True)

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
    message_max_chars: int = DEFAULT_MESSAGE_MAX_CHARS
    extra_max_keys: int = DEFAULT_EXTRA_MAX_KEYS
    extra_max_value_chars: int = DEFAULT_EXTRA_MAX_VALUE_CHARS
    extra_max_depth: int = DEFAULT_EXTRA_MAX_DEPTH
    extra_max_total_bytes: int | None = DEFAULT_EXTRA_MAX_TOTAL_BYTES
    context_max_keys: int = DEFAULT_CONTEXT_MAX_KEYS
    context_max_value_chars: int = DEFAULT_CONTEXT_MAX_VALUE_CHARS
    stacktrace_max_frames: int = DEFAULT_STACKTRACE_MAX_FRAMES

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
    ring_buffer_size: int = DEFAULT_RING_BUFFER_SIZE
    enable_journald: bool = False
    enable_eventlog: bool = False
    enable_graylog: bool = False
    graylog_protocol: str = "tcp"
    graylog_tls: bool = False
    queue_enabled: bool = True
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE
    queue_full_policy: str = "block"
    queue_put_timeout: float | None = DEFAULT_QUEUE_PUT_TIMEOUT
    queue_stop_timeout: float | None = DEFAULT_QUEUE_STOP_TIMEOUT
    force_color: bool = False
    no_color: bool = False
    console_styles: Mapping[str, str] | Mapping[LogLevel, str] | None = None
    console_theme: str | None = "dark"
    console_format_preset: str | None = None
    console_format_template: str | None = None
    console_stream: str = "stderr"
    console_stream_target: object | None = None
    scrub_patterns: dict[str, str] | None = None
    dump_format_preset: str | None = None
    dump_format_template: str | None = None
    rate_limit: tuple[int, float] | None = None
    payload_limits: PayloadLimits | Mapping[str, Any] | None = None
    diagnostic_hook: DiagnosticHook = None
    console_adapter_factory: Callable[[ConsoleAppearance], ConsolePort] | None = None

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
    rate_limit: tuple[int, float] | None = None
    limits: PayloadLimits = Field(default_factory=PayloadLimits)
    scrub_patterns: dict[str, str] = Field(default_factory=dict)
    diagnostic_hook: DiagnosticHook = None
    console_factory: Callable[[ConsoleAppearance], ConsolePort] | None = None
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE
    queue_full_policy: QueuePolicy = Field(default=QueuePolicy.BLOCK)
    queue_put_timeout: float | None = DEFAULT_QUEUE_PUT_TIMEOUT
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
    def _validate_rate_limit(cls, value: tuple[int, float] | None) -> tuple[int, float] | None:
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
