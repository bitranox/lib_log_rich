"""Settings resolution helpers for the logging runtime."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

DiagnosticHook = Optional[Callable[[str, dict[str, Any]], None]]


def _coerce_console_styles_input(styles: Mapping[str, str] | Mapping[LogLevel, str] | None) -> dict[str, str] | None:
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


@dataclass(frozen=True)
class FeatureFlags:
    """Toggle blocks that influence adapter wiring."""

    queue: bool
    ring_buffer: bool
    journald: bool
    eventlog: bool


@dataclass(frozen=True)
class ConsoleAppearance:
    """Console styling knobs resolved from parameters and environment."""

    force_color: bool
    no_color: bool
    theme: str | None
    styles: dict[str, str] | None
    format_preset: str | None
    format_template: str | None


@dataclass(frozen=True)
class DumpDefaults:
    """Default dump formatting derived from configuration."""

    format_preset: str
    format_template: str | None


@dataclass(frozen=True)
class GraylogSettings:
    """Options required to initialise the Graylog adapter."""

    enabled: bool
    endpoint: tuple[str, int] | None
    protocol: str
    tls: bool
    level: str | LogLevel


@dataclass(frozen=True)
class RuntimeSettings:
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
    rate_limit: Optional[tuple[int, float]]
    scrub_patterns: dict[str, str]
    diagnostic_hook: DiagnosticHook
    queue_maxsize: int
    queue_full_policy: str
    queue_put_timeout: float | None
    queue_stop_timeout: float | None


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
    diagnostic_hook: DiagnosticHook = None,
) -> RuntimeSettings:
    """Blend parameters, environment overrides, and platform guards."""

    service_value, environment_value = _service_and_environment(service, environment)
    console_level_value, backend_level_value, graylog_level_value = _resolve_levels(console_level, backend_level, graylog_level)
    flags = _resolve_feature_flags(
        enable_ring_buffer=enable_ring_buffer,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        queue_enabled=queue_enabled,
    )
    ring_buffer_env = os.getenv("LOG_RING_BUFFER_SIZE")
    if ring_buffer_env is not None:
        try:
            ring_size = int(ring_buffer_env)
        except ValueError as exc:
            raise ValueError("LOG_RING_BUFFER_SIZE must be an integer") from exc
        source_label = "LOG_RING_BUFFER_SIZE"
    else:
        ring_size = ring_buffer_size
        source_label = "ring_buffer_size"
    if ring_size <= 0:
        raise ValueError(f"{source_label} must be positive")
    queue_size = _resolve_queue_maxsize(queue_maxsize)
    queue_policy = _resolve_queue_policy(queue_full_policy)
    queue_timeout_value = _resolve_queue_timeout(queue_put_timeout)
    queue_stop_timeout_value = _resolve_queue_stop_timeout(queue_stop_timeout)
    console = _resolve_console(
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
    return RuntimeSettings(
        service=service_value,
        environment=environment_value,
        console_level=console_level_value,
        backend_level=backend_level_value,
        graylog_level=graylog_level_value,
        ring_buffer_size=ring_size,
        console=console,
        dump=dump_defaults,
        graylog=graylog_settings,
        flags=flags,
        rate_limit=rate_limit_value,
        scrub_patterns=patterns,
        diagnostic_hook=diagnostic_hook,
        queue_maxsize=queue_size,
        queue_full_policy=queue_policy,
        queue_put_timeout=queue_timeout_value,
        queue_stop_timeout=queue_stop_timeout_value,
    )


def _service_and_environment(service: str, environment: str) -> tuple[str, str]:
    """Resolve service/environment using env overrides when present.

    Parameters
    ----------
    service:
        Default service name supplied by ``init``.
    environment:
        Default environment name supplied by ``init``.

    Returns
    -------
    tuple[str, str]
        Effective ``(service, environment)`` pair respecting overrides.

    Examples
    --------
    >>> _ = os.environ.pop('LOG_SERVICE', None)  # ensure unset
    >>> _service_and_environment('svc', 'prod')
    ('svc', 'prod')
    """
    return os.getenv("LOG_SERVICE", service), os.getenv("LOG_ENVIRONMENT", environment)


def _resolve_levels(
    console_level: str | LogLevel,
    backend_level: str | LogLevel,
    graylog_level: str | LogLevel,
) -> tuple[str | LogLevel, str | LogLevel, str | LogLevel]:
    """Apply environment overrides to severity thresholds.

    Returns
    -------
    tuple[str | LogLevel, str | LogLevel, str | LogLevel]
        Console, backend, and Graylog thresholds after environment overrides.

    Examples
    --------
    >>> _resolve_levels('info', 'warning', 'error')
    ('info', 'warning', 'error')
    """
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
    """Determine adapter feature flags with platform guards.

    Why
    ---
    Some adapters are platform-specific (journald vs Windows Event Log). The
    system design mandates mutually exclusive defaults so there is no confusion
    during cross-platform development.

    Returns
    -------
    FeatureFlags
        Frozen dataclass capturing the effective toggle set.

    Examples
    --------
    >>> flags = _resolve_feature_flags(
    ...     enable_ring_buffer=True,
    ...     enable_journald=True,
    ...     enable_eventlog=True,
    ...     queue_enabled=True,
    ... )
    >>> isinstance(flags, FeatureFlags)
    True
    """
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
    """Blend console formatting inputs with environment overrides.

    Returns
    -------
    ConsoleAppearance
        Frozen dataclass capturing force/no-colour flags, theme, style map, and
        formatting preset/template.

    Examples
    --------
    >>> appearance = _resolve_console(
    ...     force_color=False,
    ...     no_color=False,
    ...     console_theme='classic',
    ...     console_styles=None,
    ...     console_format_preset='full',
    ...     console_format_template=None,
    ... )
    >>> appearance.format_preset
    'full'
    """
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
    """Determine dump format defaults respecting environment overrides.

    Returns
    -------
    DumpDefaults
        Dataclass capturing the effective preset and optional template.

    Examples
    --------
    >>> defaults = _resolve_dump_defaults(dump_format_preset=None, dump_format_template=None)
    >>> isinstance(defaults, DumpDefaults)
    True
    """
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
    """Resolve Graylog adapter settings with environment overrides.

    Returns
    -------
    GraylogSettings
        Dataclass describing enablement, endpoint, protocol, TLS flag, and level.

    Examples
    --------
    >>> settings = _resolve_graylog(
    ...     enable_graylog=False,
    ...     graylog_endpoint=None,
    ...     graylog_protocol='udp',
    ...     graylog_tls=False,
    ...     graylog_level='error',
    ... )
    >>> isinstance(settings, GraylogSettings)
    True
    """
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
    """Return the effective rate limit tuple after env overrides.

    Parameters
    ----------
    value:
        Tuple provided via ``init`` or ``None`` to disable limiting.

    Returns
    -------
    Optional[tuple[int, float]]
        ``(max_events, window_seconds)`` pair or ``None``.

    Examples
    --------
    >>> _resolve_rate_limit((10, 1.0))
    (10, 1.0)
    """
    return _coerce_rate_limit(os.getenv("LOG_RATE_LIMIT"), value)


def _resolve_scrub_patterns(custom: Optional[dict[str, str]]) -> dict[str, str]:
    """Combine default, custom, and environment-provided scrub patterns.

    Parameters
    ----------
    custom:
        Mapping supplied via ``init`` overriding defaults.

    Returns
    -------
    dict[str, str]
        Aggregated mapping from field name to regex pattern.

    Examples
    --------
    >>> patterns = _resolve_scrub_patterns({'api_key': r'.+'})
    >>> 'password' in patterns and 'api_key' in patterns
    True
    """
    merged = dict(DEFAULT_SCRUB_PATTERNS)
    if custom:
        merged.update(custom)
    env_patterns = _parse_scrub_patterns(os.getenv("LOG_SCRUB_PATTERNS"))
    if env_patterns:
        merged.update(env_patterns)
    return merged


def _env_bool(name: str, default: bool) -> bool:
    """Interpret an environment variable as a boolean flag.

    Parameters
    ----------
    name:
        Environment variable key.
    default:
        Value returned when the variable is unset.

    Returns
    -------
    bool
        ``True`` when the variable equals one of ``{"1","true","yes","on"}``.

    Examples
    --------
    >>> _env_bool('NON_EXISTENT_FLAG', True)
    True
    """
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_console_styles(raw: str | None) -> dict[str, str]:
    """Parse comma-separated ``LEVEL=style`` entries into a mapping.

    Parameters
    ----------
    raw:
        Environment string such as ``"INFO=green,ERROR=red"``.

    Returns
    -------
    dict[str, str]
        Normalised mapping with whitespace trimmed.

    Examples
    --------
    >>> _parse_console_styles('INFO=green, ERROR=red')
    {'INFO': 'green', 'ERROR': 'red'}
    """
    if not raw:
        return {}
    result: dict[str, str] = {}
    for chunk in raw.split(","):
        if not chunk.strip() or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            result[key] = value
    return result


def _parse_scrub_patterns(raw: str | None) -> dict[str, str]:
    """Parse ``FIELD=regex`` pairs from environment variables.

    Parameters
    ----------
    raw:
        Environment string such as ``"password=.?,token=.+"``.

    Returns
    -------
    dict[str, str]
        Mapping from field to regex pattern.

    Examples
    --------
    >>> _parse_scrub_patterns('token=.+, password=.+')
    {'token': '.+', 'password': '.+'}
    """
    if not raw:
        return {}
    result: dict[str, str] = {}
    for chunk in raw.split(","):
        if not chunk.strip() or "=" not in chunk:
            continue
        key, pattern = chunk.split("=", 1)
        key = key.strip()
        pattern = pattern.strip()
        if key and pattern:
            result[key] = pattern
    return result


def _merge_console_styles(
    explicit: Mapping[str, str] | None,
    env_styles: Mapping[str, str],
) -> dict[str, str]:
    """Combine explicit style overrides with environment-defined styles.

    Parameters
    ----------
    explicit:
        Styles provided via configuration (may include ``LogLevel`` keys).
    env_styles:
        Styles parsed from environment variables.

    Returns
    -------
    dict[str, str]
        Normalised mapping keyed by uppercase level names.

    Examples
    --------
    >>> _merge_console_styles({'INFO': 'green'}, {'error': 'red'})
    {'INFO': 'green', 'ERROR': 'red'}
    """
    merged: dict[str, str] = {}

    def _normalise_key(key: str | LogLevel) -> str:
        """Return the uppercase string key for style dictionaries.

        Parameters
        ----------
        key:
            Possibly a :class:`LogLevel` or string.

        Returns
        -------
        str
            Uppercase representation suitable for lookup.
        """
        if isinstance(key, LogLevel):
            return key.name
        return key.strip().upper()

    if explicit:
        for key, value in explicit.items():
            norm = _normalise_key(key)
            if norm:
                merged[norm] = value

    for key, value in env_styles.items():
        norm = key.strip().upper()
        if norm:
            merged[norm] = value

    return merged


def _resolve_console_palette(
    theme: str | None,
    explicit: Mapping[str, str] | None,
    env_styles: Mapping[str, str],
) -> tuple[str | None, dict[str, str] | None]:
    """Derive the effective theme key and style overrides.

    Parameters
    ----------
    theme:
        Optional theme name looked up in :data:`CONSOLE_STYLE_THEMES`.
    explicit:
        Style overrides provided via configuration.
    env_styles:
        Style overrides parsed from environment.

    Returns
    -------
    tuple[str | None, dict[str, str] | None]
        Resolved theme key (lowercase) and combined style mapping.

    Examples
    --------
    >>> _resolve_console_palette('classic', None, {})[0]
    'classic'
    """
    resolved_theme: str | None = None
    base: dict[str, str] = {}
    if theme:
        key = theme.strip().lower()
        if key:
            try:
                palette = CONSOLE_STYLE_THEMES[key]
            except KeyError as exc:
                raise ValueError(f"Unknown console theme: {theme!r}") from exc
            resolved_theme = key
            base.update({level.upper(): style for level, style in palette.items()})
    overrides = _merge_console_styles(explicit, env_styles)
    if overrides:
        base.update(overrides)
    return resolved_theme, base or None


def _coerce_rate_limit(value: str | None, fallback: Optional[tuple[int, float]]) -> Optional[tuple[int, float]]:
    """Parse ``MAX/SECONDS`` strings into rate limit tuples.

    Parameters
    ----------
    value:
        Environment string such as ``"10/1.5"`` or ``None``.
    fallback:
        Value returned when parsing fails.

    Returns
    -------
    Optional[tuple[int, float]]
        Parsed tuple or the provided fallback.

    Examples
    --------
    >>> _coerce_rate_limit('5/2', None)
    (5, 2.0)
    >>> _coerce_rate_limit('invalid', (1, 1.0))
    (1, 1.0)
    """
    if value is None:
        return fallback
    candidate = value.strip()
    if not candidate:
        return fallback
    if "/" not in candidate:
        return fallback
    count_str, window_str = candidate.split("/", 1)
    try:
        count = int(count_str)
        window = float(window_str)
    except ValueError:
        return fallback
    if count <= 0 or window <= 0:
        return fallback
    return count, window


def _coerce_graylog_endpoint(raw: str | None, fallback: tuple[str, int] | None) -> tuple[str, int] | None:
    """Parse ``HOST:PORT`` strings into endpoint tuples.

    Parameters
    ----------
    raw:
        Environment string; may be ``None``.
    fallback:
        Tuple provided via ``init`` or ``None``.

    Returns
    -------
    tuple[str, int] | None
        Parsed endpoint tuple or ``fallback`` when parsing fails.

    Raises
    ------
    ValueError
        If ``raw`` is malformed (missing host or integer port).

    Examples
    --------
    >>> _coerce_graylog_endpoint('localhost:12201', None)
    ('localhost', 12201)
    >>> _coerce_graylog_endpoint('', ('fallback', 9000))
    ('fallback', 9000)
    """
    if raw is None and fallback is None:
        return None
    candidate = raw if raw is not None else ""
    candidate = candidate.strip()
    if not candidate:
        return fallback
    host, _, port = candidate.partition(":")
    if not host or not port.isdigit():
        raise ValueError("Expected HOST:PORT for Graylog endpoint")
    return host, int(port)


__all__ = [
    "ConsoleAppearance",
    "DEFAULT_SCRUB_PATTERNS",
    "DiagnosticHook",
    "DumpDefaults",
    "FeatureFlags",
    "GraylogSettings",
    "RuntimeSettings",
    "build_runtime_settings",
]
