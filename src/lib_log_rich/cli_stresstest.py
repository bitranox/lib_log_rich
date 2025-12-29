"""Stress-test TUI exercising runtime configuration under load.

Provide an interactive Textual interface to validate runtime wiring under load
and observe how configuration choices influence throughput, queueing, and dump
exports.

Contents:
    * Input normalisation helpers for reading environment defaults and Textual
      widgets.
    * Parsing routines that convert Textual form values into
      :class:`lib_log_rich.runtime.RuntimeConfig` instances plus dump filters.
    * Textual application factory that wires together the stress-test interface.

Note:
    Acts as the developer-facing observability lab described in
    ``docs/systemdesign/concept_architecture_plan.md``, allowing teams to vet new
    presets and limits before promoting them to production settings.

"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass as pydantic_dataclass
from rich.text import Text

from lib_log_rich.adapters.console import AsyncQueueConsoleAdapter, QueueConsoleAdapter
from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.application.use_cases._types import ProcessResult
from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.dump_filter import FilterSpecValue
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES
from lib_log_rich.runtime import PayloadLimits, RuntimeConfig, SeveritySnapshot

if TYPE_CHECKING:  # pragma: no cover - Textual imports only for typing
    from lib_log_rich.runtime._settings import ConsoleAppearance


@dataclass(slots=True)
class SettingSpec:
    """Metadata describing a single configurable field in the TUI."""

    key: str
    label: str
    default: str
    help: str
    category: str


@pydantic_dataclass(config=ConfigDict(frozen=True))
class RunConfig:
    """Validated configuration that will be applied to the runtime under test."""

    service: str
    environment: str
    logger_name: str
    log_level: LogLevel | None
    log_level_mode: Literal["FIXED", "CYCLE"]
    records_total: int
    message_length: int
    context_fields: int
    context_value_length: int
    extra_fields: int
    extra_value_length: int
    queue_enabled: bool
    queue_maxsize: int
    queue_full_policy: str
    queue_put_timeout: float | None
    queue_stop_timeout: float | None
    enable_ring_buffer: bool
    ring_buffer_size: int
    enable_journald: bool
    enable_eventlog: bool
    enable_graylog: bool
    graylog_endpoint: tuple[str, int] | None
    graylog_protocol: str
    graylog_tls: bool
    console_level: str
    backend_level: str
    graylog_level: str
    force_color: bool
    no_color: bool
    console_theme: str | None
    console_format_preset: str | None
    console_format_template: str | None
    console_styles: dict[str, str] | None
    dump_format: str
    dump_format_preset: str | None
    dump_format_template: str | None
    dump_level: LogLevel | None
    dump_context_filters: dict[str, FilterSpecValue] | None
    dump_context_extra_filters: dict[str, FilterSpecValue] | None
    dump_extra_filters: dict[str, FilterSpecValue] | None
    scrub_patterns: dict[str, str] | None
    rate_limit: tuple[int, float] | None
    payload_limits: PayloadLimits
    diag_history_limit: int


@dataclass(slots=True)
class StressMetrics:
    """Collects metrics for the stress run so we can present live feedback."""

    planned: int = 0
    emitted: int = 0
    failed: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    diagnostics: Counter[str] = field(default_factory=Counter)

    def reset(self, planned: int) -> None:
        """Start a new measurement window."""
        self.planned = planned
        self.emitted = 0
        self.failed = 0
        self.started_at = time.perf_counter()
        self.finished_at = None
        self.diagnostics = Counter()

    def finish(self) -> None:
        """Stop the measurement timer."""
        self.finished_at = time.perf_counter()

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at or time.perf_counter()
        return max(0.0, end - self.started_at)

    @property
    def throughput(self) -> float:
        duration = self.elapsed
        if duration == 0.0:
            return 0.0
        return self.emitted / duration

    def format_lines(self, snapshot: SeveritySnapshot | None = None) -> list[str]:
        """Return text lines summarising the run for the sidebar widget."""
        diag_summary = ", ".join(f"{name}:{count}" for name, count in self.diagnostics.most_common(6)) or "(none)"
        lines = [
            f"Planned: {self.planned}",
            f"Emitted: {self.emitted} (failed: {self.failed})",
            f"Elapsed: {self.elapsed:.2f}s",
            f"Throughput: {self.throughput:.1f} events/s",
            f"Diagnostics: {diag_summary}",
        ]

        if snapshot is not None:
            peak = snapshot.highest.name if snapshot.highest is not None else "NONE"
            level_summary = " ".join(
                f"{level.name}:{snapshot.counts.get(level, 0)}"
                for level in (LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL)
            )
            threshold_summary = ", ".join(f"{level.name}:{count}" for level, count in snapshot.thresholds.items()) or "(none)"
            drop_reason_summary = ", ".join(f"{reason}:{count}" for reason, count in snapshot.drops_by_reason.items()) or "(none)"
            drop_level_summary = " ".join(
                f"{level.name}:{snapshot.drops_by_level.get(level, 0)}"
                for level in (LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL)
            )
            reason_level_pairs = [f"{reason}/{level.name}:{count}" for (reason, level), count in snapshot.drops_by_reason_and_level.items() if count]

            lines.extend(
                [
                    f"Peak Level: {peak}",
                    f"Total Events: {snapshot.total_events}",
                    f"Level Counts: {level_summary}",
                    f"Thresholds: {threshold_summary}",
                    f"Drops Total: {snapshot.dropped_total}",
                    f"Drops by reason: {drop_reason_summary}",
                    f"Drops by level: {drop_level_summary}",
                ]
            )
            if reason_level_pairs:
                lines.append("Drops reason/level: " + ", ".join(reason_level_pairs))

        return lines


# Fields toggled by checkboxes; defaults converted to lowercase text for consistency.
_BOOLEAN_FIELDS = {
    "queue_enabled",
    "enable_ring_buffer",
    "force_color",
    "no_color",
    "enable_journald",
    "enable_eventlog",
    "enable_graylog",
    "graylog_tls",
    "payload_truncate_message",
}
# Fields whose widget defaults should be normalised to lowercase values.
_LOWERCASE_FIELDS = {
    "queue_full_policy",
    "graylog_protocol",
    "dump_format",
    "dump_format_preset",
    "console_theme",
    "console_format_preset",
}
# Fields expected to use uppercase tokens matching enum names.
_UPPERCASE_FIELDS = {
    "log_level",
    "console_level",
    "backend_level",
    "graylog_level",
    "dump_level",
}
# Canonical list of log levels exposed in select widgets.
_LOG_LEVEL_OPTIONS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
# Choice metadata reused across select widgets to avoid duplication.
_CHOICE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "queue_full_policy": [("Block", "block"), ("Drop", "drop")],
    "graylog_protocol": [("TCP", "tcp"), ("UDP", "udp")],
    "dump_format": [("Text", "text"), ("JSON", "json"), ("YAML", "yaml")],
    "dump_format_preset": [
        ("Full", "full"),
        ("Short", "short"),
        ("Full + Location", "full_loc"),
        ("Short + Location", "short_loc"),
        ("Short + Location + Icon", "short_loc_icon"),
    ],
    "console_format_preset": [
        ("Full", "full"),
        ("Short", "short"),
        ("Full + Location", "full_loc"),
        ("Short + Location", "short_loc"),
        ("Short + Location + Icon", "short_loc_icon"),
    ],
}
_CONSOLE_THEME_CHOICES = [("Runtime default", "")]
for theme_name in sorted(CONSOLE_STYLE_THEMES):
    label = theme_name.replace("_", " ").title()
    _CONSOLE_THEME_CHOICES.append((label, theme_name))
_CHOICE_FIELDS["console_theme"] = _CONSOLE_THEME_CHOICES
# `Select` option pairs constructed from the canonical level list.
_base_level_choices = [(name, name) for name in _LOG_LEVEL_OPTIONS]
_CHOICE_FIELDS["log_level"] = _base_level_choices + [("Cycle", "CYCLE")]
_CHOICE_FIELDS["dump_level"] = [("All levels", "")] + _base_level_choices
for level_field in ("console_level", "backend_level", "graylog_level"):
    _CHOICE_FIELDS[level_field] = list(_base_level_choices)
for bool_field in _BOOLEAN_FIELDS:
    _CHOICE_FIELDS[bool_field] = [("True", "true"), ("False", "false")]


APP_CSS = """
Screen {
    background: #002b36;
}
#backdrop {
    padding: 1 1;
    align: center middle;
}
#layout {
    width: 100%;
    height: 96vh;
    max-height: 96vh;
    border: round #6ea0ff;
    background: #083f7f;
    color: #f5f7ff;
    padding: 1 1;
}
#settings-panel {
    width: 35%;
    height: 100%;
    max-height: 100%;
    border: solid #6ea0ff;
    padding: 0 1;
    background: #05336b;
    overflow-y: auto;
}
VerticalScroll#settings-panel {
    height: 100%;
    overflow-y: auto;
}
#settings {
    padding-right: 1;
}
.category-label {
    text-style: bold;
    padding: 1 0 0 0;
    color: #fefefe;
}
.setting-row {
    height: auto;
    min-height: 1;
    align: center middle;
    padding: 0;
}
.setting-label {
    width: 26;
    min-width: 26;
    padding-right: 1;
    text-style: bold;
}
Input.setting-input {
    width: 32;
    border: none;
    background: #000000;
    color: #e6f0ff;
    padding: 0;
    height: 1;
}
Input.setting-input:focus {
    border: none;
    color: #a8d0ff;
    background: #083a72;
}
Select.setting-select {
    width: 32;
    margin: 0;
}
Select.setting-select > SelectCurrent {
    padding: 0 1;
}
#sidebar {
    width: 65%;
    height: 100%;
    padding-left: 1;
    align: left top;
}
#sidebar-controls {
    padding: 0;
    border-bottom: solid #6ea0ff;
}
#buttons {
    padding: 0;
    margin: 0;
    content-align: left middle;
}
#buttons Button {
    margin-right: 1;
    height: auto;
}
#buttons Button:last-child {
    margin-right: 0;
}
#status {
    padding: 0;
    margin: 0;
    color: #d9ecff;
}
#metrics {
    padding: 0;
    margin: 0;
    color: #9bc5ff;
}
VerticalScroll#sidebar-logs {
    height: 1fr;
    overflow-y: auto;
    padding-top: 0;
}
#title {
    text-align: center;
    text-style: bold;
}
#subtitle {
    color: #cfe2ff;
    padding-bottom: 1;
}
Button {
    min-width: 16;
}
.log-heading {
    padding: 0;
    text-style: bold;
    color: #d9ecff;
}
.toggle-row {
    align: left middle;
    padding: 0;
}
.toggle-label {
    color: #d9ecff;
}
Checkbox.log-toggle {
    color: #d9ecff;
    padding: 0;
}
Checkbox.log-toggle>.checkbox--checkbox {
    border: solid #6ea0ff;
}
#operations-log,
#diagnostics,
#console-output-queued,
#console-output-async,
#dump-output {
    height: auto;
    max-height: 12;
    background: #001b33;
    color: #f4f4f4;
    border: solid #6ea0ff;
    margin: 0 0 1 0;
    overflow-y: auto;
}
"""


def _env_default(name: str, fallback: str) -> str:
    """Return the environment override for a setting or its default.

    Stress-test operators mirror CLI behaviour by reading environment overrides.
    Centralising the lookup ensures defaults match the configuration guidance in
    ``docs/systemdesign/concept_architecture_plan.md``.

    Args:
        name: Environment variable to look up (for example ``LOG_CONSOLE_LEVEL``).
        fallback: Value used when the variable is unset.

    Returns:
        Resolved text preserved for populating Textual inputs.

    Note:
        The helper only reads from :mod:`os`.

    Example:
        >>> import os
        >>> os.environ['LIB_LOG_RICH_STRESS'] = 'custom'
        >>> _env_default('LIB_LOG_RICH_STRESS', 'fallback')
        'custom'
        >>> _ = os.environ.pop('LIB_LOG_RICH_STRESS', None)
        >>> _env_default('LIB_LOG_RICH_STRESS', 'fallback')
        'fallback'

    """
    value = os.getenv(name)
    return value if value is not None else fallback


def _env_bool_default(name: str, fallback: bool) -> str:
    """Return an environment-sourced boolean rendered as lowercase text.

    Textual inputs expect string values. Normalising booleans keeps environment
    toggles consistent with the CLI defaults documented in the system design
    notes, and avoids inconsistent casing in the UI.

    Args:
        name: Environment variable to read.
        fallback: Boolean default applied when the variable is unset.

    Returns:
        ``"true"`` or ``"false"`` when the variable is absent, otherwise the
        raw value so manual overrides survive intact.

    Note:
        The helper only reads process environment variables.

    Example:
        >>> import os
        >>> _ = os.environ.pop('LIB_LOG_RICH_BOOL', None)
        >>> _env_bool_default('LIB_LOG_RICH_BOOL', True)
        'true'
        >>> os.environ['LIB_LOG_RICH_BOOL'] = '0'
        >>> _env_bool_default('LIB_LOG_RICH_BOOL', True)
        '0'
        >>> _ = os.environ.pop('LIB_LOG_RICH_BOOL', None)

    """
    value = os.getenv(name)
    if value is None:
        return "true" if fallback else "false"
    return value


def _env_int_default(name: str, fallback: int) -> str:
    """Return integer defaults as text for Textual input widgets.

    The UI expects string values, but our defaults come from integers documented
    in ``docs/systemdesign/module_reference.md``. Converting once keeps widget
    setup declarative.

    Args:
        name: Environment variable holding a numeric override.
        fallback: Integer used when no override is present.

    Returns:
        Raw environment value when set, otherwise the fallback converted to a
        string.

    Example:
        >>> import os
        >>> _ = os.environ.pop('LIB_LOG_RICH_INT', None)
        >>> _env_int_default('LIB_LOG_RICH_INT', 42)
        '42'
        >>> os.environ['LIB_LOG_RICH_INT'] = '5'
        >>> _env_int_default('LIB_LOG_RICH_INT', 42)
        '5'
        >>> _ = os.environ.pop('LIB_LOG_RICH_INT', None)

    """
    value = os.getenv(name)
    return value if value is not None else str(fallback)


def _normalise_choice_default(spec: SettingSpec, *, options: list[tuple[str, str]]) -> str:
    """Ensure select widgets start with a valid option.

    Environment defaults may drift from the available options (for example when
    a preset is renamed). Normalising here protects the TUI from crashing while
    leaving a clear audit trail for out-of-date values.

    Args:
        spec: The setting metadata describing the field and default text.
        options: Pairs of display label and stored value supplied to the widget.

    Returns:
        A value present in ``options`` with the correct casing.

    Example:
        >>> spec = SettingSpec('queue_full_policy', 'Queue policy', 'Drop', '...', 'Queue')
        >>> _normalise_choice_default(spec, options=[('Block', 'block'), ('Drop', 'drop')])
        'drop'
        >>> spec = SettingSpec('console_level', 'Console level', 'warning', '...', 'Console')
        >>> _normalise_choice_default(spec, options=[('Info', 'INFO'), ('Warn', 'WARNING')])
        'WARNING'

    """
    raw_default = (spec.default or options[0][1]).strip()
    if spec.key in _BOOLEAN_FIELDS or spec.key in _LOWERCASE_FIELDS:
        raw_default = raw_default.lower()
    elif spec.key in _UPPERCASE_FIELDS:
        raw_default = raw_default.upper()
    valid_values = {value for _label, value in options}
    return raw_default if raw_default in valid_values else options[0][1]


def _generate_specs() -> list[SettingSpec]:
    """Return the ordered list of configuration controls for the TUI.

    Centralising the field catalogue keeps the Textual layout declarative and
    matches the documentation in ``docs/systemdesign/module_reference.md`` so
    QA engineers can trace every toggle back to its runtime effect.

    Returns:
        Metadata describing each setting including grouping and defaults.

    Example:
        >>> specs = _generate_specs()
        >>> 'service' in {spec.key for spec in specs}
        True
        >>> all(spec.category for spec in specs)
        True

    """
    return [
        # Run parameters
        SettingSpec("service", "Service", _env_default("LOG_SERVICE", "stress-service"), "Value bound to LogContext.service.", "Run"),
        SettingSpec("environment", "Environment", _env_default("LOG_ENVIRONMENT", "demo"), "Value bound to LogContext.environment.", "Run"),
        SettingSpec("logger_name", "Logger name", "stress.tui", "Name passed to lib_log_rich.getLogger().", "Run"),
        SettingSpec(
            "log_level", "Log level", _env_default("LOG_CONSOLE_LEVEL", "CYCLE"), "Level emitted for each record (supports fixed levels or cycle).", "Run"
        ),
        SettingSpec("records_total", "Log records", "200", "Number of log events to emit.", "Run"),
        SettingSpec("message_length", "Message length", "80", "Characters per log message.", "Run"),
        SettingSpec("context_fields", "Context fields", "2", "Context key count bound via bind().", "Run"),
        SettingSpec("context_value_length", "Context value length", "16", "Character length per context value.", "Run"),
        SettingSpec("extra_fields", "Extra fields", "2", "Per-event extra key count.", "Run"),
        SettingSpec("extra_value_length", "Extra value length", "18", "Character length per extra value.", "Run"),
        # Queue settings
        SettingSpec("queue_enabled", "Enable queue", _env_bool_default("LOG_QUEUE_ENABLED", True), "Use background queue worker when true.", "Queue"),
        SettingSpec("queue_maxsize", "Queue maxsize", _env_int_default("LOG_QUEUE_MAXSIZE", 2048), "Maximum queued events before policy applies.", "Queue"),
        SettingSpec(
            "queue_full_policy", "Queue policy", _env_default("LOG_QUEUE_FULL_POLICY", "block"), "block waits for space, drop rejects events.", "Queue"
        ),
        SettingSpec(
            "queue_put_timeout",
            "Queue put timeout",
            _env_default("LOG_QUEUE_PUT_TIMEOUT", "1.0"),
            "Seconds producer waits when blocking (blank = None).",
            "Queue",
        ),
        SettingSpec(
            "queue_stop_timeout",
            "Queue stop timeout",
            _env_default("LOG_QUEUE_STOP_TIMEOUT", "5.0"),
            "Shutdown drain deadline in seconds (<=0 => wait forever).",
            "Queue",
        ),
        # Ring buffer / dump
        SettingSpec(
            "enable_ring_buffer",
            "Enable ring buffer",
            _env_bool_default("LOG_RING_BUFFER_ENABLED", True),
            "Toggle in-memory ring buffer retention.",
            "Ring buffer",
        ),
        SettingSpec(
            "ring_buffer_size", "Ring buffer size", _env_int_default("LOG_RING_BUFFER_SIZE", 25_000), "Number of events kept in the ring buffer.", "Ring buffer"
        ),
        SettingSpec("dump_format", "Dump format", _env_default("LOG_DUMP_FORMAT", "text"), "Dump format name (text/json/yaml).", "Ring buffer"),
        SettingSpec("dump_format_preset", "Dump preset", _env_default("LOG_DUMP_FORMAT_PRESET", "full"), "Default dump preset used by dump().", "Ring buffer"),
        SettingSpec(
            "dump_format_template",
            "Dump template",
            _env_default("LOG_DUMP_FORMAT_TEMPLATE", ""),
            "Override preset with custom template (blank for default).",
            "Ring buffer",
        ),
        SettingSpec(
            "dump_level", "Dump min level", _env_default("LOG_DUMP_LEVEL", ""), "Minimum level included when capturing dumps (blank = all).", "Ring buffer"
        ),
        SettingSpec("dump_context_filters", "Context filters", "", "Comma list: key=value or key~contains:value (LogContext fields).", "Ring buffer"),
        SettingSpec("dump_context_extra_filters", "Context extra filters", "", "Comma list: key=value or key~regex:^foo$ (context.extra).", "Ring buffer"),
        SettingSpec("dump_extra_filters", "Extra filters", "", "Comma list: key=value or key~icontains:bar (event extras).", "Ring buffer"),
        # Console formatting
        SettingSpec("console_level", "Console level", _env_default("LOG_CONSOLE_LEVEL", "INFO"), "Minimum level printed to console adapter.", "Console"),
        SettingSpec("backend_level", "Backend level", _env_default("LOG_BACKEND_LEVEL", "WARNING"), "Minimum level for structured backends.", "Console"),
        SettingSpec(
            "console_theme", "Console theme", _env_default("LOG_CONSOLE_THEME", "classic"), "Palette from domain.palettes.CONSOLE_STYLE_THEMES.", "Console"
        ),
        SettingSpec(
            "console_format_preset",
            "Console preset",
            _env_default("LOG_CONSOLE_FORMAT_PRESET", "full"),
            "Named console format preset (full/short/full_loc/short_loc).",
            "Console",
        ),
        SettingSpec(
            "console_format_template",
            "Console template",
            _env_default("LOG_CONSOLE_FORMAT_TEMPLATE", ""),
            "Custom console template (blank to use preset).",
            "Console",
        ),
        SettingSpec(
            "console_styles", "Console styles", _env_default("LOG_CONSOLE_STYLES", ""), "Comma list LEVEL=style overrides (e.g. INFO=cyan).", "Console"
        ),
        SettingSpec("force_color", "Force color", _env_bool_default("LOG_FORCE_COLOR", False), "Force ANSI colour even when terminal says no.", "Console"),
        SettingSpec("no_color", "Disable color", _env_bool_default("LOG_NO_COLOR", False), "Disable colour output entirely.", "Console"),
        # Structured adapters
        SettingSpec("enable_journald", "Enable journald", _env_bool_default("LOG_ENABLE_JOURNALD", False), "Emit to systemd-journald when true.", "Structured"),
        SettingSpec(
            "enable_eventlog", "Enable Event Log", _env_bool_default("LOG_ENABLE_EVENTLOG", False), "Emit to Windows Event Log when true.", "Structured"
        ),
        SettingSpec(
            "enable_graylog", "Enable Graylog", _env_bool_default("LOG_ENABLE_GRAYLOG", False), "Send GELF payloads to Graylog endpoint.", "Structured"
        ),
        SettingSpec(
            "graylog_endpoint", "Graylog endpoint", _env_default("LOG_GRAYLOG_ENDPOINT", ""), "host:port for Graylog (blank to auto/inherit).", "Structured"
        ),
        SettingSpec("graylog_protocol", "Graylog protocol", _env_default("LOG_GRAYLOG_PROTOCOL", "tcp"), "tcp or udp.", "Structured"),
        SettingSpec("graylog_tls", "Graylog TLS", _env_bool_default("LOG_GRAYLOG_TLS", False), "Wrap TCP connection with TLS.", "Structured"),
        SettingSpec("graylog_level", "Graylog level", _env_default("LOG_GRAYLOG_LEVEL", "WARNING"), "Minimum level for Graylog adapter.", "Structured"),
        # Scrubbing & rate limits
        SettingSpec(
            "scrub_patterns",
            "Scrub patterns",
            _env_default("LOG_SCRUB_PATTERNS", "password=.+,token=.+,secret=.+"),
            "Comma list key=regex redactions (blank uses defaults).",
            "Scrub & limits",
        ),
        SettingSpec("rate_limit", "Rate limit", _env_default("LOG_RATE_LIMIT", ""), "Format MAX:WINDOW_SECONDS (blank disables).", "Scrub & limits"),
        # Payload limits
        SettingSpec("payload_truncate_message", "Truncate message", "true", "Clamp messages longer than limit when true.", "Payload"),
        SettingSpec("payload_message_max_chars", "Message max chars", "4096", "Maximum characters kept in message.", "Payload"),
        SettingSpec("payload_extra_max_keys", "Extra max keys", "25", "Maximum keys stored in per-event extras.", "Payload"),
        SettingSpec("payload_extra_max_value_chars", "Extra value max chars", "512", "Maximum characters kept per extra value.", "Payload"),
        SettingSpec("payload_extra_max_depth", "Extra max depth", "3", "Maximum nesting depth preserved in extras.", "Payload"),
        SettingSpec("payload_extra_max_total_bytes", "Extra max bytes", "8192", "Total bytes budget for extras (blank disables).", "Payload"),
        SettingSpec("payload_context_max_keys", "Context extra max keys", "20", "Maximum keys stored in context extras.", "Payload"),
        SettingSpec("payload_context_max_value_chars", "Context value max chars", "256", "Maximum characters kept per context extra value.", "Payload"),
        SettingSpec("payload_stacktrace_max_frames", "Stacktrace max frames", "10", "Frames kept when truncating stack traces.", "Payload"),
        # Diagnostics
        SettingSpec("diag_history_limit", "Diagnostics history", "200", "Number of diagnostic entries kept in the log view.", "Diagnostics"),
    ]


@lru_cache(maxsize=1)
def _get_settings() -> tuple[SettingSpec, ...]:
    """Expose the immutable setting catalogue for reuse across the TUI.

    Multiple views need the same ordered list. Caching avoids rebuilding the
    list on every recomposition while keeping a single source of truth.

    Returns:
        Tuple-backed view so consumers cannot accidentally mutate metadata.

    Example:
        >>> first_call = _get_settings()
        >>> second_call = _get_settings()
        >>> first_call is second_call
        True

    """
    return tuple(_generate_specs())


@lru_cache(maxsize=1)
def _get_setting_groups() -> dict[str, list[SettingSpec]]:
    """Return settings organised by sidebar category for rendering.

    The TUI groups controls by domain (Run, Queue, Console, …). Building the
    mapping once keeps rendering logic simple and avoids runtime mutation.

    Returns:
        Mapping of category name to the ordered specs in that section.

    Example:
        >>> grouped = _get_setting_groups()
        >>> 'Run' in grouped
        True
        >>> isinstance(grouped['Run'][0], SettingSpec)
        True

    """
    grouped: dict[str, list[SettingSpec]] = defaultdict(list)
    for spec in _get_settings():
        grouped[spec.category].append(spec)
    return grouped


def _parse_bool(text: str, *, default: bool) -> bool:
    """Normalise Textual input strings into booleans.

    Textual widgets return free-form text. The runtime expects canonical booleans,
    so we centralise accepted synonyms to match CLI semantics.

    Args:
        text: Raw value captured from the widget.
        default: Value returned when ``text`` is blank.

    Returns:
        Normalised boolean.

    Raises:
        ValueError: If ``text`` is non-empty but not a recognised boolean token.

    Example:
        >>> _parse_bool('YES', default=False)
        True
        >>> _parse_bool('', default=True)
        True
        >>> _parse_bool('off', default=True)
        False

    """
    if not text:
        return default
    candidate = text.strip().lower()
    if candidate in {"1", "true", "yes", "y", "on"}:
        return True
    if candidate in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Expected boolean value, received '{text}'.")


def _parse_int(text: str, name: str, *, minimum: int | None = None) -> int:
    """Convert widget text to integers with defensive validation.

    Many runtime limits are integers. We surface explicit errors so testers know
    which field must be corrected instead of receiving a generic ValueError.

    Args:
        text: Raw input captured from the UI.
        name: Human-readable label used in error messages.
        minimum: Optional lower bound inclusive.

    Returns:
        Parsed integer.

    Raises:
        ValueError: If parsing fails or the value breaches ``minimum``.

    Example:
        >>> _parse_int('5', 'Queue maxsize', minimum=1)
        5
        >>> _parse_int('0', 'Queue maxsize', minimum=1)
        Traceback (most recent call last):
        ...
        ValueError: Queue maxsize must be >= 1 (got 0).

    """
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer (got '{text}').") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value}).")
    return value


def _parse_float(
    text: str,
    name: str,
    *,
    allow_blank: bool = False,
    allow_non_positive: bool = False,
) -> float | None:
    """Parse decimal inputs used for timeouts and windows.

    Several runtime settings accept floating point durations. We keep all
    validation rules in one place so updates propagate consistently to the TUI
    and documentation.

    Args:
        text: Raw user-entered value.
        name: Human-readable label for error context.
        allow_blank: When ``True`` blank strings map to ``None``.
        allow_non_positive: When ``True`` non-positive values are permitted (for sentinel semantics).

    Returns:
        Parsed float respecting the configured allowances.

    Raises:
        ValueError: If the text is blank without ``allow_blank`` or the value violates the
            positivity constraint.

    Example:
        >>> _parse_float('1.5', 'Timeout', allow_blank=False)
        1.5
        >>> _parse_float('', 'Timeout', allow_blank=True) is None
        True
        >>> _parse_float('-1', 'Timeout', allow_non_positive=False)
        Traceback (most recent call last):
        ...
        ValueError: Timeout must be > 0 (got -1.0).

    """
    if not text:
        if allow_blank:
            return None
        raise ValueError(f"{name} cannot be blank.")
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number (got '{text}').") from exc
    if not allow_non_positive and value <= 0:
        raise ValueError(f"{name} must be > 0 (got {value}).")
    return value


def _parse_endpoint(text: str) -> tuple[str, int] | None:
    """Parse Graylog endpoint definitions from the settings form.

    The stress tool lets operators experiment with GELF delivery. This helper
    keeps validation messages targeted so misconfiguration is obvious.

    Args:
        text: Input string in ``host:port`` form.

    Returns:
        Tuple of host and port when supplied, otherwise ``None``.

    Raises:
        ValueError: If the input lacks a host or port.

    Example:
        >>> _parse_endpoint('graylog.example.com:12201')
        ('graylog.example.com', 12201)
        >>> _parse_endpoint('') is None
        True

    """
    if not text:
        return None
    if ":" not in text:
        raise ValueError("Graylog endpoint must be host:port.")
    host, port_text = text.split(":", 1)
    if not host:
        raise ValueError("Graylog endpoint host cannot be empty.")
    port = _parse_int(port_text, "Graylog endpoint port", minimum=1)
    return host, port


def _parse_styles(text: str) -> dict[str, str] | None:
    """Interpret comma-separated level styling overrides.

    Console demos allow experimenting with Rich styling using ``LEVEL=style``
    pairs. Normalising case keeps downstream adapters consistent with the palette
    definitions in ``lib_log_rich.domain.palettes``.

    Args:
        text: Comma-delimited string such as ``"INFO=cyan,ERROR=red"``.

    Returns:
        Mapping from upper-case level tokens to Rich style strings, or ``None``
        when no overrides are provided.

    Raises:
        ValueError: If an entry lacks an equals sign or contains blank segments.

    Example:
        >>> _parse_styles('INFO=cyan,ERROR=bold red')
        {'INFO': 'cyan', 'ERROR': 'bold red'}
        >>> _parse_styles('') is None
        True

    """
    if not text:
        return None
    styles: dict[str, str] = {}
    for pair in text.split(","):
        if "=" not in pair:
            raise ValueError("Console styles must use LEVEL=style format.")
        level, style = pair.split("=", 1)
        if not level or not style:
            raise ValueError("Console style mappings require non-empty keys and values.")
        styles[level.strip().upper()] = style.strip()
    return styles


def _parse_patterns(text: str) -> dict[str, str] | None:
    """Parse scrub pattern overrides from the configuration form.

    The stress runner allows testing of regex scrubbers before shipping them.
    Validating the ``key=regex`` structure here prevents runtime surprises when
    the job executes.

    Args:
        text: Comma-separated ``key=pattern`` entries.

    Returns:
        Mapping of field name to regex text, or ``None`` when blank.

    Raises:
        ValueError: If entries are malformed or missing values.

    Example:
        >>> result = _parse_patterns('token=.*')
        >>> result['token']
        '.*'
        >>> _parse_patterns('') is None
        True

    """
    if not text:
        return None
    patterns: dict[str, str] = {}
    for pair in text.split(","):
        if "=" not in pair:
            raise ValueError("Scrub patterns must use key=regex format.")
        key, pattern = pair.split("=", 1)
        key = key.strip()
        if not key or not pattern:
            raise ValueError("Scrub pattern entries require non-empty key and regex.")
        patterns[key] = pattern
    return patterns


def _append_dump_filter(target: dict[str, FilterSpecValue], key: str, spec: FilterSpecValue) -> None:
    """Accumulate dump filter specs supporting OR semantics."""
    existing = target.get(key)
    if existing is None:
        target[key] = spec
        return
    if isinstance(existing, list):
        existing.append(spec)
        return
    target[key] = [existing, spec]


def _parse_dump_filters(text: str, label: str) -> dict[str, FilterSpecValue] | None:
    """Parse comma-delimited filter entries into dump filter specs."""
    if not text:
        return None
    filters: dict[str, FilterSpecValue] = {}
    for raw in text.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if "~" in entry:
            key_part, spec_part = entry.split("~", 1)
            key = key_part.strip()
            if not key:
                raise ValueError(f"{label} entries require a key before '~'.")
            if ":" not in spec_part:
                raise ValueError(f"{label} entries using '~' must follow key~operator:value syntax.")
            operator, value = spec_part.split(":", 1)
            operator = operator.strip().lower()
            value = value.strip()
            if not value:
                raise ValueError(f"{label} entries require a value after ':'")
            if operator == "contains":
                spec: FilterSpecValue = {"contains": value}
            elif operator == "icontains":
                spec = {"icontains": value}
            elif operator == "regex":
                try:
                    pattern = re.compile(value)
                except re.error as exc:
                    raise ValueError(f"{label} regex {value!r} is invalid: {exc}") from exc
                spec = {"pattern": pattern, "regex": True}
            else:
                raise ValueError(f"{label} operator must be contains, icontains, or regex.")
        else:
            if "=" not in entry:
                raise ValueError(f"{label} entries must use key=value or key~operator:value syntax.")
            key, value = entry.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or not value:
                raise ValueError(f"{label} entries require non-empty key and value.")
            spec = value
        _append_dump_filter(filters, key, spec)
    return filters or None


def _parse_rate_limit(text: str) -> tuple[int, float] | None:
    """Parse ``max:window`` rate-limit expressions for the stress runs.

    Stress scenarios often tweak burst limits. Parsing here mirrors the syntax
    accepted by ``RuntimeConfig`` so the resulting tuple can be handed directly
    to the adapter factory.

    Args:
        text: Comma input such as ``"100:1.5"``.

    Returns:
        ``(max_events, window_seconds)`` when provided, otherwise ``None``.

    Raises:
        ValueError: If the string is malformed or uses invalid numbers.

    Example:
        >>> _parse_rate_limit('10:0.5')
        (10, 0.5)
        >>> _parse_rate_limit('') is None
        True

    """
    if not text:
        return None
    if ":" not in text:
        raise ValueError("Rate limit format is MAX:WINDOW_SECONDS.")
    max_text, window_text = text.split(":", 1)
    max_events = _parse_int(max_text, "Rate limit max events", minimum=1)
    window = _parse_float(window_text, "Rate limit window", allow_non_positive=False)
    if window is None:
        raise ValueError("Rate limit window must be provided.")
    return max_events, window


def _parse_payload_limits(values: dict[str, str]) -> PayloadLimits:
    """Build :class:`PayloadLimits` from the form values.

    Payload limits constrain diagnostic noise and protect downstream queues. The
    stress harness mirrors runtime behaviour so testers can observe truncation
    thresholds before shipping new defaults.

    Args:
        values: Mapping from setting key to the textual value captured from widgets.

    Returns:
        Dataclass instance populated with validated limits.

    Note:
        Imports :mod:`lib_log_rich.runtime` lazily to avoid pulling heavy
        dependencies during module import.

    Example:
        >>> sample = {
        ...     'payload_truncate_message': 'true',
        ...     'payload_message_max_chars': '120',
        ...     'payload_extra_max_keys': '5',
        ...     'payload_extra_max_value_chars': '32',
        ...     'payload_extra_max_depth': '4',
        ...     'payload_extra_max_total_bytes': '',
        ...     'payload_context_max_keys': '5',
        ...     'payload_context_max_value_chars': '32',
        ...     'payload_stacktrace_max_frames': '10',
        ... }
        >>> limits = _parse_payload_limits(sample)
        >>> limits.message_max_chars
        120

    """
    from lib_log_rich.runtime import PayloadLimits

    return PayloadLimits(
        truncate_message=_parse_bool(values["payload_truncate_message"], default=True),
        message_max_chars=_parse_int(values["payload_message_max_chars"], "Message max chars", minimum=1),
        extra_max_keys=_parse_int(values["payload_extra_max_keys"], "Extra max keys", minimum=1),
        extra_max_value_chars=_parse_int(values["payload_extra_max_value_chars"], "Extra value max chars", minimum=1),
        extra_max_depth=_parse_int(values["payload_extra_max_depth"], "Extra max depth", minimum=1),
        extra_max_total_bytes=_parse_int(values["payload_extra_max_total_bytes"], "Extra max bytes", minimum=1)
        if values["payload_extra_max_total_bytes"]
        else None,
        context_max_keys=_parse_int(values["payload_context_max_keys"], "Context extra max keys", minimum=1),
        context_max_value_chars=_parse_int(values["payload_context_max_value_chars"], "Context value max chars", minimum=1),
        stacktrace_max_frames=_parse_int(values["payload_stacktrace_max_frames"], "Stacktrace max frames", minimum=1),
    )


def _collect_values(rows: dict[str, Any]) -> dict[str, str]:
    """Extract string values from Textual setting rows.

    The TUI stores widget instances keyed by setting. Collecting once simplifies
    downstream parsing and keeps error reporting anchored to canonical strings.

    Args:
        rows: Mapping of setting keys to row components exposing ``value()``.

    Returns:
        Snapshot of user-entered text for further normalisation.

    Note:
        Calls the ``value()`` accessor on each row; widgets may perform trimming as
        part of their API.

    Example:
        >>> class Dummy:  # minimal duck type for doctest
        ...     def __init__(self, value):
        ...         self._value = value
        ...     def value(self):
        ...         return self._value
        >>> _collect_values({'service': Dummy('demo')})
        {'service': 'demo'}

    """
    return {key: row.value() for key, row in rows.items()}


def _parse_basic_config(values: dict[str, str]) -> dict[str, Any]:
    """Parse basic service configuration."""
    from lib_log_rich.domain import LogLevel

    log_level_raw = values["log_level"].strip().upper() or "INFO"
    if log_level_raw == "CYCLE":
        log_level = None
        log_level_mode = "CYCLE"
    else:
        try:
            log_level = LogLevel.from_name(log_level_raw)
        except ValueError as exc:
            raise ValueError("Invalid log level provided.") from exc
        log_level_mode = "FIXED"

    return {
        "service": values["service"] or "stress-service",
        "environment": values["environment"] or "demo",
        "logger_name": values["logger_name"] or "stress.tui",
        "log_level": log_level,
        "log_level_mode": log_level_mode,
    }


def _parse_workload_config(values: dict[str, str]) -> dict[str, Any]:
    """Parse workload generation configuration."""
    return {
        "records_total": _parse_int(values["records_total"], "Log records", minimum=1),
        "message_length": _parse_int(values["message_length"], "Message length", minimum=1),
        "context_fields": _parse_int(values["context_fields"], "Context fields", minimum=0),
        "context_value_length": _parse_int(values["context_value_length"], "Context value length", minimum=1),
        "extra_fields": _parse_int(values["extra_fields"], "Extra fields", minimum=0),
        "extra_value_length": _parse_int(values["extra_value_length"], "Extra value length", minimum=1),
    }


def _parse_queue_config(values: dict[str, str]) -> dict[str, Any]:
    """Parse queue-related configuration."""
    queue_full_policy = values["queue_full_policy"].strip().lower() or "block"
    if queue_full_policy not in {"block", "drop"}:
        raise ValueError("Queue policy must be 'block' or 'drop'.")

    raw_stop_timeout = _parse_float(values["queue_stop_timeout"], "Queue stop timeout", allow_blank=True, allow_non_positive=True)
    queue_stop_timeout = raw_stop_timeout if (raw_stop_timeout and raw_stop_timeout > 0) else None

    return {
        "queue_enabled": _parse_bool(values["queue_enabled"], default=True),
        "queue_maxsize": _parse_int(values["queue_maxsize"], "Queue maxsize", minimum=1),
        "queue_full_policy": queue_full_policy,
        "queue_put_timeout": _parse_float(values["queue_put_timeout"], "Queue put timeout", allow_blank=True),
        "queue_stop_timeout": queue_stop_timeout,
    }


def _parse_dump_config(values: dict[str, str]) -> dict[str, Any]:
    """Parse dump/ring buffer configuration."""
    from lib_log_rich.domain import LogLevel

    dump_format_raw = values["dump_format"].strip().lower() or "text"
    if dump_format_raw not in {"text", "json", "yaml"}:
        raise ValueError("Dump format must be text/json/yaml.")

    dump_level_raw = values["dump_level"].strip().upper()
    if dump_level_raw:
        try:
            dump_level = LogLevel.from_name(dump_level_raw)
        except ValueError as exc:
            raise ValueError("Invalid dump level specified.") from exc
    else:
        dump_level = None

    return {
        "enable_ring_buffer": _parse_bool(values["enable_ring_buffer"], default=True),
        "ring_buffer_size": _parse_int(values["ring_buffer_size"], "Ring buffer size", minimum=1),
        "dump_format": dump_format_raw,
        "dump_format_preset": values["dump_format_preset"].strip() or None,
        "dump_format_template": values["dump_format_template"].strip() or None,
        "dump_level": dump_level,
        "dump_context_filters": _parse_dump_filters(values["dump_context_filters"], "Context filters"),
        "dump_context_extra_filters": _parse_dump_filters(values["dump_context_extra_filters"], "Context extra filters"),
        "dump_extra_filters": _parse_dump_filters(values["dump_extra_filters"], "Extra filters"),
    }


def _parse_logging_levels(values: dict[str, str]) -> dict[str, str]:
    """Parse and validate logging level configuration."""
    from lib_log_rich.domain import LogLevel

    console_level = (values["console_level"] or "INFO").upper()
    backend_level = (values["backend_level"] or "WARNING").upper()
    graylog_level = (values["graylog_level"] or "WARNING").upper()

    try:
        LogLevel.from_name(console_level)
        LogLevel.from_name(backend_level)
        LogLevel.from_name(graylog_level)
    except ValueError as exc:
        raise ValueError("Invalid console/backend/Graylog level specified.") from exc

    return {
        "console_level": console_level,
        "backend_level": backend_level,
        "graylog_level": graylog_level,
    }


def _parse_console_config(values: dict[str, str]) -> dict[str, Any]:
    """Parse console appearance configuration."""
    return {
        "console_theme": values["console_theme"].strip() or None,
        "console_format_preset": values["console_format_preset"].strip() or None,
        "console_format_template": values["console_format_template"].strip() or None,
        "console_styles": _parse_styles(values["console_styles"]),
        "force_color": _parse_bool(values["force_color"], default=False),
        "no_color": _parse_bool(values["no_color"], default=False),
    }


def _parse_backend_config(values: dict[str, str]) -> dict[str, Any]:
    """Parse backend adapter configuration."""
    graylog_protocol = values["graylog_protocol"].strip().lower() or "tcp"
    if graylog_protocol not in {"tcp", "udp"}:
        raise ValueError("Graylog protocol must be 'tcp' or 'udp'.")

    return {
        "enable_journald": _parse_bool(values["enable_journald"], default=False),
        "enable_eventlog": _parse_bool(values["enable_eventlog"], default=False),
        "enable_graylog": _parse_bool(values["enable_graylog"], default=False),
        "graylog_endpoint": _parse_endpoint(values["graylog_endpoint"]),
        "graylog_protocol": graylog_protocol,
        "graylog_tls": _parse_bool(values["graylog_tls"], default=False),
    }


def _parse_security_config(values: dict[str, str]) -> dict[str, Any]:
    """Parse security and limits configuration."""
    return {
        "scrub_patterns": _parse_patterns(values["scrub_patterns"]),
        "rate_limit": _parse_rate_limit(values["rate_limit"]),
        "payload_limits": _parse_payload_limits(values),
    }


def _parse_config(rows: dict[str, Any]) -> RunConfig:
    """Convert the Textual form state into :class:`RunConfig`.

    The stress harness orchestrates a high number of settings; validating and
    normalising them centrally prevents runtime surprises and ensures alignment
    with ``docs/systemdesign/module_reference.md``.

    Args:
        rows: Mapping of setting key to UI row component exposing ``value()``.

    Returns:
        Fully validated configuration object consumed by the stress workers.

    Raises:
        ValueError: When any field fails validation (invalid enum, malformed filter, etc.).

    Example:
        >>> class _Row:
        ...     def __init__(self, value: str):
        ...         self._value = value
        ...     def value(self) -> str:
        ...         return self._value
        >>> defaults = {spec.key: _Row(spec.default or '') for spec in _get_settings()}
        >>> config = _parse_config(defaults)
        >>> config.service, config.environment
        ('stress-service', 'demo')

    """
    values = _collect_values(rows)

    # Delegate to focused parsers - each handles a logical group
    basic_config = _parse_basic_config(values)
    workload_config = _parse_workload_config(values)
    queue_config = _parse_queue_config(values)
    dump_config = _parse_dump_config(values)
    levels_config = _parse_logging_levels(values)
    console_config = _parse_console_config(values)
    backend_config = _parse_backend_config(values)
    security_config = _parse_security_config(values)

    # Simple object construction by merging all config dicts
    return RunConfig(
        **basic_config,
        **workload_config,
        **queue_config,
        **dump_config,
        **levels_config,
        **console_config,
        **backend_config,
        **security_config,
        diag_history_limit=_parse_int(values["diag_history_limit"], "Diagnostics history", minimum=10),
    )


def _make_text(index: int, length: int, prefix: str) -> str:
    """Generate deterministic filler text for log payloads.

    Stress scenarios need reproducible payloads to compare runs. This helper
    creates predictable strings with bounded length.

    Args:
        index: Sequence number appended to the prefix.
        length: Total length budget for the resulting string.
        prefix: Human-readable prefix describing the field (for debugging dumps).

    Returns:
        Bounded string filled with underscores when padding is required.

    Example:
        >>> _make_text(3, 12, 'ctx')
        'ctx-3_______'
        >>> len(_make_text(99, 4, 'msg'))
        4

    """
    base = f"{prefix}-{index}"
    if len(base) >= length:
        return base[:length]
    padding = "_" * max(0, length - len(base))
    return (base + padding)[:length]


@dataclass(frozen=True)
class _TextualImports:
    """Container bundling Textual types so imports stay lazy.

    Textual is an optional dev dependency. Collecting types here keeps the rest
    of the module importable even when Textual is unavailable.
    """

    App: type[Any]
    ComposeResult: type[Any]
    Container: type[Any]
    Horizontal: type[Any]
    Vertical: type[Any]
    VerticalScroll: type[Any]
    Button: type[Any]
    Checkbox: type[Any]
    Input: type[Any]
    RichLog: type[Any]
    Select: type[Any]
    Static: type[Any]


def _import_textual() -> _TextualImports:
    """Load Textual components on demand with clear error messaging.

    Textual is optional for headless environments. Importing lazily retains
    module importability while providing actionable messaging when developers
    run the stress tool without dev extras installed.

    Returns:
        Bundle of Textual classes used to declare the UI.

    Raises:
        SystemExit: When Textual is unavailable; the stack trace helps with debugging.

    Example:
        >>> isinstance(_import_textual().Button.__name__, str)  # doctest: +ELLIPSIS
        True

    """
    try:
        from textual.app import App as _App
        from textual.app import ComposeResult as _ComposeResult
        from textual.containers import Container as _Container
        from textual.containers import Horizontal as _Horizontal
        from textual.containers import Vertical as _Vertical
        from textual.containers import VerticalScroll as _VerticalScroll
        from textual.widgets import Button as _Button
        from textual.widgets import Checkbox as _Checkbox
        from textual.widgets import Input as _Input
        from textual.widgets import RichLog as _RichLog
        from textual.widgets import Select as _Select
        from textual.widgets import Static as _Static
    except Exception as exc:  # pragma: no cover - textual missing
        import traceback

        print(
            "[stresstest] Textual is required. Install dev extras: pip install -e .[dev]",
            file=sys.stderr,
        )
        traceback.print_exc()
        raise SystemExit(1) from exc
    return _TextualImports(
        App=_App,
        ComposeResult=_ComposeResult,
        Container=_Container,
        Horizontal=_Horizontal,
        Vertical=_Vertical,
        VerticalScroll=_VerticalScroll,
        Button=_Button,
        Checkbox=_Checkbox,
        Input=_Input,
        RichLog=_RichLog,
        Select=_Select,
        Static=_Static,
    )


def _enable_project_configuration() -> None:
    """Load project-level configuration (dotenv support) before running Textual.

    The stress harness honours the same ``.env`` overrides as the CLI. Enabling
    it ensures the TUI exercises the runtime with consistent credentials and
    endpoints.

    Note:
        Imports :mod:`lib_log_rich.config` and reads environment variables as part of
        ``enable_dotenv`` execution.

    """
    from . import config as config_module

    config_module.enable_dotenv()


def _import_runtime_modules() -> tuple[Any, Any]:
    """Import the public package and runtime module lazily for stress runs.

    Delaying imports prevents expensive Rich/Textual setup during module import
    while still giving the application direct access to `lib_log_rich` helpers.

    Returns:
        Tuple of the top-level package and the runtime module.

    """
    import lib_log_rich as log
    from lib_log_rich import runtime

    return log, runtime


def _create_app_class(imports: _TextualImports, log: Any, runtime: Any) -> type[Any]:
    """Construct the Textual application class used by the stress test.

    The class definition depends on dynamic imports and runtime collaborators.
    Building it lazily keeps module import inexpensive while aligning layout and
    behaviour with the architecture plan.

    Args:
        imports: Bundle of Textual types resolved at runtime.
        log: Public package providing helpers such as ``hello_world`` for quick demos.
        runtime: Runtime module exposing composition helpers.

    Returns:
        Subclass of :class:`textual.app.App` ready to run.

    """
    Horizontal = imports.Horizontal
    Select = imports.Select
    Input = imports.Input
    Static = imports.Static
    Checkbox = imports.Checkbox
    Button = imports.Button
    RichLog = imports.RichLog

    class SettingRow(Horizontal):
        """Renders a single configuration row and exposes its value."""

        def __init__(self, spec: SettingSpec) -> None:
            super().__init__(classes="setting-row")
            self.spec = spec
            choices = _CHOICE_FIELDS.get(spec.key)
            if choices is not None:
                default_value = _normalise_choice_default(spec, options=choices)
                self._input = Select(options=choices, value=default_value, classes="setting-select", compact=True)
                self._input.styles.width = 32
            else:
                self._input = Input(value=spec.default or "", classes="setting-input")
            self._input.tooltip = spec.help

        def compose(self) -> Iterable[Any]:  # type: ignore[override]
            label = Static(self.spec.label, classes="setting-label")
            label.tooltip = self.spec.help
            yield label
            yield self._input

        def value(self) -> str:
            raw_value = getattr(self._input, "value", "")
            if callable(raw_value):  # defensive: some widgets expose value() callable
                raw_value = raw_value()
            return (raw_value or "").strip()

    class StressTestApp(imports.App[None]):
        CSS = APP_CSS
        BINDINGS = [("q", "quit", "Quit"), ("ctrl+c", "quit", "Quit")]

        def __init__(self) -> None:
            super().__init__()
            self._rows: dict[str, SettingRow] = {}
            self._metrics = StressMetrics()
            self._run_task: asyncio.Task[None] | None = None
            self._active_run_token: object | None = None
            self._diagnostic_history: list[str] = []
            self._status: Static | None = None
            self._metrics_view: Static | None = None
            self._operations_log: RichLog | None = None
            self._diagnostics_log: RichLog | None = None
            self._queued_console_log: RichLog | None = None
            self._async_console_log: RichLog | None = None
            self._dump_log: RichLog | None = None
            self._console_queue: Queue[str] = Queue()
            self._console_async_queue: asyncio.Queue[str] = asyncio.Queue()
            self._console_export_style: Literal["ansi", "html"] = "ansi"
            self._queued_output_enabled = True
            self._async_output_enabled = True
            self._queued_toggle: Checkbox | None = None
            self._async_toggle: Checkbox | None = None
            self._log = log
            self._runtime = runtime

        def compose(self) -> Iterable[Any]:  # type: ignore[override]
            yield self._build_root_container()

        def _build_root_container(self) -> Any:
            return imports.Container(self._build_layout(), id="backdrop")

        def _build_layout(self) -> Any:
            settings_panel = self._build_settings_panel()
            sidebar = self._build_sidebar()
            return imports.Horizontal(settings_panel, sidebar, id="layout")

        def _build_settings_panel(self) -> Any:
            widgets: list[Any] = [
                imports.Static("lib_log_rich Stress Test", id="title"),
                imports.Static(
                    "Configure runtime settings, then press Start to emit synthetic logs across all adapters.",
                    id="subtitle",
                ),
            ]
            for category, specs in _get_setting_groups().items():
                widgets.append(imports.Static(category, classes="category-label"))
                for spec in specs:
                    row = SettingRow(spec)
                    self._rows[spec.key] = row
                    widgets.append(row)
            panel = imports.VerticalScroll(*widgets, id="settings-panel")
            panel.can_focus = True
            panel.can_focus_children = True
            return panel

        def _build_sidebar(self) -> Any:
            controls = self._build_sidebar_controls()
            log_views = self._build_log_views()
            return imports.Vertical(controls, log_views, id="sidebar")

        def _build_sidebar_controls(self) -> Any:
            buttons = imports.Horizontal(
                Button("Start", id="start"),
                Button("Stop", id="stop", disabled=True),
                Button("Clear Logs", id="clear"),
                Button("Quit", id="quit"),
                id="buttons",
            )
            buttons.styles.padding = (0, 0, 0, 0)
            buttons.styles.margin = (0, 0, 0, 0)

            self._status = Static("Ready.", id="status")
            self._style_status_block(self._status)
            self._metrics_view = Static("", id="metrics")
            self._style_status_block(self._metrics_view)

            toggle_rows = [
                self._build_toggle_row(
                    label="Queued Output",
                    toggle_id="toggle-console-queued",
                    tooltip="Toggle display of queued console output.",
                ),
                self._build_toggle_row(
                    label="Async Output",
                    toggle_id="toggle-console-async",
                    tooltip="Toggle display of async console output.",
                ),
            ]

            controls = imports.Vertical(
                buttons,
                self._status,
                self._metrics_view,
                *toggle_rows,
                id="sidebar-controls",
            )
            controls.styles.padding = (0, 0, 0, 0)
            controls.styles.margin = (0, 0, 0, 0)
            return controls

        def _build_toggle_row(self, *, label: str, toggle_id: str, tooltip: str) -> Any:
            caption = Static(label, classes="toggle-label")
            caption.styles.width = "1fr"
            checkbox = Checkbox(label="Enabled", value=True, id=toggle_id, classes="log-toggle", tooltip=tooltip)
            self._style_toggle(checkbox)
            if toggle_id.endswith("queued"):
                self._queued_toggle = checkbox
            else:
                self._async_toggle = checkbox
            return imports.Horizontal(caption, checkbox, classes="toggle-row")

        def _build_log_views(self) -> Any:
            widgets: list[Any] = []
            for heading, element_id, attr in [
                ("Operations", "operations-log", "_operations_log"),
                ("Diagnostics", "diagnostics", "_diagnostics_log"),
                ("Console Output Queued", "console-output-queued", "_queued_console_log"),
                ("Console Output Async", "console-output-async", "_async_console_log"),
                ("Dump Output", "dump-output", "_dump_log"),
            ]:
                widgets.append(Static(heading, classes="log-heading"))
                log_widget = RichLog(id=element_id, wrap=True)
                setattr(self, attr, log_widget)
                widgets.append(log_widget)
            return imports.VerticalScroll(*widgets, id="sidebar-logs")

        def on_mount(self) -> None:  # type: ignore[override]
            try:
                panel = self.query_one("#settings-panel", imports.VerticalScroll)
                panel.focus()
                panel.scroll_home(animate=False)
            except Exception as exc:  # pragma: no cover - focus best effort
                self._append_operation(f"Unable to focus settings panel: {exc}")
            self.set_interval(0.1, self._poll_console_outputs)

        async def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
            button_id = event.button.id
            if button_id == "start":
                await self._start_run()
            elif button_id == "stop":
                await self._stop_run()
            elif button_id == "clear":
                self._reset_log_views()
                self._append_operation("Logs cleared.")
            elif button_id == "quit":
                if self._run_task is not None and not self._run_task.done():
                    await self._stop_run()
                self.exit()

        def _reset_log_views(self) -> None:
            for widget in (
                self._operations_log,
                self._diagnostics_log,
                self._queued_console_log,
                self._async_console_log,
                self._dump_log,
            ):
                if widget is not None:
                    widget.clear()
            self._diagnostic_history.clear()
            self._clear_console_queues()

        def _append_operation(self, message: str) -> None:
            if self._operations_log is not None:
                self._operations_log.write(message)

        def _style_status_block(self, widget: Static) -> None:
            widget.styles.margin = (0, 0, 0, 0)
            widget.styles.padding = (0, 1, 0, 1)

        def _style_toggle(self, toggle: Checkbox) -> None:
            toggle.styles.margin = (0, 0, 0, 1)
            toggle.styles.padding = (0, 1, 0, 1)
            toggle.styles.background = "#001b33"
            toggle.styles.border = ("solid", "#6ea0ff")
            toggle.styles.color = "#d9ecff"

        def on_checkbox_changed(self, event: Checkbox.Changed) -> None:  # type: ignore[override]
            checkbox_id = event.checkbox.id
            if checkbox_id == "toggle-console-queued":
                self._queued_output_enabled = event.value
            elif checkbox_id == "toggle-console-async":
                self._async_output_enabled = event.value
            self._poll_console_outputs()

        def _console_adapter_factory(self, width_hint: int | None) -> Callable[[ConsoleAppearance], ConsolePort]:
            parent = self

            class _CompositeConsoleAdapter(ConsolePort):
                def __init__(self, appearance: ConsoleAppearance) -> None:
                    self._queued = QueueConsoleAdapter(
                        parent._console_queue,
                        export_style=parent._console_export_style,
                        force_color=appearance.force_color,
                        no_color=appearance.no_color,
                        styles=appearance.styles,
                        format_preset=appearance.format_preset,
                        format_template=appearance.format_template,
                        console_width=width_hint,
                    )
                    self._async = AsyncQueueConsoleAdapter(
                        parent._console_async_queue,
                        export_style=parent._console_export_style,
                        force_color=appearance.force_color,
                        no_color=appearance.no_color,
                        styles=appearance.styles,
                        format_preset=appearance.format_preset,
                        format_template=appearance.format_template,
                        console_width=width_hint,
                    )

                def emit(self, event, *, colorize: bool) -> None:  # type: ignore[override]
                    if parent._queued_output_enabled:
                        self._queued.emit(event, colorize=colorize)
                    if parent._async_output_enabled:
                        self._async.emit(event, colorize=colorize)

            return _CompositeConsoleAdapter

        def _clear_console_queues(self) -> None:
            while True:
                try:
                    self._console_queue.get_nowait()
                except Empty:
                    break
            while True:
                try:
                    self._console_async_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

        def _poll_console_outputs(self) -> None:
            html_mode = self._console_export_style == "html"
            self._drain_queue(self._console_queue, self._queued_console_log, self._queued_output_enabled, html_mode)
            self._drain_async_queue(self._console_async_queue, self._async_console_log, self._async_output_enabled, html_mode)

        def _drain_queue(
            self,
            source: Queue[str],
            target: RichLog | None,
            enabled: bool,
            html_mode: bool,
        ) -> None:
            while True:
                try:
                    chunk = source.get_nowait()
                except Empty:
                    break
                self._write_console_chunk(target, chunk, enabled, html_mode)

        def _drain_async_queue(
            self,
            source: asyncio.Queue[str],
            target: RichLog | None,
            enabled: bool,
            html_mode: bool,
        ) -> None:
            while True:
                try:
                    chunk = source.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._write_console_chunk(target, chunk, enabled, html_mode)

        def _write_console_chunk(
            self,
            target: RichLog | None,
            chunk: str,
            enabled: bool,
            html_mode: bool,
        ) -> None:
            if not chunk or not enabled or target is None:
                return
            if html_mode:
                target.write(chunk)
            else:
                target.write(Text.from_ansi(chunk))

        async def _start_run(self) -> None:
            if self._run_task is not None and not self._run_task.done():
                return
            self._reset_log_views()
            self._append_operation("Starting new stress test run.")
            try:
                config = _parse_config(self._rows)
            except ValueError as exc:
                self._set_status(f"[red]Error: {exc}")
                self._append_operation(f"Configuration error: {exc}")
                return

            run_token = object()
            self._prepare_controls_for_run(run_token, config.records_total)
            self._run_task = asyncio.create_task(self._run_stress_test(config, run_token))

        def _prepare_controls_for_run(self, run_token: object, planned: int) -> None:
            self.query_one("#start", Button).disabled = True
            self.query_one("#stop", Button).disabled = False
            self._set_status("Starting stress test…")
            self._active_run_token = run_token
            self._metrics.reset(planned)
            self._update_metrics_view()

        async def _stop_run(self) -> None:
            self._append_operation("Stop requested; cancelling current run.")
            if self._run_task is not None and not self._run_task.done():
                self._run_task.cancel()
                try:
                    await self._run_task
                except asyncio.CancelledError:
                    self._append_operation("Run cancellation acknowledged.")
            self._reset_controls_after_run()
            self._set_status("Stopped.")
            self._poll_console_outputs()

        async def _run_stress_test(self, config: RunConfig, run_token: object) -> None:
            try:
                if not await self._initialise_runtime(config, run_token):
                    return
                await self._emit_records(config, run_token)
            except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
                self._append_operation("Emission cancelled.")
                self._set_status("[yellow]Cancelled.")
            except Exception as exc:  # pragma: no cover - defensive
                self._append_operation(f"Run failed: {exc}")
                self._set_status(f"[red]Run failed: {exc}")
            finally:
                await self._finalise_run(config, run_token)

        async def _initialise_runtime(self, config: RunConfig, run_token: object) -> bool:
            try:
                if self._runtime.is_initialised():
                    self._runtime.shutdown()
                self._append_operation("Initialising runtime…")
                self._clear_console_queues()

                def diagnostic_hook(name: str, payload: Mapping[str, object]) -> None:
                    self.call_from_thread(self._handle_diagnostic, run_token, name, payload, config.diag_history_limit)

                width_hint = self.size.width if self.size.width > 0 else None
                runtime_config = RuntimeConfig(
                    service=config.service,
                    environment=config.environment,
                    console_level=config.console_level,
                    backend_level=config.backend_level,
                    graylog_level=config.graylog_level,
                    graylog_endpoint=config.graylog_endpoint,
                    graylog_protocol=config.graylog_protocol,
                    graylog_tls=config.graylog_tls,
                    enable_ring_buffer=config.enable_ring_buffer,
                    ring_buffer_size=config.ring_buffer_size,
                    enable_journald=config.enable_journald,
                    enable_eventlog=config.enable_eventlog,
                    enable_graylog=config.enable_graylog,
                    queue_enabled=config.queue_enabled,
                    queue_maxsize=config.queue_maxsize,
                    queue_full_policy=config.queue_full_policy,
                    queue_put_timeout=config.queue_put_timeout,
                    queue_stop_timeout=config.queue_stop_timeout,
                    force_color=config.force_color,
                    no_color=config.no_color,
                    console_styles=config.console_styles,
                    console_theme=config.console_theme,
                    console_format_preset=config.console_format_preset,
                    console_format_template=config.console_format_template,
                    dump_format_preset=config.dump_format_preset,
                    dump_format_template=config.dump_format_template,
                    scrub_patterns=config.scrub_patterns,
                    rate_limit=config.rate_limit,
                    payload_limits=config.payload_limits,
                    diagnostic_hook=diagnostic_hook,
                    console_adapter_factory=self._console_adapter_factory(width_hint),
                )
                self._runtime.init(runtime_config)
            except Exception as exc:  # pragma: no cover - runtime init failure
                self._set_status(f"[red]Initialisation failed: {exc}")
                self._append_operation(f"Runtime initialisation failed: {exc}")
                self._reset_controls_after_run()
                if self._active_run_token is run_token:
                    self._active_run_token = None
                self._run_task = None
                return False

            self._append_operation("Runtime initialised.")
            self._poll_console_outputs()
            return True

        async def _emit_records(self, config: RunConfig, run_token: object) -> None:
            logger = self._log.getLogger(config.logger_name)
            cycle_methods: list[Callable[..., dict[str, Any]]] | None = None
            if config.log_level_mode == "CYCLE":
                cycle_methods = [getattr(logger, name.lower()) for name in _LOG_LEVEL_OPTIONS]
                level_method: Callable[..., dict[str, Any]] = cycle_methods[0]
            else:
                if config.log_level is None:
                    raise RuntimeError("Log level must be selected when cycle mode is disabled.")
                level_method = getattr(logger, config.log_level.name.lower())
            context_extra = {f"context_{i:02d}": _make_text(i, config.context_value_length, "ctx") for i in range(config.context_fields)}

            self._set_status("Running…")
            details = ""
            if cycle_methods:
                details = f" (cycle: {' -> '.join(_LOG_LEVEL_OPTIONS)})"
            self._append_operation(f"Emitting {config.records_total} records…{details}")

            stride = max(1, (config.records_total // 50) or 1)
            with self._log.bind(
                service=config.service,
                environment=config.environment,
                job_id="stress-job",
                request_id="stress-suite",
                extra=context_extra or None,
            ):
                for index in range(config.records_total):
                    await asyncio.sleep(0)
                    message = _make_text(index, config.message_length, "log")
                    extras = {f"extra_{i:02d}": _make_text(index * (i + 1), config.extra_value_length, "extra") for i in range(config.extra_fields)}
                    if cycle_methods:
                        current_method = cycle_methods[index % len(cycle_methods)]
                    else:
                        current_method = level_method
                    result = current_method(message, extra=extras or None)
                    self._metrics.emitted += 1
                    if not result.ok:
                        self._metrics.failed += 1
                        self._record_result_failure(
                            run_token=run_token,
                            index=index,
                            result=result,
                            limit=config.diag_history_limit,
                        )
                    if index % 25 == 0 or index + 1 == config.records_total:
                        self._update_metrics_view()
                        self._poll_console_outputs()
                    if (index + 1) % stride == 0 or index + 1 == config.records_total:
                        self._set_status(f"Running… {index + 1}/{config.records_total}")

        def _record_result_failure(self, *, run_token: object, index: int, result: ProcessResult, limit: int) -> None:
            if run_token is not self._active_run_token:
                return
            summary = result.reason or "log emission failed"
            details: dict[str, object] = {
                "event_id": result.event_id,
                "reason": result.reason,
                "queued": result.queued,
                "failed_adapters": result.failed_adapters,
            }
            payload: dict[str, object] = {"index": index, "summary": summary, "details": details}
            self._handle_diagnostic(run_token, "result_failure", payload, limit)

        async def _finalise_run(self, config: RunConfig, run_token: object) -> None:
            self._metrics.finish()
            self._poll_console_outputs()
            self._update_metrics_view()
            self._capture_dump(config)
            await self._shutdown_runtime()
            if self._active_run_token is run_token:
                self._active_run_token = None
            self._run_task = None
            self._reset_controls_after_run()
            summary = (
                f"Completed {self._metrics.emitted}/{self._metrics.planned} events in {self._metrics.elapsed:.2f}s (~{self._metrics.throughput:.1f} evt/s)."
            )
            self._set_status(summary)

        def _capture_dump(self, config: RunConfig) -> None:
            dump_output = None
            try:
                colorize = (self._console_export_style == "ansi" and not config.no_color) or config.force_color
                dump_output = self._log.dump(
                    dump_format=config.dump_format,
                    level=config.dump_level,
                    context_filters=config.dump_context_filters,
                    context_extra_filters=config.dump_context_extra_filters,
                    extra_filters=config.dump_extra_filters,
                    console_format_preset=config.dump_format_preset if config.dump_format == "text" else None,
                    console_format_template=config.dump_format_template if config.dump_format == "text" else None,
                    theme=config.console_theme if config.dump_format == "text" else None,
                    console_styles=config.console_styles if config.dump_format == "text" else None,
                    color=colorize,
                )
            except Exception as exc:  # pragma: no cover - defensive dump handling
                self._append_operation(f"Dump failed: {exc}")
                return
            if not dump_output or self._dump_log is None:
                return
            self._dump_log.clear()
            writer = (lambda line: self._dump_log.write(Text.from_ansi(line))) if self._console_export_style == "ansi" else self._dump_log.write
            for line in dump_output.splitlines():
                writer(line)
            self._append_operation("Ring buffer dump captured.")

        async def _shutdown_runtime(self) -> None:
            try:
                await self._runtime.shutdown_async()
            except RuntimeError as exc:
                self._append_operation(f"Runtime shutdown async failed: {exc}")
            self._poll_console_outputs()

        def _reset_controls_after_run(self) -> None:
            self.query_one("#start", Button).disabled = False
            self.query_one("#stop", Button).disabled = True

        def _handle_diagnostic(
            self,
            run_token: object,
            name: str,
            payload: Mapping[str, object],
            limit: int,
        ) -> None:
            if run_token is not self._active_run_token:
                return

            def _format_summary() -> str | None:
                event_id = payload.get("event_id") or payload.get("details", {}).get("event_id")
                logger_name = payload.get("logger") or payload.get("details", {}).get("logger")
                match name:
                    case "queued":
                        return f"event {event_id or '?'} queued"
                    case "queue_dropped":
                        return f"queue dropped event {event_id or '?'} from logger {logger_name or '?'}"
                    case "queue_full":
                        return f"queue full for logger {logger_name or '?'}"
                    case "queue_shutdown_timeout":
                        timeout = payload.get("timeout")
                        drain = payload.get("drain_completed")
                        return f"queue shutdown timeout ({timeout}s, drain={drain})"
                    case "queue_worker_error":
                        return f"queue worker failed: {payload.get('exception', 'unknown error')}"
                    case "queue_degraded_drop_mode":
                        return "queue switched to drop mode after worker failure"
                    case "queue_drop_callback_error":
                        return f"drop callback raised: {payload.get('exception', 'unknown error')}"
                    case "adapter_error":
                        adapters = payload.get("adapters") or payload.get("adapter")
                        return f"adapter error ({adapters})" if adapters else "adapter error"
                    case "rate_limited":
                        return f"rate limited logger {logger_name or '?'}"
                    case "extra_invalid":
                        return f"invalid extra payload for event {event_id or '?'}"
                    case "result_failure":
                        idx = payload.get("index")
                        summary = payload.get("summary") or "log emission failed"
                        return f"result failure#{idx if idx is not None else ''}: {summary}"
                    case _:
                        return None

            summary = _format_summary()
            if name != "emitted":
                self._metrics.diagnostics[name] += 1
            self._poll_console_outputs()
            if name == "emitted":
                self._update_metrics_view()
                return
            entry_text = f"{name}: {summary}" if summary else f"{name}: {payload}"
            self._diagnostic_history.append(entry_text)
            if len(self._diagnostic_history) > limit:
                self._diagnostic_history.pop(0)
                if self._diagnostics_log is not None:
                    self._diagnostics_log.clear()
                    for line in self._diagnostic_history:
                        self._diagnostics_log.write(line)
                    self._update_metrics_view()
                    return
            if self._diagnostics_log is not None:
                self._diagnostics_log.write(entry_text)
            self._update_metrics_view()

        def _set_status(self, message: str) -> None:
            if self._status is not None:
                self._status.update(message)

        def _update_metrics_view(self) -> None:
            if self._metrics_view is None:
                return
            snapshot: SeveritySnapshot | None = None
            try:
                snapshot = self._runtime.severity_snapshot()
            except (RuntimeError, AttributeError):  # pragma: no cover - runtime may be uninitialised
                snapshot = None
            self._metrics_view.update("\n".join(self._metrics.format_lines(snapshot)))

    return StressTestApp


def create_stresstest_app() -> type[Any]:
    """Build the Textual stress-test application class without side effects.

    Tests and tooling need access to the Textual app definition without
    launching the UI or touching environment-dependent configuration.

    Returns:
        The Textual application class that drives the stress-test interface.

    """
    imports = _import_textual()
    log, runtime = _import_runtime_modules()
    return _create_app_class(imports, log, runtime)


def run() -> None:
    """Entry-point that sets up dependencies and launches the Textual app."""
    _enable_project_configuration()
    StressTestApp = create_stresstest_app()
    StressTestApp().run()


__all__ = ["create_stresstest_app", "run"]


if __name__ == "__main__":
    run()
