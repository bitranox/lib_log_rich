"""Rich-powered console adapter implementing :class:`ConsolePort`.

Purpose
-------
Bridge the application layer with Rich so console output respects the styling
rules captured in ``concept_architecture.md``.

Contents
--------
* :data:`_STYLE_MAP` - default level-to-style mapping.
* :class:`RichConsoleAdapter` - adapter constructed by :func:`lib_log_rich.init`.

System Role
-----------
Primary human-facing sink; honours runtime overrides and environment variables
for colour control.

Alignment Notes
---------------
Colour handling and formatting mirror the usage documented in
``docs/systemdesign/module_reference.md`` and ``CONSOLESTYLES.md``.
"""

from __future__ import annotations

from typing import Mapping

from rich.console import Console

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel

from .._formatting import build_format_payload


_STYLE_MAP: Mapping[LogLevel, str] = {
    LogLevel.DEBUG: "dim",
    LogLevel.INFO: "cyan",
    LogLevel.WARNING: "yellow",
    LogLevel.ERROR: "red",
    LogLevel.CRITICAL: "bold red",
}

#: Default Rich styles keyed by :class:`LogLevel` severity.


_CONSOLE_PRESETS: dict[str, str] = {
    "full": "{timestamp_trimmed_naive} {level_icon} {LEVEL:>8} {logger_name} — {message}{context_fields}",
    "short": "{hh}:{mm}:{ss}|{level_code}|{logger_name}: {message}",
    "full_loc": "{timestamp_trimmed_naive_loc} {level_icon} {LEVEL:>8} {logger_name} — {message}{context_fields}",
    "short_loc": "{hh_loc}:{mm_loc}:{ss_loc}|{level_code}|{logger_name}: {message}",
}


class RichConsoleAdapter(ConsolePort):
    """Render log events using Rich formatting with theme overrides."""

    def __init__(
        self,
        *,
        console: Console | None = None,
        force_color: bool = False,
        no_color: bool = False,
        styles: Mapping[str, str] | None = None,
        format_preset: str | None = None,
        format_template: str | None = None,
    ) -> None:
        """Configure the console adapter with colour and style overrides.

        Parameters
        ----------
        console:
            Optional pre-configured Rich console instance.
        force_color:
            Force ANSI colour even when Rich would disable it.
        no_color:
            Disable colour regardless of terminal capabilities.
        styles:
            Mapping of levels to Rich style strings overriding defaults.
        format_preset:
            Named preset from :data:`_CONSOLE_PRESETS`.
        format_template:
            Custom ``str.format`` template overriding presets.
        """
        if console is not None:
            self._console = console
        else:
            self._console = Console(force_terminal=force_color, no_color=no_color)
        self._force_color = force_color
        self._no_color = no_color
        if styles:
            merged = dict(_STYLE_MAP)
            for key, value in styles.items():
                level = LogLevel.from_name(key)
                merged[level] = value
            self._style_map = merged
        else:
            self._style_map = dict(_STYLE_MAP)
        self._template, self._template_source = _resolve_template(format_preset, format_template)

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        """Print ``event`` using Rich with optional colour.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from io import StringIO
        >>> from lib_log_rich.domain.context import LogContext
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
        >>> console = Console(file=StringIO(), record=True)
        >>> adapter = RichConsoleAdapter(console=console)
        >>> adapter.emit(event, colorize=False)
        >>> 'msg' in console.export_text()
        True
        """
        style = self._style_map.get(event.level, "") if colorize and not self._no_color else ""
        line = self._format_line(event)
        self._console.print(line, style=style, highlight=False)

    def _format_line(self, event: LogEvent) -> str:
        """Format ``event`` using the configured template with fallbacks.

        Parameters
        ----------
        event:
            Log event to render.

        Returns
        -------
        str
            Rendered line ready for Rich printing.

        Raises
        ------
        ValueError
            When both the custom template and fallback preset fail to render.
        """
        payload = build_format_payload(event)
        template = self._template
        try:
            return template.format(**payload)
        except Exception:
            if self._template_source != "full":
                fallback = _CONSOLE_PRESETS["full"]
                try:
                    return fallback.format(**payload)
                except Exception as exc:  # pragma: no cover - defensive
                    raise ValueError("Console format template failed to render") from exc
            raise


def _resolve_template(format_preset: str | None, format_template: str | None) -> tuple[str, str]:
    """Select the console template and track its origin.

    Parameters
    ----------
    format_preset:
        Named preset to load when ``format_template`` is ``None``.
    format_template:
        Custom template string overriding presets.

    Returns
    -------
    tuple[str, str]
        ``(template, source)`` where ``source`` is either ``"custom"`` or the
        preset key.

    Raises
    ------
    ValueError
        If the requested preset does not exist.

    Examples
    --------
    >>> _resolve_template('full', None)[1]
    'full'
    >>> _resolve_template(None, '{message}')[1]
    'custom'
    """
    if format_template:
        return format_template, "custom"
    preset = (format_preset or "full").lower()
    try:
        return _CONSOLE_PRESETS[preset], preset
    except KeyError as exc:
        raise ValueError(f"Unknown console format preset: {format_preset!r}") from exc


__all__ = ["RichConsoleAdapter"]
