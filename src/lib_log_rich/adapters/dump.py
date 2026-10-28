"""Dump adapter supporting text, JSON, and HTML exports.

Outputs
-------
* Text with optional ANSI colouring.
* JSON arrays for structured analysis.
* HTML tables mirroring core metadata.
* HTML text rendered via Rich styles for theme-aware sharing.

Purpose
-------
Turn ring buffer snapshots into shareable artefacts without depending on
external sinks.

Contents
--------
* :class:`DumpAdapter` - implementation of :class:`DumpPort`.

System Role
-----------
Feeds operational tooling (CLI, logdemo) and diagnostics when operators request
text/JSON/HTML dumps.

Alignment Notes
---------------
Output formats and templates align with the behaviour described in
``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

import html
from functools import lru_cache
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console
from rich.text import Text

from lib_log_rich.application.ports.dump import DumpPort
from lib_log_rich.domain import json_codec
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.enums import ConsoleTheme, FormatPreset
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

from ._formatting import build_format_payload, format_process_chain, indent_traceback, traceback_text
from ._schemas import LogEventPayload

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from lib_log_rich.domain.events import LogEvent

# Invisible marker character for ANSI style extraction
# Used as a placeholder to capture Rich's ANSI prefix/suffix sequences
_STYLE_EXTRACTION_MARKER: str = "\u0000"


def _create_rich_console_for_dump() -> Console:
    """Create Rich console configured for dump rendering with truecolor support."""
    return Console(color_system="truecolor", force_terminal=True, legacy_windows=False)


def _create_style_wrapper(rich_console: Console, style: str) -> tuple[str, str]:
    """Generate ANSI prefix/suffix pair for a given Rich style string.

    Parameters
    ----------
    rich_console:
        Rich console instance for rendering.
    style:
        Rich style string to convert.

    Returns
    -------
    tuple[str, str]
        (prefix, suffix) ANSI sequences to wrap text.

    """
    marker = _STYLE_EXTRACTION_MARKER
    with rich_console.capture() as capture:
        rich_console.print(Text(marker, style=style), end="")
    styled_marker = capture.get()
    prefix, marker_found, suffix = styled_marker.partition(marker)
    if not marker_found:
        return ("", "")
    return (prefix, suffix)


def _event_theme(event: LogEvent) -> ConsoleTheme | None:
    """Return the palette an event's ``theme`` extra names, if it names one.

    Example:
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.events import LogEvent
        >>> ctx = LogContext(service='svc', environment='env', job_id='job')
        >>> def make(theme):
        ...     return LogEvent('1', datetime.now(timezone.utc), 'x', LogLevel.INFO, 'm', ctx, extra={'theme': theme})
        >>> _event_theme(make(' Neon ')) is ConsoleTheme.NEON
        True
        >>> _event_theme(make('nope')) is None, _event_theme(make(7)) is None
        (True, True)

    """
    value = event.extra.get("theme")
    if not isinstance(value, str):
        return None
    try:
        return ConsoleTheme.from_str(value)
    except ValueError:
        # Extras are free-form caller data, not configuration: a name that is
        # no palette just leaves the dump's own theme in charge, because one
        # odd event must never fail the whole dump.
        return None


def _event_palette(event: LogEvent, theme_styles: Mapping[str, str]) -> Mapping[str, str]:
    """Return the palette the event's own ``theme`` extra names, else ``theme_styles``."""
    event_theme = _event_theme(event)
    return theme_styles if event_theme is None else CONSOLE_STYLE_THEMES[event_theme]


def _resolve_event_style(
    event: LogEvent,
    *,
    resolved_styles: dict[str, str],
    theme_styles: Mapping[str, str],
) -> str | None:
    """Determine the Rich style string for an event based on level and theme.

    Parameters
    ----------
    event:
        Log event to style.
    resolved_styles:
        Explicit style overrides keyed by level name.
    theme_styles:
        Theme palette keyed by level name.

    Returns
    -------
    str | None:
        Rich style string, or None if no style found.

    """
    # Check explicit style overrides first
    style_name = resolved_styles.get(event.level.name)
    if style_name is not None:
        return style_name

    return _event_palette(event, theme_styles).get(event.level.name)


def _apply_fallback_ansi_color(line: str, level: LogLevel) -> str:
    """Apply simple ANSI color to line based on log level.

    Parameters
    ----------
    line:
        Text line to colorize.
    level:
        Log level determining color choice.

    Returns
    -------
    str:
        Line wrapped in ANSI color codes if level has a fallback color.

    """
    fallback_colours = {
        LogLevel.DEBUG: "\u001b[36m",  # cyan
        LogLevel.INFO: "\u001b[32m",  # green
        LogLevel.WARNING: "\u001b[33m",  # yellow
        LogLevel.ERROR: "\u001b[31m",  # red
        LogLevel.CRITICAL: "\u001b[35m",  # magenta
    }
    reset = "\u001b[0m"

    colour = fallback_colours.get(level)
    if colour:
        return "\n".join(f"{colour}{part}{reset}" for part in line.split("\n"))
    return line


