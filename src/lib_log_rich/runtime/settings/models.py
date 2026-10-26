"""Data models shared by runtime settings resolution helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.application.use_cases._types import DiagnosticCallback
from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.enums import ConsoleStream, ConsoleTheme, FormatPreset, GraylogProtocol, QueuePolicy

DiagnosticHook = DiagnosticCallback | None

# The single-kind arms keep a caller's dict[str, str] or dict[LogLevel, str] assignable
# (Mapping keys are invariant to a type checker). The mixed arm is for pydantic: with it a
# mapping holding both kinds validates as-is, where otherwise the lax str arm would coerce a
# LogLevel key to its int value ('20'), which no longer names a level.
ConsoleStylesInput = Mapping[str, str] | Mapping[LogLevel, str] | Mapping[str | LogLevel, str]


def _style_key(key: str) -> str:
    """Return a style key in the spelling the console looks levels up by; blank stays blank."""
    return key.strip().upper()


def coerce_console_styles_input(
    styles: ConsoleStylesInput | None,
) -> dict[str, str] | None:
    """Normalise configured console styles to level-name keys, once, on entry.

    This is where a caller's styles enter settings resolution; everything after it
    (the merge with ``LOG_CONSOLE_STYLES`` and the palette defaults) relies on the
    keys already being level names.

    Args:
        styles: Styles keyed by level name in any case and padding, or by
            :class:`LogLevel`.

    Returns:
        The styles keyed by uppercase level name, blank keys dropped; ``None``
        when ``styles`` is empty or ``None``.

    Example:
        >>> coerce_console_styles_input({LogLevel.INFO: "cyan", " warning ": "yellow"})
        {'INFO': 'cyan', 'WARNING': 'yellow'}
    """
    if not styles:
        return None
    normalised: dict[str, str] = {}
    for key, value in styles.items():
        if isinstance(key, LogLevel):
            normalised[key.name] = value
        else:
            candidate = _style_key(key)
            if candidate:
                normalised[candidate] = value
    return normalised


DEFAULT_SCRUB_PATTERNS: dict[str, str] = {
    "password": r".+",  # nosec: B105
    "secret": r".+",  # nosec: B105
    "token": r".+",  # nosec: B105
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
    theme: ConsoleTheme | None = None
    styles: dict[str, str] | None = None
    # ``styles`` with the theme's palette filled in renders the console. Dumps need the
    # user's own styles without that fill, or a dump's theme could never replace it.
    configured_styles: dict[str, str] | None = None
    format_preset: FormatPreset | None = None
    format_template: str | None = None
    stream: ConsoleStream = ConsoleStream.STDERR
    stream_target: object | None = None

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    @field_validator("styles", "configured_styles")
    @classmethod
    def _normalise_styles(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        # A ConsoleAppearance can be built directly (a custom console factory's
        # tests do), so it is an entry point of its own and normalises too.
        if value is None:
            return None
        return {_style_key(key): val for key, val in value.items() if _style_key(key)}

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

    format_preset: FormatPreset = FormatPreset.FULL
    format_template: str | None = None

    model_config = ConfigDict(frozen=True)


class GraylogSettings(BaseModel):
    """Options required to initialise the Graylog adapter."""

    enabled: bool
    endpoint: tuple[str, int] | None = None
    protocol: GraylogProtocol = Field(default=GraylogProtocol.TCP)
    tls: bool = False
    level: LogLevel = Field(default=LogLevel.WARNING)

    model_config = ConfigDict(frozen=True)

    @field_validator("level", mode="before")
    @classmethod
    def _parse_level(cls, value: object) -> LogLevel:
        return LogLevel.coerce(value)

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

    @model_validator(mode="after")
    def _require_endpoint_when_enabled(self) -> GraylogSettings:
        # Without an endpoint the runtime builds no Graylog sink, so an operator who
        # enables Graylog and forgets the endpoint would lose every event silently.
        if self.enabled and self.endpoint is None:
            raise ValueError("Graylog is enabled but no endpoint is set (graylog_endpoint or LOG_GRAYLOG_ENDPOINT)")
        return self

    @model_validator(mode="after")
    def _require_tls_over_tcp(self) -> GraylogSettings:
        # Mirrors the condition under which the runtime builds the adapter, which
        # refuses TLS over UDP; a disabled Graylog builds none.
        if self.enabled and self.endpoint is not None and self.protocol is GraylogProtocol.UDP and self.tls:
            raise ValueError("TLS is only supported for TCP Graylog transport")
        return self


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

    # A mapping from a config file reaches this model; a misspelled limit is refused by name
    # rather than dropped.
    model_config = ConfigDict(frozen=True, extra="forbid")

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
    ring_buffer_checkpoint: Path | None = None
    enable_journald: bool = False
    enable_eventlog: bool = False
    enable_graylog: bool = False
    graylog_protocol: GraylogProtocol = GraylogProtocol.TCP
    graylog_tls: bool = False
    queue_enabled: bool = True
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE
    queue_full_policy: QueuePolicy = QueuePolicy.BLOCK
    queue_put_timeout: float | None = DEFAULT_QUEUE_PUT_TIMEOUT
    queue_stop_timeout: float | None = DEFAULT_QUEUE_STOP_TIMEOUT
    force_color: bool = False
    no_color: bool = False
    console_styles: ConsoleStylesInput | None = None
    console_theme: ConsoleTheme | None = ConsoleTheme.DARK
    console_format_preset: FormatPreset | None = None
    console_format_template: str | None = None
    console_stream: ConsoleStream = ConsoleStream.STDERR
    console_stream_target: object | None = None
    scrub_patterns: dict[str, str] | None = None
    dump_format_preset: FormatPreset | None = None
    dump_format_template: str | None = None
    rate_limit: tuple[int, float] | None = None
    payload_limits: PayloadLimits | None = None
    diagnostic_hook: DiagnosticHook = None
    console_adapter_factory: Callable[[ConsoleAppearance], ConsolePort] | None = None

    # extra="forbid": a misspelled setting is refused by name instead of being dropped, which
    # would leave the intended value silently at its default.
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    # Config files and CLI options spell these in any case ("UDP", "Drop"), which
    # the plain enum validation would refuse; ``from_str`` also names the valid
    # values when it refuses one.
    @field_validator("graylog_protocol", mode="before")
    @classmethod
    def _parse_graylog_protocol(cls, value: object) -> object:
        return GraylogProtocol.from_str(value) if isinstance(value, str) else value

    @field_validator("queue_full_policy", mode="before")
    @classmethod
    def _parse_queue_full_policy(cls, value: object) -> object:
        return QueuePolicy.from_str(value) if isinstance(value, str) else value

    @field_validator("console_stream", mode="before")
    @classmethod
    def _parse_console_stream(cls, value: object) -> object:
        return ConsoleStream.from_str(value) if isinstance(value, str) else value

    @field_validator("ring_buffer_checkpoint", mode="before")
    @classmethod
    def _parse_ring_buffer_checkpoint(cls, value: object) -> object:
        # An empty string is what TOML spells "unset"; Path("") would name the current directory.
        if isinstance(value, str):
            return Path(value.strip()) if value.strip() else None
        return value

    @field_validator("console_format_preset", "dump_format_preset", mode="before")
    @classmethod
    def _parse_format_preset(cls, value: object) -> object:
        # An empty string is what TOML spells "unset", as for the templates below.
        if isinstance(value, str):
            return FormatPreset.from_str(value) if value.strip() else None
        return value

    @field_validator("console_theme", mode="before")
    @classmethod
    def _parse_console_theme(cls, value: object) -> object:
        # An empty string is what TOML spells "unset", as for the presets above.
        if isinstance(value, str):
            return ConsoleTheme.from_str(value) if value.strip() else None
        return value

    @field_validator("console_format_template", "dump_format_template", mode="before")
    @classmethod
    def _empty_str_as_none(cls, value: str | None) -> str | None:
        """Coerce empty/whitespace-only strings to None for TOML compatibility."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("graylog_endpoint", "rate_limit", mode="before")
    @classmethod
    def _empty_seq_as_none(
        cls,
        value: tuple[str, int] | tuple[int, float] | list[str | int | float] | None,
    ) -> tuple[str, int] | tuple[int, float] | list[str | int | float] | None:
        """Coerce empty lists/tuples to None for TOML compatibility."""
        if isinstance(value, (list, tuple)) and not value:
            return None
        return value


