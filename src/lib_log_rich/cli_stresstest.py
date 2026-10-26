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
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from queue import Empty, Queue
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict
from rich.text import Text

from lib_log_rich.adapters.console import AsyncQueueConsoleAdapter, QueueConsoleAdapter
from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.enums import ConsoleTheme, DiagnosticEvent, ExportStyle, FormatPreset, GraylogProtocol, QueuePolicy, StrValueEnum
from lib_log_rich.runtime import PayloadLimits, RuntimeConfig, SeveritySnapshot

if TYPE_CHECKING:  # pragma: no cover - Textual imports only for typing
    from collections.abc import Callable, Mapping

    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static

    from lib_log_rich.application.use_cases._types import ProcessResult
    from lib_log_rich.domain.dump_filter import FilterSpecValue
    from lib_log_rich.domain.events import LogEvent
    from lib_log_rich.runtime.settings import ConsoleAppearance


class _SettingCategory(StrValueEnum):
    """Sidebar section a setting is listed under; the value is the heading shown."""

    RUN = "Run"
    QUEUE = "Queue"
    RING_BUFFER = "Ring buffer"
    CONSOLE = "Console"
    STRUCTURED = "Structured"
    SCRUB_LIMITS = "Scrub & limits"
    PAYLOAD = "Payload"
    DIAGNOSTICS = "Diagnostics"


class _LogLevelMode(StrValueEnum):
    """Whether a run emits every record at one level or cycles through all of them."""

    FIXED = "FIXED"
    CYCLE = "CYCLE"


class _ControlButton(StrValueEnum):
    """Sidebar control buttons; the value is the widget id the button is mounted with.

    The value doubles as the DOM id so the styles, the pilot-driven tests and
    the press handler all name the same button without a second spelling.

    Example:
        >>> _ControlButton("start") is _ControlButton.START
        True
        >>> _ControlButton.STOP.selector
        '#stop'

    """

    START = "start"
    STOP = "stop"
    CLEAR = "clear"
    QUIT = "quit"

    @property
    def selector(self) -> str:
        """Return the CSS id selector that finds this button in the DOM."""
        return f"#{self.value}"


class _OutputToggle(StrValueEnum):
    """Checkboxes that show or hide one console output pane; the value is the widget id.

    Example:
        >>> _OutputToggle("toggle-console-async") is _OutputToggle.ASYNC
        True

    """

    QUEUED = "toggle-console-queued"
    ASYNC = "toggle-console-async"


@dataclass(slots=True)
class SettingSpec:
    """Metadata describing a single configurable field in the TUI."""

    key: str
    label: str
    default: str
    help: str
    category: _SettingCategory


class _FormValues(BaseModel):
    """Text of every settings row, read once from the widgets.

    One field per :class:`SettingSpec` key; ``extra="forbid"`` makes a key the
    catalogue adds without a field here fail on the first Start instead of
    being silently ignored.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str
    environment: str
    logger_name: str
    log_level: str
    records_total: str
    message_length: str
    context_fields: str
    context_value_length: str
    extra_fields: str
    extra_value_length: str
    queue_enabled: str
    queue_maxsize: str
    queue_full_policy: str
    queue_put_timeout: str
    queue_stop_timeout: str
    enable_ring_buffer: str
    ring_buffer_size: str
    dump_format: str
    dump_format_preset: str
    dump_format_template: str
    dump_level: str
    dump_context_filters: str
    dump_context_extra_filters: str
    dump_extra_filters: str
    console_level: str
    backend_level: str
    console_theme: str
    console_format_preset: str
    console_format_template: str
    console_styles: str
    force_color: str
    no_color: str
    enable_journald: str
    enable_eventlog: str
    enable_graylog: str
    graylog_endpoint: str
    graylog_protocol: str
    graylog_tls: str
    graylog_level: str
    scrub_patterns: str
    rate_limit: str
    payload_truncate_message: str
    payload_message_max_chars: str
    payload_extra_max_keys: str
    payload_extra_max_value_chars: str
    payload_extra_max_depth: str
    payload_extra_max_total_bytes: str
    payload_context_max_keys: str
    payload_context_max_value_chars: str
    payload_stacktrace_max_frames: str
    diag_history_limit: str


@dataclass(frozen=True, slots=True)
class _RunSettings:
    """Identity of the emitting logger and the level each record uses."""

    service: str
    environment: str
    logger_name: str
    log_level: LogLevel | None
    log_level_mode: _LogLevelMode


@dataclass(frozen=True, slots=True)
class _WorkloadSettings:
    """Shape of the synthetic records a run emits."""

    records_total: int
    message_length: int
    context_fields: int
    context_value_length: int
    extra_fields: int
    extra_value_length: int


@dataclass(frozen=True, slots=True)
class _QueueSettings:
    """Background queue options handed to :class:`RuntimeConfig`."""

    enabled: bool
    maxsize: int
    full_policy: QueuePolicy
    put_timeout: float | None
    stop_timeout: float | None


@dataclass(frozen=True, slots=True)
class _DumpSettings:
    """Ring buffer retention and the dump taken after the run."""

    enable_ring_buffer: bool
    ring_buffer_size: int
    format: DumpFormat
    format_preset: FormatPreset | None
    format_template: str | None
    level: LogLevel | None
    context_filters: dict[str, FilterSpecValue] | None
    context_extra_filters: dict[str, FilterSpecValue] | None
    extra_filters: dict[str, FilterSpecValue] | None


@dataclass(frozen=True, slots=True)
class _LevelSettings:
    """Minimum levels for the console, the structured backends and Graylog."""

    console: LogLevel
    backend: LogLevel
    graylog: LogLevel


@dataclass(frozen=True, slots=True)
class _ConsoleSettings:
    """Console appearance options."""

    theme: ConsoleTheme | None
    format_preset: FormatPreset | None
    format_template: str | None
    styles: dict[str, str] | None
    force_color: bool
    no_color: bool


@dataclass(frozen=True, slots=True)
class _BackendSettings:
    """Structured backends and the Graylog endpoint."""

    enable_journald: bool
    enable_eventlog: bool
    enable_graylog: bool
    graylog_endpoint: tuple[str, int] | None
    graylog_protocol: GraylogProtocol
    graylog_tls: bool


@dataclass(frozen=True, slots=True)
class _LimitSettings:
    """Scrubbing, rate limiting and payload limits."""

    scrub_patterns: dict[str, str] | None
    rate_limit: tuple[int, float] | None
    payload_limits: PayloadLimits


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Validated configuration that will be applied to the runtime under test.

    Every section is parsed from :class:`_FormValues` by its own ``_parse_*``
    function, so a value is converted from widget text exactly once.
    """

    run: _RunSettings
    workload: _WorkloadSettings
    queue: _QueueSettings
    dump: _DumpSettings
    levels: _LevelSettings
    console: _ConsoleSettings
    backends: _BackendSettings
    limits: _LimitSettings
    diag_history_limit: int