def _html_traceback(event: LogEvent) -> str:
    """Return the event's traceback as an escaped ``<pre>`` block, or ``""``.

    Examples:
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.events import LogEvent
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> when = datetime(2025, 9, 30, tzinfo=timezone.utc)
        >>> _html_traceback(LogEvent('id', when, 'svc', LogLevel.ERROR, 'm', ctx, exc_info='in <module>'))
        '<pre>in &lt;module&gt;</pre>'
        >>> _html_traceback(LogEvent('id', when, 'svc', LogLevel.ERROR, 'm', ctx))
        ''
    """
    trace = traceback_text(event)
    return f"<pre>{html.escape(trace)}</pre>" if trace else ""


def _normalise_styles(styles: Mapping[str, str] | None) -> dict[str, str]:
    """Convert mixed keys to uppercase level names for palette lookups.

    Parameters
    ----------
    styles:
        Mapping keyed by :class:`LogLevel` or strings.

    Returns
    -------
    dict[str, str]
        Dictionary keyed by uppercase strings.

    Examples
    --------
    >>> _normalise_styles({LogLevel.INFO: 'green', 'error': 'red'})
    {'INFO': 'green', 'ERROR': 'red'}

    """
    if not styles:
        return {}
    normalised: dict[str, str] = {}
    for key, value in styles.items():
        if isinstance(key, LogLevel):
            normalised[key.name] = value
        else:
            norm_key = str(key).strip().upper()
            if norm_key:
                normalised[norm_key] = value
    return normalised


def _resolve_theme_styles(theme: ConsoleTheme | None) -> Mapping[str, str]:
    """Return the palette for ``theme``, keyed by uppercase level name.

    Args:
        theme: Palette to use; ``None`` for none.

    Returns:
        The palette, or an empty mapping when ``theme`` is ``None``.

    Raises:
        ValueError: If ``theme`` names no palette.

    Examples:
        >>> _resolve_theme_styles(ConsoleTheme.CLASSIC)['INFO']
        'cyan'
        >>> _resolve_theme_styles(None)
        {}
        >>> _resolve_theme_styles('nope')  # an untyped caller's plain string
        Traceback (most recent call last):
        ...
        ValueError: Unknown console theme: 'nope'

    """
    if theme is None:
        return {}
    try:
        return CONSOLE_STYLE_THEMES[theme]
    except KeyError as exc:
        # str() rather than .value, as in _resolve_preset: an untyped caller's
        # plain string then gets this ValueError instead of an AttributeError.
        raise ValueError(f"Unknown console theme: {str(theme)!r}") from exc


#: Fallback colour styles used when neither theme nor explicit styles provide mappings.
_FALLBACK_HTML_STYLES: dict[LogLevel, str] = {
    LogLevel.DEBUG: "cyan",
    LogLevel.INFO: "green",
    LogLevel.WARNING: "yellow",
    LogLevel.ERROR: "red",
    LogLevel.CRITICAL: "magenta",
}

#: Text dump template per preset. ``SHORT_LOC_ICON`` is absent on purpose: its
#: icon needs a terminal, so a text dump refuses it rather than guessing a glyph.
_TEXT_PRESETS: dict[FormatPreset, str] = {
    FormatPreset.FULL: "{timestamp} {LEVEL:<8} {logger_name} {event_id} {message}{context_fields}",
    FormatPreset.SHORT: "{hh}:{mm}:{ss}|{level_code}|{logger_name}: {message}",
    FormatPreset.FULL_LOC: "{timestamp_loc} {LEVEL:<8} {logger_name} {event_id} {message}{context_fields}",
    FormatPreset.SHORT_LOC: "{hh_loc}:{mm_loc}:{ss_loc}|{level_code}|{logger_name}: {message}",
}


@lru_cache(maxsize=8)
def _resolve_preset(preset: FormatPreset) -> str:
    """Return the template string associated with a named preset.

    Parameters
    ----------
    preset:
        Preset such as :attr:`FormatPreset.FULL` or :attr:`FormatPreset.SHORT`.

    Returns
    -------
    str
        Format string ready for :func:`str.format`.

    Raises
    ------
    ValueError
        If ``preset`` has no text dump template (``SHORT_LOC_ICON``).

    Examples
    --------
    >>> _resolve_preset(FormatPreset.FULL).startswith('{timestamp}')
    True
    >>> _resolve_preset(FormatPreset.SHORT_LOC_ICON)
    Traceback (most recent call last):
    ...
    ValueError: Unknown text dump preset: 'short_loc_icon'

    """
    try:
        return _TEXT_PRESETS[preset]
    except KeyError as exc:
        # str() rather than .value: an untyped caller's plain string then gets
        # this ValueError instead of an AttributeError.
        raise ValueError(f"Unknown text dump preset: {str(preset)!r}") from exc