class RuntimeSettings(BaseModel):
    """Snapshot of resolved configuration passed into the composition root."""

    service: str
    environment: str
    console_level: LogLevel
    backend_level: LogLevel
    graylog_level: LogLevel
    ring_buffer_size: int
    ring_buffer_checkpoint: Path | None = None
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

    @field_validator("console_level", "backend_level", "graylog_level", mode="before")
    @classmethod
    def _parse_level(cls, value: object) -> LogLevel:
        return LogLevel.coerce(value)

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
        patterns = {key: pattern for key, pattern in value.items() if key}
        for key, pattern in patterns.items():
            _require_compilable_scrub_pattern(key, pattern)
        return patterns

    @model_validator(mode="after")
    def _require_renderable_console(self) -> RuntimeSettings:
        # A custom console factory decides for itself what a style key means, so
        # the built-in console's vocabulary binds only when it is used.
        if self.console_factory is None:
            _require_level_style_keys(self.console)
        return self


def _require_compilable_scrub_pattern(key: str, pattern: str) -> None:
    """Refuse a pattern the scrubber could not compile.

    Keys that are blank once stripped are skipped, as the scrubber skips them.
    """
    if not key.strip():
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid scrub pattern for '{key}': {exc}") from exc


def _require_level_style_keys(console: ConsoleAppearance) -> None:
    """Refuse a console style keyed by anything other than a level name."""
    for key in console.styles or {}:
        if not _names_a_level(key):
            raise ValueError(f"Console style key is not a log level: {key!r}")


def _names_a_level(name: str) -> bool:
    """Return whether ``name`` parses as a level, by the rule the console applies."""
    try:
        LogLevel.from_name(name)
    except ValueError:
        return False
    return True


__all__ = [
    "DEFAULT_SCRUB_PATTERNS",
    "ConsoleAppearance",
    "ConsoleStylesInput",
    "DiagnosticHook",
    "DumpDefaults",
    "FeatureFlags",
    "GraylogSettings",
    "PayloadLimits",
    "RuntimeConfig",
    "RuntimeSettings",
    "coerce_console_styles_input",
]