@dataclass(slots=True)
class StressMetrics:
    """Collects metrics for the stress run so we can present live feedback."""

    planned: int = 0
    emitted: int = 0
    failed: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    diagnostics: Counter[str] = field(default_factory=Counter[str])

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
# Levels a fixed-level run can pick, and the order a cycling run walks them in.
_EMIT_LEVELS = (LogLevel.DEBUG, LogLevel.INFO, LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL)
# Labels for every FormatPreset; keyed by the enum so a new preset without a label fails at import.
_PRESET_LABELS: dict[FormatPreset, str] = {
    FormatPreset.FULL: "Full",
    FormatPreset.SHORT: "Short",
    FormatPreset.FULL_LOC: "Full + Location",
    FormatPreset.SHORT_LOC: "Short + Location",
    FormatPreset.SHORT_LOC_ICON: "Short + Location + Icon",
}
_PRESET_CHOICES = [(_PRESET_LABELS[preset], preset.value) for preset in FormatPreset]
_DUMP_FORMAT_LABELS: dict[DumpFormat, str] = {
    DumpFormat.TEXT: "Text",
    DumpFormat.JSON: "JSON",
    DumpFormat.HTML_TABLE: "HTML table",
    DumpFormat.HTML_TXT: "HTML text",
}
_DUMP_PRESET_CHOICES = [(_PRESET_LABELS[preset], preset.value) for preset in FormatPreset if preset.renders_in_text_dump]
# Select options are (label, stored text): the form hands back the text, which the parsers convert once.
_CHOICE_FIELDS: dict[str, list[tuple[str, str]]] = {
    "queue_full_policy": [("Block", QueuePolicy.BLOCK.value), ("Drop", QueuePolicy.DROP.value)],
    "graylog_protocol": [("TCP", GraylogProtocol.TCP.value), ("UDP", GraylogProtocol.UDP.value)],
    "dump_format": [(_DUMP_FORMAT_LABELS[dump_format], dump_format.value) for dump_format in DumpFormat],
    "dump_format_preset": list(_DUMP_PRESET_CHOICES),
    "console_format_preset": list(_PRESET_CHOICES),
}
_CHOICE_FIELDS["console_theme"] = [("Runtime default", ""), *((theme.value.title(), theme.value) for theme in ConsoleTheme)]
# `Select` option pairs constructed from the canonical level list.
_base_level_choices = [(level.name, level.name) for level in _EMIT_LEVELS]
_CHOICE_FIELDS["log_level"] = [*_base_level_choices, ("Cycle", _LogLevelMode.CYCLE.value)]
_CHOICE_FIELDS["dump_level"] = [("All levels", ""), *_base_level_choices]
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
        >>> spec = SettingSpec('queue_full_policy', 'Queue policy', 'Drop', '...', _SettingCategory.QUEUE)
        >>> _normalise_choice_default(spec, options=[('Block', 'block'), ('Drop', 'drop')])
        'drop'
        >>> spec = SettingSpec('console_level', 'Console level', 'warning', '...', _SettingCategory.CONSOLE)
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
        >>> {spec.category for spec in specs} == set(_SettingCategory)
        True
        >>> [spec.key for spec in specs] == list(_FormValues.model_fields)
        True

    """
    return [
        # Run parameters
        SettingSpec("service", "Service", _env_default("LOG_SERVICE", "stress-service"), "Value bound to LogContext.service.", _SettingCategory.RUN),
        SettingSpec("environment", "Environment", _env_default("LOG_ENVIRONMENT", "demo"), "Value bound to LogContext.environment.", _SettingCategory.RUN),
        SettingSpec("logger_name", "Logger name", "stress.tui", "Name passed to lib_log_rich.getLogger().", _SettingCategory.RUN),
        SettingSpec(
            "log_level",
            "Log level",
            _env_default("LOG_CONSOLE_LEVEL", "CYCLE"),
            "Level emitted for each record (supports fixed levels or cycle).",
            _SettingCategory.RUN,
        ),
        SettingSpec("records_total", "Log records", "200", "Number of log events to emit.", _SettingCategory.RUN),
        SettingSpec("message_length", "Message length", "80", "Characters per log message.", _SettingCategory.RUN),
        SettingSpec("context_fields", "Context fields", "2", "Context key count bound via bind().", _SettingCategory.RUN),
        SettingSpec("context_value_length", "Context value length", "16", "Character length per context value.", _SettingCategory.RUN),
        SettingSpec("extra_fields", "Extra fields", "2", "Per-event extra key count.", _SettingCategory.RUN),
        SettingSpec("extra_value_length", "Extra value length", "18", "Character length per extra value.", _SettingCategory.RUN),
        # Queue settings
        SettingSpec(
            "queue_enabled", "Enable queue", _env_bool_default("LOG_QUEUE_ENABLED", True), "Use background queue worker when true.", _SettingCategory.QUEUE
        ),
        SettingSpec(
            "queue_maxsize",
            "Queue maxsize",
            _env_int_default("LOG_QUEUE_MAXSIZE", 2048),
            "Maximum queued events before policy applies.",
            _SettingCategory.QUEUE,
        ),
        SettingSpec(
            "queue_full_policy",
            "Queue policy",
            _env_default("LOG_QUEUE_FULL_POLICY", "block"),
            "block waits for space, drop rejects events.",
            _SettingCategory.QUEUE,
        ),
        SettingSpec(
            "queue_put_timeout",
            "Queue put timeout",
            _env_default("LOG_QUEUE_PUT_TIMEOUT", "1.0"),
            "Seconds producer waits when blocking (blank = None).",
            _SettingCategory.QUEUE,
        ),
        SettingSpec(
            "queue_stop_timeout",
            "Queue stop timeout",
            _env_default("LOG_QUEUE_STOP_TIMEOUT", "5.0"),
            "Shutdown drain deadline in seconds (<=0 => wait forever).",
            _SettingCategory.QUEUE,
        ),
        # Ring buffer / dump
        SettingSpec(
            "enable_ring_buffer",
            "Enable ring buffer",
            _env_bool_default("LOG_RING_BUFFER_ENABLED", True),
            "Toggle in-memory ring buffer retention.",
            _SettingCategory.RING_BUFFER,
        ),
        SettingSpec(
            "ring_buffer_size",
            "Ring buffer size",
            _env_int_default("LOG_RING_BUFFER_SIZE", 25_000),
            "Number of events kept in the ring buffer.",
            _SettingCategory.RING_BUFFER,
        ),
        SettingSpec(
            "dump_format",
            "Dump format",
            _env_default("LOG_DUMP_FORMAT", "text"),
            "Dump format name (text/json/html_table/html_txt).",
            _SettingCategory.RING_BUFFER,
        ),
        SettingSpec(
            "dump_format_preset",
            "Dump preset",
            _env_default("LOG_DUMP_FORMAT_PRESET", "full"),
            "Default dump preset used by dump().",
            _SettingCategory.RING_BUFFER,
        ),
        SettingSpec(
            "dump_format_template",
            "Dump template",
            _env_default("LOG_DUMP_FORMAT_TEMPLATE", ""),
            "Override preset with custom template (blank for default).",
            _SettingCategory.RING_BUFFER,
        ),
        SettingSpec(
            "dump_level",
            "Dump min level",
            _env_default("LOG_DUMP_LEVEL", ""),
            "Minimum level included when capturing dumps (blank = all).",
            _SettingCategory.RING_BUFFER,
        ),
        SettingSpec(
            "dump_context_filters", "Context filters", "", "Comma list: key=value or key~contains:value (LogContext fields).", _SettingCategory.RING_BUFFER
        ),
        SettingSpec(
            "dump_context_extra_filters", "Context extra filters", "", "Comma list: key=value or key~regex:^foo$ (context.extra).", _SettingCategory.RING_BUFFER
        ),
        SettingSpec("dump_extra_filters", "Extra filters", "", "Comma list: key=value or key~icontains:bar (event extras).", _SettingCategory.RING_BUFFER),
        # Console formatting
        SettingSpec(
            "console_level", "Console level", _env_default("LOG_CONSOLE_LEVEL", "INFO"), "Minimum level printed to console adapter.", _SettingCategory.CONSOLE
        ),
        SettingSpec(
            "backend_level", "Backend level", _env_default("LOG_BACKEND_LEVEL", "WARNING"), "Minimum level for structured backends.", _SettingCategory.CONSOLE
        ),
        SettingSpec(
            "console_theme",
            "Console theme",
            _env_default("LOG_CONSOLE_THEME", "classic"),
            "Palette from domain.palettes.CONSOLE_STYLE_THEMES.",
            _SettingCategory.CONSOLE,
        ),
        SettingSpec(
            "console_format_preset",
            "Console preset",
            _env_default("LOG_CONSOLE_FORMAT_PRESET", "full"),
            "Named console format preset (full/short/full_loc/short_loc).",
            _SettingCategory.CONSOLE,
        ),
        SettingSpec(
            "console_format_template",
            "Console template",
            _env_default("LOG_CONSOLE_FORMAT_TEMPLATE", ""),
            "Custom console template (blank to use preset).",
            _SettingCategory.CONSOLE,
        ),
        SettingSpec(
            "console_styles",
            "Console styles",
            _env_default("LOG_CONSOLE_STYLES", ""),
            "Comma list LEVEL=style overrides (e.g. INFO=cyan).",
            _SettingCategory.CONSOLE,
        ),
        SettingSpec(
            "force_color", "Force color", _env_bool_default("LOG_FORCE_COLOR", False), "Force ANSI colour even when terminal says no.", _SettingCategory.CONSOLE
        ),
        SettingSpec("no_color", "Disable color", _env_bool_default("LOG_NO_COLOR", False), "Disable colour output entirely.", _SettingCategory.CONSOLE),
        # Structured adapters
        SettingSpec(
            "enable_journald",
            "Enable journald",
            _env_bool_default("LOG_ENABLE_JOURNALD", False),
            "Emit to systemd-journald when true.",
            _SettingCategory.STRUCTURED,
        ),
        SettingSpec(
            "enable_eventlog",
            "Enable Event Log",
            _env_bool_default("LOG_ENABLE_EVENTLOG", False),
            "Emit to Windows Event Log when true.",
            _SettingCategory.STRUCTURED,
        ),
        SettingSpec(
            "enable_graylog",
            "Enable Graylog",
            _env_bool_default("LOG_ENABLE_GRAYLOG", False),
            "Send GELF payloads to Graylog endpoint.",
            _SettingCategory.STRUCTURED,
        ),
        SettingSpec(
            "graylog_endpoint",
            "Graylog endpoint",
            _env_default("LOG_GRAYLOG_ENDPOINT", ""),
            "host:port for Graylog (blank inherits LOG_GRAYLOG_ENDPOINT).",
            _SettingCategory.STRUCTURED,
        ),
        SettingSpec("graylog_protocol", "Graylog protocol", _env_default("LOG_GRAYLOG_PROTOCOL", "tcp"), "tcp or udp.", _SettingCategory.STRUCTURED),
        SettingSpec("graylog_tls", "Graylog TLS", _env_bool_default("LOG_GRAYLOG_TLS", False), "Wrap TCP connection with TLS.", _SettingCategory.STRUCTURED),
        SettingSpec(
            "graylog_level", "Graylog level", _env_default("LOG_GRAYLOG_LEVEL", "WARNING"), "Minimum level for Graylog adapter.", _SettingCategory.STRUCTURED
        ),
        # Scrubbing & rate limits
        SettingSpec(
            "scrub_patterns",
            "Scrub patterns",
            _env_default("LOG_SCRUB_PATTERNS", "password=.+,token=.+,secret=.+"),
            "Comma list key=regex redactions (blank uses defaults).",
            _SettingCategory.SCRUB_LIMITS,
        ),
        SettingSpec(
            "rate_limit", "Rate limit", _env_default("LOG_RATE_LIMIT", ""), "Format MAX:WINDOW_SECONDS (blank disables).", _SettingCategory.SCRUB_LIMITS
        ),
        # Payload limits
        SettingSpec("payload_truncate_message", "Truncate message", "true", "Clamp messages longer than limit when true.", _SettingCategory.PAYLOAD),
        SettingSpec("payload_message_max_chars", "Message max chars", "4096", "Maximum characters kept in message.", _SettingCategory.PAYLOAD),
        SettingSpec("payload_extra_max_keys", "Extra max keys", "25", "Maximum keys stored in per-event extras.", _SettingCategory.PAYLOAD),
        SettingSpec("payload_extra_max_value_chars", "Extra value max chars", "512", "Maximum characters kept per extra value.", _SettingCategory.PAYLOAD),
        SettingSpec("payload_extra_max_depth", "Extra max depth", "3", "Maximum nesting depth preserved in extras.", _SettingCategory.PAYLOAD),
        SettingSpec("payload_extra_max_total_bytes", "Extra max bytes", "8192", "Total bytes budget for extras (blank disables).", _SettingCategory.PAYLOAD),
        SettingSpec("payload_context_max_keys", "Context extra max keys", "20", "Maximum keys stored in context extras.", _SettingCategory.PAYLOAD),
        SettingSpec(
            "payload_context_max_value_chars", "Context value max chars", "256", "Maximum characters kept per context extra value.", _SettingCategory.PAYLOAD
        ),
        SettingSpec("payload_stacktrace_max_frames", "Stacktrace max frames", "10", "Frames kept when truncating stack traces.", _SettingCategory.PAYLOAD),
        # Diagnostics
        SettingSpec("diag_history_limit", "Diagnostics history", "200", "Number of diagnostic entries kept in the log view.", _SettingCategory.DIAGNOSTICS),
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
def _get_setting_groups() -> dict[_SettingCategory, list[SettingSpec]]:
    """Return settings organised by sidebar category for rendering.

    The TUI groups controls by domain (Run, Queue, Console, ...). Building the
    mapping once keeps rendering logic simple and avoids runtime mutation.

    Returns:
        Mapping of category to the ordered specs in that section, in the order
        the categories first appear in the catalogue.

    Example:
        >>> grouped = _get_setting_groups()
        >>> list(grouped)[0] is _SettingCategory.RUN
        True
        >>> isinstance(grouped[_SettingCategory.RUN][0], SettingSpec)
        True

    """
    grouped: dict[_SettingCategory, list[SettingSpec]] = defaultdict(list)
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
        parse_entry = _parse_operator_filter_entry if "~" in entry else _parse_equals_filter_entry
        key, spec = parse_entry(entry, label)
        _append_dump_filter(filters, key, spec)
    return filters or None


def _parse_operator_filter_entry(entry: str, label: str) -> tuple[str, FilterSpecValue]:
    """Parse one ``key~operator:value`` dump filter entry into its key and spec."""
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
    return key, _build_operator_filter_spec(operator, value, label)


def _build_operator_filter_spec(operator: str, value: str, label: str) -> FilterSpecValue:
    """Translate a normalised ``contains``/``icontains``/``regex`` operator into a filter spec."""
    if operator == "contains":
        return {"contains": value}
    if operator == "icontains":
        return {"icontains": value}
    if operator == "regex":
        try:
            pattern = re.compile(value)
        except re.error as exc:
            raise ValueError(f"{label} regex {value!r} is invalid: {exc}") from exc
        return {"pattern": pattern, "regex": True}
    raise ValueError(f"{label} operator must be contains, icontains, or regex.")


def _parse_equals_filter_entry(entry: str, label: str) -> tuple[str, FilterSpecValue]:
    """Parse one ``key=value`` dump filter entry into its key and exact-match spec."""
    if "=" not in entry:
        raise ValueError(f"{label} entries must use key=value or key~operator:value syntax.")
    key, value = entry.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key or not value:
        raise ValueError(f"{label} entries require non-empty key and value.")
    return key, value


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


def _parse_payload_limits(form: _FormValues) -> PayloadLimits:
    """Build :class:`PayloadLimits` from the form values.

    Payload limits constrain diagnostic noise and protect downstream queues. The
    stress harness mirrors runtime behaviour so testers can observe truncation
    thresholds before shipping new defaults.

    Args:
        form: Text of every settings row.

    Returns:
        Model instance populated with validated limits.

    Example:
        >>> defaults = {spec.key: spec.default for spec in _get_settings()}
        >>> form = _FormValues.model_validate({**defaults, 'payload_message_max_chars': '120', 'payload_extra_max_total_bytes': ''})
        >>> limits = _parse_payload_limits(form)
        >>> limits.message_max_chars, limits.extra_max_total_bytes
        (120, None)

    """
    total_bytes = form.payload_extra_max_total_bytes
    return PayloadLimits(
        truncate_message=_parse_bool(form.payload_truncate_message, default=True),
        message_max_chars=_parse_int(form.payload_message_max_chars, "Message max chars", minimum=1),
        extra_max_keys=_parse_int(form.payload_extra_max_keys, "Extra max keys", minimum=1),
        extra_max_value_chars=_parse_int(form.payload_extra_max_value_chars, "Extra value max chars", minimum=1),
        extra_max_depth=_parse_int(form.payload_extra_max_depth, "Extra max depth", minimum=1),
        extra_max_total_bytes=_parse_int(total_bytes, "Extra max bytes", minimum=1) if total_bytes else None,
        context_max_keys=_parse_int(form.payload_context_max_keys, "Context extra max keys", minimum=1),
        context_max_value_chars=_parse_int(form.payload_context_max_value_chars, "Context value max chars", minimum=1),
        stacktrace_max_frames=_parse_int(form.payload_stacktrace_max_frames, "Stacktrace max frames", minimum=1),
    )


class _ValueRow(Protocol):
    """A settings row that hands back its current text."""

    def value(self) -> str:
        """Return the row's text, trimmed."""
        ...