class DumpAdapter(DumpPort):
    """Render ring buffer snapshots into text, JSON, or HTML."""

    @staticmethod
    def _filter_by_level(events: Sequence[LogEvent], min_level: LogLevel | None) -> list[LogEvent]:
        """Filter events by minimum log level."""
        if min_level is None:
            return list(events)
        return [event for event in events if event.level >= min_level]

    @staticmethod
    def _resolve_template(format_preset: FormatPreset | None, format_template: str | None) -> str | None:
        """Return the explicit template, else the preset's template, else ``None``."""
        if format_preset is not None and not format_template:
            return _resolve_preset(format_preset)
        return format_template

    def _render_by_format(
        self,
        events: Sequence[LogEvent],
        *,
        dump_format: DumpFormat,
        template: str | None,
        colorize: bool,
        theme: ConsoleTheme | None,
        console_styles: Mapping[str, str] | None,
    ) -> str:
        """Dispatch to appropriate renderer based on dump_format."""
        if dump_format is DumpFormat.TEXT:
            return self._render_text(events, template=template, colorize=colorize, theme=theme, console_styles=console_styles)
        if dump_format is DumpFormat.JSON:
            return self._render_json(events)
        if dump_format is DumpFormat.HTML_TABLE:
            return self._render_html_table(events)
        if dump_format is DumpFormat.HTML_TXT:
            return self._render_html_text(events, template=template, colorize=colorize, theme=theme, console_styles=console_styles)
        raise ValueError(f"Unsupported dump format: {dump_format.value}")

    @staticmethod
    def _write_to_path(path: Path, content: str) -> None:
        """Write content to filesystem path, creating parent directories if needed."""
        parent = path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def dump(
        self,
        events: Sequence[LogEvent],
        *,
        dump_format: DumpFormat,
        path: Path | None = None,
        min_level: LogLevel | None = None,
        format_preset: FormatPreset | None = None,
        format_template: str | None = None,
        theme: ConsoleTheme | None = None,
        console_styles: Mapping[str, str] | None = None,
        colorize: bool = False,
    ) -> str:
        """Render events according to dump_format; the use case has already applied any filter."""
        filtered = self._filter_by_level(events, min_level)
        template = self._resolve_template(format_preset, format_template)
        content = self._render_by_format(filtered, dump_format=dump_format, template=template, colorize=colorize, theme=theme, console_styles=console_styles)

        if path is not None:
            self._write_to_path(path, content)

        return content

    @staticmethod
    def _format_event_line(event: LogEvent, pattern: str) -> str:
        """Format a single event with the template, its traceback block indented under it."""
        data = build_format_payload(event).to_dict()
        try:
            line = pattern.format(**data)
        except KeyError as exc:
            raise ValueError(f"Unknown placeholder in text template: {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"Invalid format specification in template: {exc}") from exc
        trace = traceback_text(event)
        return f"{line}\n{indent_traceback(trace)}" if trace else line

    @staticmethod
    def _colorize_line(
        line: str,
        event: LogEvent,
        *,
        rich_console: Console,
        style_wrappers: dict[str, tuple[str, str]],
        resolved_styles: dict[str, str],
        theme_styles: Mapping[str, str],
    ) -> str:
        """Apply colorization to a line using Rich styles or ANSI fallback."""
        style_name = _resolve_event_style(event, resolved_styles=resolved_styles, theme_styles=theme_styles)

        if style_name:
            # Use cached wrapper or create new one
            if style_name not in style_wrappers:
                style_wrappers[style_name] = _create_style_wrapper(rich_console, style_name)
            start, end = style_wrappers[style_name]
            if not (start and end):
                return line
            # Each line of a traceback block gets its own open and reset, so a
            # grep or a pager showing one line never leaks the colour onward.
            return "\n".join(f"{start}{part}{end}" for part in line.split("\n"))

        return _apply_fallback_ansi_color(line, event.level)

    @staticmethod
    def _render_text(
        events: Sequence[LogEvent],
        *,
        template: str | None,
        colorize: bool,
        theme: ConsoleTheme | None = None,
        console_styles: Mapping[str, str] | None = None,
    ) -> str:
        """Render text dumps honouring templates and optional colour."""
        if not events:
            return ""

        pattern = template or "{timestamp} {LEVEL:<8} {logger_name} {event_id} {message}"
        resolved_styles = _normalise_styles(console_styles)
        theme_styles = _resolve_theme_styles(theme)

        rich_console = _create_rich_console_for_dump() if colorize else None
        style_wrappers: dict[str, tuple[str, str]] = {}

        lines: list[str] = []
        for event in events:
            line = DumpAdapter._format_event_line(event, pattern)

            if colorize and rich_console is not None:
                line = DumpAdapter._colorize_line(
                    line, event, rich_console=rich_console, style_wrappers=style_wrappers, resolved_styles=resolved_styles, theme_styles=theme_styles
                )

            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _resolve_html_style(
        event: LogEvent,
        *,
        colorize: bool,
        resolved_styles: dict[str, str],
        theme_styles: Mapping[str, str],
    ) -> str | None:
        """Resolve Rich style for HTML rendering."""
        if not colorize:
            return None
        # Unlike the text path, an empty explicit style falls through to the palette here.
        style_name = resolved_styles.get(event.level.name)
        if style_name:
            return style_name
        return _event_palette(event, theme_styles).get(event.level.name) or _FALLBACK_HTML_STYLES.get(event.level)

    @staticmethod
    def _create_html_console() -> Console:
        """Create Rich console configured for HTML export.

        ``soft_wrap`` keeps each event on one line: the StringIO has no width,
        and Rich's default of 80 columns would split long events inside the
        ``<pre>`` block.
        """
        return Console(
            file=StringIO(),
            record=True,
            force_terminal=True,
            legacy_windows=False,
            color_system="truecolor",
            soft_wrap=True,
        )

    @staticmethod
    def _render_html_text(
        events: Sequence[LogEvent],
        *,
        template: str | None,
        colorize: bool,
        theme: ConsoleTheme | None = None,
        console_styles: Mapping[str, str] | None = None,
    ) -> str:
        """Render HTML preformatted text, optionally colourised via Rich styles."""
        if not events:
            return "<html><head><title>lib_log_rich dump</title></head><body></body></html>"

        pattern = template or "{timestamp} {LEVEL:<8} {logger_name} {event_id} {message}"
        resolved_styles = _normalise_styles(console_styles)
        theme_styles = _resolve_theme_styles(theme)
        console = DumpAdapter._create_html_console()

        for event in events:
            line = DumpAdapter._format_event_line(event, pattern)
            style_name = DumpAdapter._resolve_html_style(event, colorize=colorize, resolved_styles=resolved_styles, theme_styles=theme_styles)

            console.print(
                Text(line, style=style_name if colorize and style_name else ""),
                markup=False,
                highlight=False,
            )

        html_output = console.export_html(theme=None, clear=False)
        console.clear()
        return html_output

    @staticmethod
    def _render_json(events: Sequence[LogEvent]) -> str:
        """Serialise events into a deterministic JSON array with rich metadata.

        Parameters
        ----------
        events:
            Sequence of events to serialise.

        Returns
        -------
        str
            JSON array string containing Pydantic-validated payloads.

        Examples
        --------
        >>> DumpAdapter._render_json([])
        '[]'

        """
        payload = [LogEventPayload.from_event(event).model_dump(mode="json") for event in events]
        return json_codec.dumps(payload, indent=True)

    @staticmethod
    def _build_html_table_row(event: LogEvent) -> str:
        """Build a single HTML table row for an event.

        Uses direct attribute access on LogContext dataclass.
        """
        context = event.context
        chain_str = format_process_chain(context.process_id_chain)
        return (
            "<tr>"
            f"<td>{html.escape(event.timestamp.isoformat())}</td>"
            f"<td>{html.escape(event.level.severity.upper())}</td>"
            f"<td>{html.escape(event.logger_name)}</td>"
            f"<td>{html.escape(event.message)}{_html_traceback(event)}</td>"
            f"<td>{html.escape(str(context.user_name or ''))}</td>"
            f"<td>{html.escape(str(context.hostname or ''))}</td>"
            f"<td>{html.escape(str(context.process_id or ''))}</td>"
            f"<td>{html.escape(chain_str)}</td>"
            "</tr>"
        )

    @staticmethod
    def _render_html_table(events: Sequence[LogEvent]) -> str:
        """Generate a minimal HTML table for quick sharing.

        Examples
        --------
        >>> DumpAdapter._render_html_table([]).startswith('<html>')
        True

        """
        rows = [DumpAdapter._build_html_table_row(event) for event in events]
        table = "".join(rows)
        return (
            "<html><head><title>lib_log_rich dump</title></head><body>"
            "<table>"
            "<thead><tr><th>Timestamp</th><th>Level</th><th>Logger</th><th>Message</th>"
            "<th>User</th><th>Hostname</th><th>PID</th><th>PID Chain</th></tr></thead>"
            f"<tbody>{table}</tbody>"
            "</table>"
            "</body></html>"
        )


__all__ = ["DumpAdapter"]
