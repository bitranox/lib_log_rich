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
from collections.abc import Iterable, Mapping, Sequence
from functools import cache, lru_cache
from io import StringIO
from pathlib import Path
from typing import Any, cast

import orjson
from rich.console import Console
from rich.text import Text

from lib_log_rich.application.ports.dump import DumpPort
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.dump_filter import DumpFilter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel

from ._formatting import build_format_payload
from ._schemas import LogEventPayload


@cache
def _load_console_themes() -> dict[str, dict[str, str]]:
    """Load console themes from the domain palette module (cached).

    Returns
    -------
    dict[str, dict[str, str]]
        Mapping of theme names to level->style dictionaries (uppercase levels).

    Examples
    --------
    >>> isinstance(_load_console_themes(), dict)
    True

    """
    try:  # pragma: no cover - defensive import guard
        from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES
    except ImportError:  # pragma: no cover - happens during early bootstrap
        return {}
    return {name.lower(): {level.upper(): style for level, style in palette.items()} for name, palette in CONSOLE_STYLE_THEMES.items()}


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
    marker = "\u0000"
    with rich_console.capture() as capture:
        rich_console.print(Text(marker, style=style), end="")
    styled_marker = capture.get()
    prefix, marker_found, suffix = styled_marker.partition(marker)
    if not marker_found:
        return ("", "")
    return (prefix, suffix)