def _collect_values(rows: Mapping[str, _ValueRow]) -> _FormValues:
    """Read every settings row once into a :class:`_FormValues` snapshot.

    This is where user-typed text enters the TUI: the parsers below read the
    snapshot's fields and never touch the widgets.

    Args:
        rows: Mapping of setting keys to row components exposing ``value()``.

    Returns:
        Snapshot of user-entered text for further normalisation.

    Raises:
        pydantic.ValidationError: If ``rows`` lacks a setting or has one the
            snapshot does not know.

    Example:
        >>> class Dummy:  # minimal duck type for doctest
        ...     def __init__(self, value):
        ...         self._value = value
        ...     def value(self):
        ...         return self._value
        >>> form = _collect_values({spec.key: Dummy(spec.default) for spec in _get_settings()})
        >>> form.logger_name
        'stress.tui'
        >>> try:
        ...     _collect_values({'service': Dummy('demo')})
        ... except ValueError as exc:
        ...     print(type(exc).__name__)
        ValidationError

    """
    return _FormValues.model_validate({key: row.value() for key, row in rows.items()})


def _parse_run_settings(form: _FormValues) -> _RunSettings:
    """Parse the logger identity and the level, or level cycle, each record uses.

    Example:
        >>> defaults = {spec.key: spec.default for spec in _get_settings()}
        >>> _parse_run_settings(_FormValues.model_validate({**defaults, 'log_level': 'Cycle'})).log_level_mode is _LogLevelMode.CYCLE
        True
        >>> _parse_run_settings(_FormValues.model_validate({**defaults, 'log_level': 'loud'}))
        Traceback (most recent call last):
        ...
        ValueError: Invalid log level provided.

    """
    service = form.service or "stress-service"
    environment = form.environment or "demo"
    logger_name = form.logger_name or "stress.tui"
    log_level_raw = form.log_level.strip().upper() or "INFO"
    if log_level_raw == _LogLevelMode.CYCLE.value:
        return _RunSettings(service=service, environment=environment, logger_name=logger_name, log_level=None, log_level_mode=_LogLevelMode.CYCLE)
    try:
        log_level = LogLevel.from_name(log_level_raw)
    except ValueError as exc:
        raise ValueError("Invalid log level provided.") from exc
    return _RunSettings(service=service, environment=environment, logger_name=logger_name, log_level=log_level, log_level_mode=_LogLevelMode.FIXED)


