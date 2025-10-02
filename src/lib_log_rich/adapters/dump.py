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
import json
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from typing import cast
from io import StringIO
from pathlib import Path

from lib_log_rich.application.ports.dump import DumpPort
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.dump_filter import DumpFilter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel

from rich.console import Console
from rich.text import Text
from ._formatting import build_format_payload


@lru_cache(maxsize=None)
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
        """Render ``events`` according to ``dump_format`` and optional filters.

        Why
        ---
        Provides a single entry point for CLI commands and the public ``dump``
        helper to materialise ring-buffer contents.

        Parameters
        ----------
        events:
            Ordered sequence of :class:`LogEvent` objects.
        dump_format:
            Target format (text/json/html).
        path:
            Optional filesystem path for persistence.
        min_level:
            Minimum :class:`LogLevel` to include.
        format_preset, format_template, text_template:
            Template configuration mirroring CLI options; ``text_template`` is
            retained for backwards compatibility.
        theme:
            Default theme used for coloured dumps.
        console_styles:
            Explicit style overrides per level.
        filters:
            Optional dump filter passed through for diagnostics; the adapter expects pre-filtered events.
        colorize:
            When ``True`` emit ANSI sequences for text dumps.

        Returns
        -------
        str
            Rendered dump content (text, JSON, or HTML).

        Side Effects
        ------------
        Writes the rendered payload to ``path`` when provided.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
        >>> DumpAdapter().dump([event], dump_format=DumpFormat.JSON).startswith('[')
        True
        """
        filtered = list(events)
        _ = filters  # keep signature parity; filtering happens in the use case
        if min_level is not None:
            filtered = [event for event in filtered if event.level.value >= min_level.value]

        template = format_template or text_template
        if format_preset and not template:
            template = _resolve_preset(format_preset)

        if dump_format is DumpFormat.TEXT:
            content = self._render_text(
                filtered,
                template=template,
                colorize=colorize,
                theme=theme,
                console_styles=console_styles,
            )
        elif dump_format is DumpFormat.JSON:
            content = self._render_json(filtered)
        elif dump_format is DumpFormat.HTML_TABLE:
            content = self._render_html_table(filtered)
        elif dump_format is DumpFormat.HTML_TXT:
            content = self._render_html_text(
                filtered,
                template=template,
                colorize=colorize,
                theme=theme,
                console_styles=console_styles,
            )
        else:  # pragma: no cover - exhaustiveness guard
            raise ValueError(f"Unsupported dump format: {dump_format}")

        if path is not None:
            parent = path.parent
            if not parent.exists():
                parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return content

    @staticmethod
    def _render_text(
        events: Sequence[LogEvent],
        *,
        template: str | None,
        colorize: bool,
        theme: str | None = None,
        console_styles: Mapping[str, str] | None = None,
    ) -> str:
        """Render text dumps honouring templates and optional colour.

        Parameters
        ----------
        events:
            Sequence of events to render.
        template:
            Optional ``str.format`` template; when ``None`` uses the default.
        colorize:
            Emit ANSI sequences when ``True``.
        theme:
            Theme name providing fallback styles.
        console_styles:
            Explicit style overrides (strings or :class:`LogLevel` keys).

        Returns
        -------
        str
            Rendered multi-line text payload.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
        >>> DumpAdapter._render_text([event], template='{message}', colorize=False)
        'msg'
        """
        if not events:
            return ""

        pattern = template or "{timestamp} {LEVEL:<8} {logger_name} {event_id} {message}"

        fallback_colours = {
            LogLevel.DEBUG: "\u001b[36m",  # cyan
            LogLevel.INFO: "\u001b[32m",  # green
            LogLevel.WARNING: "\u001b[33m",  # yellow
            LogLevel.ERROR: "\u001b[31m",  # red
            LogLevel.CRITICAL: "\u001b[35m",  # magenta
        }
        reset = "\u001b[0m"

        resolved_styles = _normalise_styles(console_styles)
        theme_styles = _resolve_theme_styles(theme)

        rich_console: Console | None = Console(color_system="truecolor", force_terminal=True, legacy_windows=False) if colorize else None

        lines: list[str] = []
        for event in events:
            data = build_format_payload(event)
            try:
                line = pattern.format(**data)
            except KeyError as exc:  # pragma: no cover - invalid template
                raise ValueError(f"Unknown placeholder in text template: {exc}") from exc
            except ValueError as exc:  # pragma: no cover - invalid specifier
                raise ValueError(f"Invalid format specification in template: {exc}") from exc

            if colorize:
                style_name: str | None = resolved_styles.get(event.level.name)

                if style_name is None:
                    event_theme = None
                    try:
                        event_theme = event.extra.get("theme")
                    except AttributeError:
                        event_theme = None
                    if isinstance(event_theme, str):
                        palette = _resolve_theme_styles(event_theme) or theme_styles
                    else:
                        palette = theme_styles
                    if palette:
                        style_name = palette.get(event.level.name)

                if style_name and rich_console is not None:
                    with rich_console.capture() as capture:
                        rich_console.print(Text(line, style=style_name), end="")
                    styled_line = capture.get().rstrip("\n")
                    lines.append(styled_line)
                    continue

                colour = fallback_colours.get(event.level)
                if colour:
                    line = f"{colour}{line}{reset}"

            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _render_html_text(
        events: Sequence[LogEvent],
        *,
        template: str | None,
        colorize: bool,
        theme: str | None = None,
        console_styles: Mapping[str, str] | None = None,
    ) -> str:
        """Render HTML preformatted text, optionally colourised via Rich styles.

        Parameters
        ----------
        events:
            Sequence of events to render.
        template:
            Optional text template for each row.
        colorize:
            When ``True`` apply Rich styles for coloured HTML output.
        theme:
            Theme name considered when styles are missing.
        console_styles:
            Explicit style overrides by level.

        Returns
        -------
        str
            Full HTML document containing the formatted events.
        """
        if not events:
            return "<html><head><title>lib_log_rich dump</title></head><body></body></html>"

        pattern = template or "{timestamp} {LEVEL:<8} {logger_name} {event_id} {message}"
        resolved_styles = _normalise_styles(console_styles)
        theme_styles = _resolve_theme_styles(theme)

        buffer = StringIO()
        console = Console(
            file=buffer,
            record=True,
            force_terminal=True,
            legacy_windows=False,
            color_system="truecolor",
        )

        for event in events:
            data = build_format_payload(event)
            try:
                line = pattern.format(**data)
            except KeyError as exc:  # pragma: no cover - invalid template
                raise ValueError(f"Unknown placeholder in text template: {exc}") from exc
            except ValueError as exc:  # pragma: no cover - invalid specifier
                raise ValueError(f"Invalid format specification in template: {exc}") from exc

            style_name: str | None = None
            if colorize:
                style_name = resolved_styles.get(event.level.name)
                if style_name is None:
                    event_theme = None
                    try:
                        event_theme = event.extra.get("theme")
                    except AttributeError:
                        event_theme = None
                    if isinstance(event_theme, str):
                        palette = _resolve_theme_styles(event_theme) or theme_styles
                    else:
                        palette = theme_styles
                    if palette:
                        style_name = palette.get(event.level.name)
                if style_name is None:
                    style_name = _FALLBACK_HTML_STYLES.get(event.level)

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
        """Serialise events into a deterministic JSON array.

        Parameters
        ----------
        events:
            Sequence of events to serialise.

        Returns
        -------
        str
            JSON array string sorted by keys for deterministic output.

        Examples
        --------
        >>> DumpAdapter._render_json([])
        '[]'
        """
        payload = [event.to_dict() for event in events]
        return json.dumps(payload, indent=2, sort_keys=True)

    @staticmethod
    def _render_html_table(events: Sequence[LogEvent]) -> str:
        """Generate a minimal HTML table for quick sharing.

        Parameters
        ----------
        events:
            Sequence of events to tabulate.

        Returns
        -------
        str
            HTML document containing a table with key metadata columns.

        Examples
        --------
        >>> DumpAdapter._render_html_table([]).startswith('<html>')
        True
        """
        rows: list[str] = []
        for event in events:
            context_data = event.context.to_dict(include_none=True)
            chain_raw = context_data.get("process_id_chain")
            if isinstance(chain_raw, (list, tuple)):
                chain_iter = cast(Iterable[object], chain_raw)
                chain_parts = [str(part) for part in chain_iter]
            elif chain_raw:
                chain_parts = [str(chain_raw)]
            else:
                chain_parts = []
            chain_str = ">".join(chain_parts) if chain_parts else ""
            rows.append(
                "<tr>"
                f"<td>{html.escape(event.timestamp.isoformat())}</td>"
                f"<td>{html.escape(event.level.severity.upper())}</td>"
                f"<td>{html.escape(event.logger_name)}</td>"
                f"<td>{html.escape(event.message)}</td>"
                f"<td>{html.escape(str(context_data.get('user_name') or ''))}</td>"
                f"<td>{html.escape(str(context_data.get('hostname') or ''))}</td>"
                f"<td>{html.escape(str(context_data.get('process_id') or ''))}</td>"
                f"<td>{html.escape(chain_str)}</td>"
                "</tr>"
            )
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