def _resolve_event_style(
    event: LogEvent,
    *,
    resolved_styles: dict[str, str],
    theme_styles: dict[str, str],
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

    # Check event-specific theme override
    event_theme = None
    try:
        event_theme = event.extra.get("theme")
    except AttributeError:
        event_theme = None

    # Resolve palette (event theme > default theme)
    if isinstance(event_theme, str):
        palette = _resolve_theme_styles(event_theme) or theme_styles
    else:
        palette = theme_styles

    # Lookup level in palette
    if palette:
        return palette.get(event.level.name)

    return None


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
        return f"{colour}{line}{reset}"
    return line


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


@lru_cache(maxsize=8)
def _resolve_theme_styles(theme: str | None) -> dict[str, str]:
    """Fetch style overrides for the selected theme (if any).

    Parameters
    ----------
    theme:
        Theme name; case-insensitive.

    Returns
    -------
    dict[str, str]
        Palette mapping or empty dict when theme is ``None`` or unknown.

    Examples
    --------
    >>> isinstance(_resolve_theme_styles(None), dict)
    True

    """
    if not theme:
        return {}
    palette = _load_console_themes().get(theme.strip().lower())
    return dict(palette) if palette else {}


#: Fallback colour styles used when neither theme nor explicit styles provide mappings.
_FALLBACK_HTML_STYLES: dict[LogLevel, str] = {
    LogLevel.DEBUG: "cyan",
    LogLevel.INFO: "green",
    LogLevel.WARNING: "yellow",
    LogLevel.ERROR: "red",
    LogLevel.CRITICAL: "magenta",
}

#: Named text presets mirrored in CLI documentation for predictable dumps.
_TEXT_PRESETS: dict[str, str] = {
    "full": "{timestamp} {LEVEL:<8} {logger_name} {event_id} {message}{context_fields}",
    "short": "{hh}:{mm}:{ss}|{level_code}|{logger_name}: {message}",
    "full_loc": "{timestamp_loc} {LEVEL:<8} {logger_name} {event_id} {message}{context_fields}",
    "short_loc": "{hh_loc}:{mm_loc}:{ss_loc}|{level_code}|{logger_name}: {message}",
}


@lru_cache(maxsize=8)
def _resolve_preset(preset: str) -> str:
    """Return the template string associated with a named preset.

    Parameters
    ----------
    preset:
        Preset name such as ``"full"`` or ``"short"`` (case-insensitive).

    Returns
    -------
    str
        Format string ready for :func:`str.format`.

    Raises
    ------
    ValueError
        If ``preset`` is unknown.

    Examples
    --------
    >>> _resolve_preset('full').startswith('{timestamp}')
    True

    """
    key = preset.lower()
    try:
        return _TEXT_PRESETS[key]
    except KeyError as exc:
        raise ValueError(f"Unknown text dump preset: {preset!r}") from exc


class DumpAdapter(DumpPort):
    """Render ring buffer snapshots into text, JSON, or HTML."""

    @staticmethod
    def _filter_by_level(events: Sequence[LogEvent], min_level: LogLevel | None) -> list[LogEvent]:
        """Filter events by minimum log level."""
        if min_level is None:
            return list(events)
        return [event for event in events if event.level >= min_level]

    @staticmethod
    def _resolve_template(format_preset: str | None, format_template: str | None, text_template: str | None) -> str | None:
        """Resolve template from preset or explicit template (text_template for backwards compat)."""
        template = format_template or text_template
        if format_preset and not template:
            return _resolve_preset(format_preset)
        return template

    def _render_by_format(
        self,
        events: Sequence[LogEvent],
        dump_format: DumpFormat,
        template: str | None,
        colorize: bool,
        theme: str | None,
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
        raise ValueError(f"Unsupported dump format: {dump_format}")

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
        format_preset: str | None = None,
        format_template: str | None = None,
        text_template: str | None = None,
        theme: str | None = None,
        console_styles: Mapping[str, str] | None = None,
        filters: DumpFilter | None = None,
        colorize: bool = False,
    ) -> str:
        """Render events according to dump_format and optional filters."""
        _ = filters  # keep signature parity; filtering happens in the use case

        filtered = self._filter_by_level(events, min_level)
        template = self._resolve_template(format_preset, format_template, text_template)
        content = self._render_by_format(filtered, dump_format, template, colorize, theme, console_styles)

        if path is not None:
            self._write_to_path(path, content)

        return content

    @staticmethod
    def _format_event_line(event: LogEvent, pattern: str) -> str:
        """Format a single event using the given template pattern."""
        data = build_format_payload(event).to_dict()
        try:
            return pattern.format(**data)
        except KeyError as exc:
            raise ValueError(f"Unknown placeholder in text template: {exc}") from exc
        except ValueError as exc:
            raise ValueError(f"Invalid format specification in template: {exc}") from exc

    @staticmethod
    def _colorize_line(
        line: str,
        event: LogEvent,
        rich_console: Console,
        style_wrappers: dict[str, tuple[str, str]],
        resolved_styles: dict[str, str],
        theme_styles: dict[str, str],
    ) -> str:
        """Apply colorization to a line using Rich styles or ANSI fallback."""
        style_name = _resolve_event_style(event, resolved_styles=resolved_styles, theme_styles=theme_styles)

        if style_name:
            # Use cached wrapper or create new one
            if style_name not in style_wrappers:
                style_wrappers[style_name] = _create_style_wrapper(rich_console, style_name)
            start, end = style_wrappers[style_name]
            return f"{start}{line}{end}" if start and end else line

        return _apply_fallback_ansi_color(line, event.level)

    @staticmethod
    def _render_text(
        events: Sequence[LogEvent],
        *,
        template: str | None,
        colorize: bool,
        theme: str | None = None,
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
                line = DumpAdapter._colorize_line(line, event, rich_console, style_wrappers, resolved_styles, theme_styles)

            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _get_event_theme(event: LogEvent) -> str | None:
        """Extract theme from event extra if present."""
        try:
            theme = event.extra.get("theme")
            return theme if isinstance(theme, str) else None
        except AttributeError:
            return None

    @staticmethod
    def _resolve_palette(event_theme: str | None, theme_styles: dict[str, str]) -> dict[str, str]:
        """Resolve the palette to use based on event theme or default."""
        if event_theme:
            return _resolve_theme_styles(event_theme) or theme_styles
        return theme_styles

    @staticmethod
    def _resolve_html_style(
        event: LogEvent,
        colorize: bool,
        resolved_styles: dict[str, str],
        theme_styles: dict[str, str],
    ) -> str | None:
        """Resolve Rich style for HTML rendering."""
        if not colorize:
            return None
        # Check explicit style overrides first
        style_name = resolved_styles.get(event.level.name)
        if style_name:
            return style_name
        # Resolve palette from event or default theme
        palette = DumpAdapter._resolve_palette(DumpAdapter._get_event_theme(event), theme_styles)
        if palette:
            style_name = palette.get(event.level.name)
            if style_name:
                return style_name
        return _FALLBACK_HTML_STYLES.get(event.level)

    @staticmethod
    def _create_html_console() -> Console:
        """Create Rich console configured for HTML export."""
        return Console(
            file=StringIO(),
            record=True,
            force_terminal=True,
            legacy_windows=False,
            color_system="truecolor",
        )

    @staticmethod
    def _render_html_text(
        events: Sequence[LogEvent],
        *,
        template: str | None,
        colorize: bool,
        theme: str | None = None,
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
            style_name = DumpAdapter._resolve_html_style(event, colorize, resolved_styles, theme_styles)

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
        return orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()

    @staticmethod
    def _format_process_chain_html(chain_raw: Any) -> str:
        """Format process ID chain for HTML table display."""
        if isinstance(chain_raw, (list, tuple)):
            chain_iter = cast(Iterable[object], chain_raw)
            chain_parts = [str(part) for part in chain_iter]
        elif chain_raw:
            chain_parts = [str(chain_raw)]
        else:
            chain_parts = []
        return ">".join(chain_parts) if chain_parts else ""

    @staticmethod
    def _build_html_table_row(event: LogEvent) -> str:
        """Build a single HTML table row for an event.

        Uses direct attribute access on LogContext dataclass.
        """
        context = event.context
        chain_str = DumpAdapter._format_process_chain_html(context.process_id_chain)
        return (
            "<tr>"
            f"<td>{html.escape(event.timestamp.isoformat())}</td>"
            f"<td>{html.escape(event.level.severity.upper())}</td>"
            f"<td>{html.escape(event.logger_name)}</td>"
            f"<td>{html.escape(event.message)}</td>"
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
            "<thead><tr><th>Timestamp</th><th>Level</th><th>Logger</th><th>Message</th><th>User</th><th>Hostname</th><th>PID</th><th>PID Chain</th></tr></thead>"
            f"<tbody>{table}</tbody>"
            "</table>"
            "</body></html>"
        )


__all__ = ["DumpAdapter"]