def _parse_workload_settings(form: _FormValues) -> _WorkloadSettings:
    """Parse workload generation configuration."""
    return _WorkloadSettings(
        records_total=_parse_int(form.records_total, "Log records", minimum=1),
        message_length=_parse_int(form.message_length, "Message length", minimum=1),
        context_fields=_parse_int(form.context_fields, "Context fields", minimum=0),
        context_value_length=_parse_int(form.context_value_length, "Context value length", minimum=1),
        extra_fields=_parse_int(form.extra_fields, "Extra fields", minimum=0),
        extra_value_length=_parse_int(form.extra_value_length, "Extra value length", minimum=1),
    )


def _parse_queue_settings(form: _FormValues) -> _QueueSettings:
    """Parse queue-related configuration.

    Example:
        >>> defaults = {spec.key: spec.default for spec in _get_settings()}
        >>> _parse_queue_settings(_FormValues.model_validate({**defaults, 'queue_full_policy': 'Drop'})).full_policy is QueuePolicy.DROP
        True
        >>> _parse_queue_settings(_FormValues.model_validate({**defaults, 'queue_full_policy': 'sometimes'}))
        Traceback (most recent call last):
        ...
        ValueError: Queue policy must be 'block' or 'drop'.

    """
    try:
        full_policy = QueuePolicy.from_str(form.queue_full_policy.strip() or QueuePolicy.BLOCK.value)
    except ValueError as exc:
        raise ValueError("Queue policy must be 'block' or 'drop'.") from exc

    raw_stop_timeout = _parse_float(form.queue_stop_timeout, "Queue stop timeout", allow_blank=True, allow_non_positive=True)
    # <=0 is the form's spelling of "wait forever", which the runtime spells None.
    stop_timeout = raw_stop_timeout if (raw_stop_timeout and raw_stop_timeout > 0) else None

    return _QueueSettings(
        enabled=_parse_bool(form.queue_enabled, default=True),
        maxsize=_parse_int(form.queue_maxsize, "Queue maxsize", minimum=1),
        full_policy=full_policy,
        put_timeout=_parse_float(form.queue_put_timeout, "Queue put timeout", allow_blank=True),
        stop_timeout=stop_timeout,
    )


def _parse_format_preset(text: str) -> FormatPreset | None:
    """Parse a preset select's text; blank keeps the runtime default.

    Example:
        >>> _parse_format_preset('short_loc') is FormatPreset.SHORT_LOC
        True
        >>> _parse_format_preset('') is None
        True

    """
    stripped = text.strip()
    return FormatPreset.from_str(stripped) if stripped else None


def _parse_console_theme(text: str) -> ConsoleTheme | None:
    """Parse a theme select's text; blank keeps the runtime default.

    Example:
        >>> _parse_console_theme('neon') is ConsoleTheme.NEON
        True
        >>> _parse_console_theme('') is None
        True

    """
    stripped = text.strip()
    return ConsoleTheme.from_str(stripped) if stripped else None


def _parse_dump_settings(form: _FormValues) -> _DumpSettings:
    """Parse dump/ring buffer configuration.

    Example:
        >>> defaults = {spec.key: spec.default for spec in _get_settings()}
        >>> chosen = {'dump_format': 'JSON', 'dump_format_preset': 'short_loc', 'dump_level': 'info'}
        >>> dump = _parse_dump_settings(_FormValues.model_validate({**defaults, **chosen}))
        >>> dump.format is DumpFormat.JSON, dump.format_preset is FormatPreset.SHORT_LOC, dump.level is LogLevel.INFO
        (True, True, True)
        >>> _parse_dump_settings(_FormValues.model_validate({**defaults, 'dump_format': 'xml'}))
        Traceback (most recent call last):
        ...
        ValueError: Dump format must be one of: text, json, html_table, html_txt.
        >>> _parse_dump_settings(_FormValues.model_validate({**defaults, 'dump_level': 'loud'}))
        Traceback (most recent call last):
        ...
        ValueError: Invalid dump level specified.

    """
    try:
        dump_format = DumpFormat.from_name(form.dump_format or DumpFormat.TEXT.value)
    except ValueError as exc:
        raise ValueError(f"Dump format must be one of: {', '.join(member.value for member in DumpFormat)}.") from exc

    dump_level_raw = form.dump_level.strip().upper()
    if dump_level_raw:
        try:
            dump_level = LogLevel.from_name(dump_level_raw)
        except ValueError as exc:
            raise ValueError("Invalid dump level specified.") from exc
    else:
        dump_level = None

    return _DumpSettings(
        enable_ring_buffer=_parse_bool(form.enable_ring_buffer, default=True),
        ring_buffer_size=_parse_int(form.ring_buffer_size, "Ring buffer size", minimum=1),
        format=dump_format,
        format_preset=_parse_format_preset(form.dump_format_preset),
        format_template=form.dump_format_template.strip() or None,
        level=dump_level,
        context_filters=_parse_dump_filters(form.dump_context_filters, "Context filters"),
        context_extra_filters=_parse_dump_filters(form.dump_context_extra_filters, "Context extra filters"),
        extra_filters=_parse_dump_filters(form.dump_extra_filters, "Extra filters"),
    )


def _parse_level_settings(form: _FormValues) -> _LevelSettings:
    """Parse and validate logging level configuration.

    Example:
        >>> defaults = {spec.key: spec.default for spec in _get_settings()}
        >>> chosen = {'console_level': 'debug', 'backend_level': 'error', 'graylog_level': 'critical'}
        >>> levels = _parse_level_settings(_FormValues.model_validate({**defaults, **chosen}))
        >>> levels.console, levels.backend, levels.graylog
        (<LogLevel.DEBUG: 10>, <LogLevel.ERROR: 40>, <LogLevel.CRITICAL: 50>)
        >>> _parse_level_settings(_FormValues.model_validate({**defaults, 'graylog_level': 'loud'}))
        Traceback (most recent call last):
        ...
        ValueError: Invalid console/backend/Graylog level specified.

    """
    try:
        return _LevelSettings(
            console=LogLevel.from_name(form.console_level or LogLevel.INFO.name),
            backend=LogLevel.from_name(form.backend_level or LogLevel.WARNING.name),
            graylog=LogLevel.from_name(form.graylog_level or LogLevel.WARNING.name),
        )
    except ValueError as exc:
        raise ValueError("Invalid console/backend/Graylog level specified.") from exc


def _parse_console_settings(form: _FormValues) -> _ConsoleSettings:
    """Parse console appearance configuration."""
    return _ConsoleSettings(
        theme=_parse_console_theme(form.console_theme),
        format_preset=_parse_format_preset(form.console_format_preset),
        format_template=form.console_format_template.strip() or None,
        styles=_parse_styles(form.console_styles),
        force_color=_parse_bool(form.force_color, default=False),
        no_color=_parse_bool(form.no_color, default=False),
    )


def _parse_backend_settings(form: _FormValues) -> _BackendSettings:
    """Parse backend adapter configuration.

    Example:
        >>> defaults = {spec.key: spec.default for spec in _get_settings()}
        >>> _parse_backend_settings(_FormValues.model_validate({**defaults, 'graylog_protocol': 'UDP'})).graylog_protocol is GraylogProtocol.UDP
        True
        >>> _parse_backend_settings(_FormValues.model_validate({**defaults, 'graylog_protocol': 'http'}))
        Traceback (most recent call last):
        ...
        ValueError: Graylog protocol must be 'tcp' or 'udp'.

    """
    try:
        graylog_protocol = GraylogProtocol.from_str(form.graylog_protocol.strip() or GraylogProtocol.TCP.value)
    except ValueError as exc:
        raise ValueError("Graylog protocol must be 'tcp' or 'udp'.") from exc

    return _BackendSettings(
        enable_journald=_parse_bool(form.enable_journald, default=False),
        enable_eventlog=_parse_bool(form.enable_eventlog, default=False),
        enable_graylog=_parse_bool(form.enable_graylog, default=False),
        graylog_endpoint=_parse_endpoint(form.graylog_endpoint),
        graylog_protocol=graylog_protocol,
        graylog_tls=_parse_bool(form.graylog_tls, default=False),
    )


def _parse_limit_settings(form: _FormValues) -> _LimitSettings:
    """Parse security and limits configuration."""
    return _LimitSettings(
        scrub_patterns=_parse_patterns(form.scrub_patterns),
        rate_limit=_parse_rate_limit(form.rate_limit),
        payload_limits=_parse_payload_limits(form),
    )


def _parse_config(rows: Mapping[str, _ValueRow]) -> RunConfig:
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
        >>> config.run.service, config.run.environment
        ('stress-service', 'demo')

    """
    form = _collect_values(rows)
    # Keyword order is evaluation order: the first section that fails names the
    # error the status line shows, so it follows the form from top to bottom.
    return RunConfig(
        run=_parse_run_settings(form),
        workload=_parse_workload_settings(form),
        queue=_parse_queue_settings(form),
        dump=_parse_dump_settings(form),
        levels=_parse_level_settings(form),
        console=_parse_console_settings(form),
        backends=_parse_backend_settings(form),
        limits=_parse_limit_settings(form),
        diag_history_limit=_parse_int(form.diag_history_limit, "Diagnostics history", minimum=10),
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

    App: type[App[None]]
    Container: type[Container]
    Horizontal: type[Horizontal]
    Vertical: type[Vertical]
    VerticalScroll: type[VerticalScroll]
    Button: type[Button]
    Checkbox: type[Checkbox]
    Input: type[Input]
    RichLog: type[RichLog]
    Select: type[Select[str]]
    Static: type[Static]


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


@dataclass(frozen=True, slots=True)
class _DiagnosticFields:
    """One diagnostic payload plus the event id and logger name resolved from it."""

    payload: Mapping[str, object]
    event_id: object
    logger_name: object


@dataclass(frozen=True, slots=True)
class _ReceivedDiagnostic:
    """A diagnostic name as the hook received it, resolved to its event once.

    The hook's name is a plain ``str`` by contract. ``event`` is ``None`` for a
    name this TUI does not know (a newer runtime, a custom emitter), which is
    still counted and shown with its raw payload rather than crashing the TUI.
    """

    name: str
    event: DiagnosticEvent | None

    @classmethod
    def from_hook(cls, name: str) -> _ReceivedDiagnostic:
        """Resolve a hook name.

        Example:
            >>> _ReceivedDiagnostic.from_hook('queued').event is DiagnosticEvent.QUEUED
            True
            >>> _ReceivedDiagnostic.from_hook('not_a_diagnostic').event is None
            True

        """
        try:
            return cls(name=name, event=DiagnosticEvent(name))
        except ValueError:
            return cls(name=name, event=None)


@dataclass(frozen=True, slots=True)
class _ResultFailure:
    """A record whose logging call returned a failed :class:`ProcessResult`."""

    index: int
    result: ProcessResult

    # Shown and counted under this name beside the runtime's own diagnostics.
    NAME = "result_failure"

    def summary(self) -> str:
        """Return the one-line summary shown in the diagnostics view.

        Example:
            >>> from lib_log_rich.application.use_cases._types import ProcessResult
            >>> from lib_log_rich.domain.enums import DropReason
            >>> _ResultFailure(index=7, result=ProcessResult(ok=False, reason=DropReason.RATE_LIMITED)).summary()
            'result failure#7: rate_limited'
            >>> _ResultFailure(index=0, result=ProcessResult(ok=False)).summary()
            'result failure#0: log emission failed'

        """
        reason = self.result.reason or "log emission failed"
        return f"result failure#{self.index}: {reason}"


def _summarise_adapter_error(fields: _DiagnosticFields) -> str:
    """Summarise an ``adapter_error`` diagnostic, naming the adapters when known."""
    adapters = fields.payload.get("adapters") or fields.payload.get("adapter")
    return f"adapter error ({adapters})" if adapters else "adapter error"


def _summarise_queue_shutdown_timeout(fields: _DiagnosticFields) -> str:
    """Summarise a ``queue_shutdown_timeout`` diagnostic with its timeout and drain state."""
    timeout = fields.payload.get("timeout")
    drain = fields.payload.get("drain_completed")
    return f"queue shutdown timeout ({timeout}s, drain={drain})"


_DIAGNOSTIC_SUMMARIES: dict[DiagnosticEvent, Callable[[_DiagnosticFields], str]] = {
    DiagnosticEvent.QUEUED: lambda f: f"event {f.event_id or '?'} queued",
    DiagnosticEvent.QUEUE_DROPPED: lambda f: f"queue dropped event {f.event_id or '?'} from logger {f.logger_name or '?'}",
    DiagnosticEvent.QUEUE_FULL: lambda f: f"queue full for logger {f.logger_name or '?'}",
    DiagnosticEvent.QUEUE_SHUTDOWN_TIMEOUT: _summarise_queue_shutdown_timeout,
    DiagnosticEvent.QUEUE_WORKER_ERROR: lambda f: f"queue worker failed: {f.payload.get('exception', 'unknown error')}",
    DiagnosticEvent.QUEUE_DEGRADED_DROP_MODE: lambda _f: "queue switched to drop mode after worker failure",
    DiagnosticEvent.QUEUE_DROP_CALLBACK_ERROR: lambda f: f"drop callback raised: {f.payload.get('exception', 'unknown error')}",
    DiagnosticEvent.ADAPTER_ERROR: _summarise_adapter_error,
    DiagnosticEvent.RATE_LIMITED: lambda f: f"rate limited logger {f.logger_name or '?'}",
    DiagnosticEvent.EXTRA_INVALID: lambda f: f"invalid extra payload for event {f.event_id or '?'}",
}


def _format_diagnostic_summary(event: DiagnosticEvent, payload: Mapping[str, object]) -> str | None:
    """Render a one-line summary for a diagnostic, or ``None`` when it has no summary.

    Example:
        >>> _format_diagnostic_summary(DiagnosticEvent.QUEUE_FULL, {'logger': 'app'})
        'queue full for logger app'
        >>> _format_diagnostic_summary(DiagnosticEvent.MESSAGE_TRUNCATED, {}) is None
        True

    """
    formatter = _DIAGNOSTIC_SUMMARIES.get(event)
    if formatter is None:
        return None
    return formatter(_DiagnosticFields(payload=payload, event_id=payload.get("event_id"), logger_name=payload.get("logger")))


if TYPE_CHECKING:
    # The type checker sees the mixin as the App it is mixed into, so App methods used here
    # (query_one, set_interval, call_from_thread, ...) are checked. At run time the base is
    # object: textual is imported lazily, and the concrete class adds App to its bases.
    _AppBase = App[None]
else:
    _AppBase = object


class _StressTestAppBehaviour(_AppBase):
    """Behaviour of the stress-test Textual app, kept outside the lazy class factory.

    Textual is imported lazily, so the concrete app class is created inside
    :func:`_create_app_class`. Only that class's base list depends on the lazy
    import; every method lives here and reaches Textual widgets through
    ``self._imports``. The concrete class lists this mixin before
    ``textual.app.App`` so these methods take precedence over the App defaults,
    and Textual's handler lookup walks the whole MRO, so ``on_*`` handlers
    defined here are dispatched.
    """

    def _init_stress_state(self, *, imports: _TextualImports, log: Any, runtime: Any, setting_row_cls: type[Any]) -> None:
        self._imports = imports
        self._setting_row_cls = setting_row_cls
        self._rows: dict[str, Any] = {}
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
        self._console_export_style = ExportStyle.ANSI
        self._queued_output_enabled = True
        self._async_output_enabled = True
        self._queued_toggle: Checkbox | None = None
        self._async_toggle: Checkbox | None = None
        # Not self._log: textual.App has a _log method its logger calls once devtools connect.
        self._lib_log: Any = log
        self._runtime = runtime

    def compose(self) -> ComposeResult:
        yield self._build_root_container()

    def _build_root_container(self) -> Any:
        return self._imports.Container(self._build_layout(), id="backdrop")

    def _build_layout(self) -> Any:
        settings_panel = self._build_settings_panel()
        sidebar = self._build_sidebar()
        return self._imports.Horizontal(settings_panel, sidebar, id="layout")

    def _build_settings_panel(self) -> Any:
        widgets: list[Any] = [
            self._imports.Static("lib_log_rich Stress Test", id="title"),
            self._imports.Static(
                "Configure runtime settings, then press Start to emit synthetic logs across all adapters.",
                id="subtitle",
            ),
        ]
        for category, specs in _get_setting_groups().items():
            widgets.append(self._imports.Static(category.value, classes="category-label"))
            for spec in specs:
                row = self._setting_row_cls(spec)
                self._rows[spec.key] = row
                widgets.append(row)
        panel = self._imports.VerticalScroll(*widgets, id="settings-panel")
        panel.can_focus = True
        panel.can_focus_children = True
        return panel

    def _build_sidebar(self) -> Any:
        controls = self._build_sidebar_controls()
        log_views = self._build_log_views()
        return self._imports.Vertical(controls, log_views, id="sidebar")

    def _build_sidebar_controls(self) -> Any:
        buttons = self._imports.Horizontal(
            self._imports.Button("Start", id=_ControlButton.START.value),
            self._imports.Button("Stop", id=_ControlButton.STOP.value, disabled=True),
            self._imports.Button("Clear Logs", id=_ControlButton.CLEAR.value),
            self._imports.Button("Quit", id=_ControlButton.QUIT.value),
            id="buttons",
        )
        buttons.styles.padding = (0, 0, 0, 0)
        buttons.styles.margin = (0, 0, 0, 0)

        self._status = self._imports.Static("Ready.", id="status")
        self._style_status_block(self._status)
        self._metrics_view = self._imports.Static("", id="metrics")
        self._style_status_block(self._metrics_view)

        toggle_rows = [
            self._build_toggle_row(
                label="Queued Output",
                toggle=_OutputToggle.QUEUED,
                tooltip="Toggle display of queued console output.",
            ),
            self._build_toggle_row(
                label="Async Output",
                toggle=_OutputToggle.ASYNC,
                tooltip="Toggle display of async console output.",
            ),
        ]

        controls = self._imports.Vertical(
            buttons,
            self._status,
            self._metrics_view,
            *toggle_rows,
            id="sidebar-controls",
        )
        controls.styles.padding = (0, 0, 0, 0)
        controls.styles.margin = (0, 0, 0, 0)
        return controls

    def _build_toggle_row(self, *, label: str, toggle: _OutputToggle, tooltip: str) -> Any:
        caption = self._imports.Static(label, classes="toggle-label")
        caption.styles.width = "1fr"
        checkbox = self._imports.Checkbox(label="Enabled", value=True, id=toggle.value, classes="log-toggle", tooltip=tooltip)
        self._style_toggle(checkbox)
        if toggle is _OutputToggle.QUEUED:
            self._queued_toggle = checkbox
        else:
            self._async_toggle = checkbox
        return self._imports.Horizontal(caption, checkbox, classes="toggle-row")

    def _build_log_views(self) -> Any:
        widgets: list[Any] = []
        for heading, element_id, attr in [
            ("Operations", "operations-log", "_operations_log"),
            ("Diagnostics", "diagnostics", "_diagnostics_log"),
            ("Console Output Queued", "console-output-queued", "_queued_console_log"),
            ("Console Output Async", "console-output-async", "_async_console_log"),
            ("Dump Output", "dump-output", "_dump_log"),
        ]:
            widgets.append(self._imports.Static(heading, classes="log-heading"))
            log_widget = self._imports.RichLog(id=element_id, wrap=True)
            setattr(self, attr, log_widget)
            widgets.append(log_widget)
        return self._imports.VerticalScroll(*widgets, id="sidebar-logs")

    def on_mount(self) -> None:
        try:
            panel = self.query_one("#settings-panel", self._imports.VerticalScroll)
            panel.focus()
            panel.scroll_home(animate=False)
        except Exception as exc:  # pragma: no cover - focus best effort
            self._append_operation(f"Unable to focus settings panel: {exc}")
        self.set_interval(0.1, self._poll_console_outputs)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        # Textual hands the id back as a plain str | None; it is parsed into the enum once, here.
        try:
            button = _ControlButton(event.button.id)
        except ValueError:
            return
        if button is _ControlButton.START:
            await self._start_run()
        elif button is _ControlButton.STOP:
            await self._stop_run()
        elif button is _ControlButton.CLEAR:
            self._reset_log_views()
            self._append_operation("Logs cleared.")
        else:
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

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        # Textual hands the id back as a plain str | None; a checkbox this app did not mount changes no pane.
        try:
            toggle: _OutputToggle | None = _OutputToggle(event.checkbox.id)
        except ValueError:
            toggle = None
        if toggle is _OutputToggle.QUEUED:
            self._queued_output_enabled = event.value
        elif toggle is _OutputToggle.ASYNC:
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

            def emit(self, event: LogEvent, *, colorize: bool) -> None:
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
        html_mode = self._console_export_style is ExportStyle.HTML
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
            self._set_status(f"Error: {exc}", style="red")
            self._append_operation(f"Configuration error: {exc}")
            return

        run_token = object()
        self._prepare_controls_for_run(run_token, config.workload.records_total)
        self._run_task = asyncio.create_task(self._run_stress_test(config, run_token))

    def _prepare_controls_for_run(self, run_token: object, planned: int) -> None:
        self.query_one(_ControlButton.START.selector, self._imports.Button).disabled = True
        self.query_one(_ControlButton.STOP.selector, self._imports.Button).disabled = False
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
            self._set_status("Cancelled.", style="yellow")
        except Exception as exc:  # pragma: no cover - defensive
            self._append_operation(f"Run failed: {exc}")
            self._set_status(f"Run failed: {exc}", style="red")
        finally:
            await self._finalise_run(config, run_token)

    async def _initialise_runtime(self, config: RunConfig, run_token: object) -> bool:
        try:
            if self._runtime.is_initialised():
                self._runtime.shutdown()
            self._append_operation("Initialising runtime…")
            self._clear_console_queues()

            # Caller-side diagnostics (queued, rate_limited, truncations) arrive on this, the
            # app's own thread, where call_from_thread raises and the runtime swallows it.
            app_thread = threading.get_ident()

            def diagnostic_hook(name: str, payload: Mapping[str, object]) -> None:
                if threading.get_ident() == app_thread:
                    self._handle_diagnostic(run_token, name, payload, config.diag_history_limit)
                    return
                self.call_from_thread(self._handle_diagnostic, run_token, name, payload, config.diag_history_limit)

            width_hint = self.size.width if self.size.width > 0 else None
            runtime_config = RuntimeConfig(
                service=config.run.service,
                environment=config.run.environment,
                console_level=config.levels.console,
                backend_level=config.levels.backend,
                graylog_level=config.levels.graylog,
                graylog_endpoint=config.backends.graylog_endpoint,
                graylog_protocol=config.backends.graylog_protocol,
                graylog_tls=config.backends.graylog_tls,
                enable_ring_buffer=config.dump.enable_ring_buffer,
                ring_buffer_size=config.dump.ring_buffer_size,
                enable_journald=config.backends.enable_journald,
                enable_eventlog=config.backends.enable_eventlog,
                enable_graylog=config.backends.enable_graylog,
                queue_enabled=config.queue.enabled,
                queue_maxsize=config.queue.maxsize,
                queue_full_policy=config.queue.full_policy,
                queue_put_timeout=config.queue.put_timeout,
                queue_stop_timeout=config.queue.stop_timeout,
                force_color=config.console.force_color,
                no_color=config.console.no_color,
                console_styles=config.console.styles,
                console_theme=config.console.theme,
                console_format_preset=config.console.format_preset,
                console_format_template=config.console.format_template,
                dump_format_preset=config.dump.format_preset,
                dump_format_template=config.dump.format_template,
                scrub_patterns=config.limits.scrub_patterns,
                rate_limit=config.limits.rate_limit,
                payload_limits=config.limits.payload_limits,
                diagnostic_hook=diagnostic_hook,
                console_adapter_factory=self._console_adapter_factory(width_hint),
            )
            self._runtime.init(runtime_config)
        except Exception as exc:  # pragma: no cover - runtime init failure
            self._set_status(f"Initialisation failed: {exc}", style="red")
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
        run = config.run
        workload = config.workload
        logger = self._lib_log.getLogger(run.logger_name)
        cycle_levels: tuple[LogLevel, ...] | None = None
        if run.log_level_mode is _LogLevelMode.CYCLE:
            cycle_levels = _EMIT_LEVELS
            fixed_level = cycle_levels[0]
        else:
            if run.log_level is None:
                raise RuntimeError("Log level must be selected when cycle mode is disabled.")
            fixed_level = run.log_level
        context_extra = {f"context_{i:02d}": _make_text(i, workload.context_value_length, "ctx") for i in range(workload.context_fields)}

        self._set_status("Running…")
        details = ""
        if cycle_levels:
            details = f" (cycle: {' -> '.join(level.name for level in _EMIT_LEVELS)})"
        self._append_operation(f"Emitting {workload.records_total} records…{details}")

        stride = max(1, (workload.records_total // 50) or 1)
        with self._lib_log.bind(
            service=run.service,
            environment=run.environment,
            job_id="stress-job",
            request_id="stress-suite",
            extra=context_extra or None,
        ):
            for index in range(workload.records_total):
                await asyncio.sleep(0)
                message = _make_text(index, workload.message_length, "log")
                extras = {f"extra_{i:02d}": _make_text(index * (i + 1), workload.extra_value_length, "extra") for i in range(workload.extra_fields)}
                level = cycle_levels[index % len(cycle_levels)] if cycle_levels else fixed_level
                result: ProcessResult = logger.log(level, message, extra=extras or None)
                self._metrics.emitted += 1
                if not result.ok:
                    self._metrics.failed += 1
                    self._record_result_failure(run_token, _ResultFailure(index=index, result=result), limit=config.diag_history_limit)
                if index % 25 == 0 or index + 1 == workload.records_total:
                    self._update_metrics_view()
                    self._poll_console_outputs()
                if (index + 1) % stride == 0 or index + 1 == workload.records_total:
                    self._set_status(f"Running… {index + 1}/{workload.records_total}")

    def _record_result_failure(self, run_token: object, failure: _ResultFailure, *, limit: int) -> None:
        if run_token is not self._active_run_token:
            return
        self._record_counted_diagnostic(_ResultFailure.NAME, f"{_ResultFailure.NAME}: {failure.summary()}", limit)

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
        summary = f"Completed {self._metrics.emitted}/{self._metrics.planned} events in {self._metrics.elapsed:.2f}s (~{self._metrics.throughput:.1f} evt/s)."
        self._set_status(summary)

    def _capture_dump(self, config: RunConfig) -> None:
        dump = config.dump
        console = config.console
        # Presets, templates, theme and styles shape text lines only; JSON ignores them.
        text_dump = dump.format is DumpFormat.TEXT
        ansi_export = self._console_export_style is ExportStyle.ANSI
        dump_output = ""
        try:
            colorize = (ansi_export and not console.no_color) or console.force_color
            dump_output: str = self._lib_log.dump(
                dump_format=dump.format,
                level=dump.level,
                context_filters=dump.context_filters,
                context_extra_filters=dump.context_extra_filters,
                extra_filters=dump.extra_filters,
                console_format_preset=dump.format_preset if text_dump else None,
                console_format_template=dump.format_template if text_dump else None,
                theme=console.theme if text_dump else None,
                console_styles=console.styles if text_dump else None,
                color=colorize,
            )
        except Exception as exc:  # pragma: no cover - defensive dump handling
            self._append_operation(f"Dump failed: {exc}")
            return
        if not dump_output or self._dump_log is None:
            return
        self._dump_log.clear()
        for line in dump_output.splitlines():
            self._dump_log.write(Text.from_ansi(line) if ansi_export else line)
        self._append_operation("Ring buffer dump captured.")

    async def _shutdown_runtime(self) -> None:
        try:
            await self._runtime.shutdown_async()
        except RuntimeError as exc:
            self._append_operation(f"Runtime shutdown async failed: {exc}")
        self._poll_console_outputs()

    def _reset_controls_after_run(self) -> None:
        self.query_one(_ControlButton.START.selector, self._imports.Button).disabled = False
        self.query_one(_ControlButton.STOP.selector, self._imports.Button).disabled = True

    def _handle_diagnostic(
        self,
        run_token: object,
        name: str,
        payload: Mapping[str, object],
        limit: int,
    ) -> None:
        if run_token is not self._active_run_token:
            return
        # The hook's edge: the plain-str name becomes a DiagnosticEvent here, once.
        received = _ReceivedDiagnostic.from_hook(name)
        if received.event in {DiagnosticEvent.EMITTED, DiagnosticEvent.QUEUED}:
            # One per record (emitted inline, queued with the queue on): counting or
            # listing them would bury every other diagnostic.
            self._poll_console_outputs()
            self._update_metrics_view()
            return
        summary = _format_diagnostic_summary(received.event, payload) if received.event is not None else None
        entry_text = f"{received.name}: {summary}" if summary else f"{received.name}: {payload}"
        self._record_counted_diagnostic(received.name, entry_text, limit)

    def _record_counted_diagnostic(self, name: str, entry_text: str, limit: int) -> None:
        self._metrics.diagnostics[name] += 1
        self._poll_console_outputs()
        self._record_diagnostic_entry(entry_text, limit)
        self._update_metrics_view()

    def _record_diagnostic_entry(self, entry_text: str, limit: int) -> None:
        self._diagnostic_history.append(entry_text)
        if len(self._diagnostic_history) > limit:
            self._diagnostic_history.pop(0)
            if self._diagnostics_log is not None:
                # The oldest line has scrolled out of the history, so redraw the
                # whole view rather than appending the new line.
                self._diagnostics_log.clear()
                for line in self._diagnostic_history:
                    self._diagnostics_log.write(line)
                return
        if self._diagnostics_log is not None:
            self._diagnostics_log.write(entry_text)

    def _set_status(self, message: str, *, style: str = "") -> None:
        # Static.update parses a str as Textual markup, and these messages carry
        # exception text and the user's own setting values: "[/]" would close the
        # colour early and "[bold]" would vanish. A Text is shown literally.
        if self._status is not None:
            self._status.update(Text(message, style=style))

    def _update_metrics_view(self) -> None:
        if self._metrics_view is None:
            return
        snapshot: SeveritySnapshot | None = None
        try:
            snapshot = self._runtime.severity_snapshot()
        except (RuntimeError, AttributeError):  # pragma: no cover - runtime may be uninitialised
            snapshot = None
        self._metrics_view.update(Text("\n".join(self._metrics.format_lines(snapshot))))


def _create_setting_row_class(imports: _TextualImports) -> type[Any]:
    """Construct the Textual row widget that renders one :class:`SettingSpec`.

    Args:
        imports: Bundle of Textual types resolved at runtime.

    Returns:
        Subclass of ``textual.containers.Horizontal`` taking a ``SettingSpec``.

    """
    Horizontal = imports.Horizontal
    Select = imports.Select
    Input = imports.Input
    Static = imports.Static

    class SettingRow(Horizontal):
        """Renders a single configuration row and exposes its value."""

        def __init__(self, spec: SettingSpec) -> None:
            super().__init__(classes="setting-row")
            self.spec = spec
            self._input: Select[str] | Input
            choices = _CHOICE_FIELDS.get(spec.key)
            if choices is not None:
                default_value = _normalise_choice_default(spec, options=choices)
                self._input = Select(options=choices, value=default_value, classes="setting-select", compact=True)
                self._input.styles.width = 32
            else:
                self._input = Input(value=spec.default or "", classes="setting-input")
            self._input.tooltip = spec.help

        def compose(self) -> ComposeResult:
            label = Static(self.spec.label, classes="setting-label")
            label.tooltip = self.spec.help
            yield label
            yield self._input

        def value(self) -> str:
            # A Select with nothing chosen holds Select.BLANK, which reads as no value.
            raw_value: object = self._input.value
            return raw_value.strip() if isinstance(raw_value, str) else ""

    return SettingRow


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
    setting_row_cls = _create_setting_row_class(imports)

    class StressTestApp(_StressTestAppBehaviour, imports.App):
        CSS = APP_CSS
        BINDINGS = [("q", "quit", "Quit"), ("ctrl+c", "quit", "Quit")]

        def __init__(self) -> None:
            super().__init__()
            self._init_stress_state(imports=imports, log=log, runtime=runtime, setting_row_cls=setting_row_cls)

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
