"""Rich-powered console adapter implementing :class:`ConsolePort`.

Purpose
-------
Bridge the application layer with Rich so console output respects the styling
rules captured in ``konzept_architecture.md``.

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

from typing import Mapping, MutableMapping

from rich.console import Console

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


_STYLE_MAP: Mapping[LogLevel, str] = {
    LogLevel.DEBUG: "dim",
    LogLevel.INFO: "cyan",
    LogLevel.WARNING: "yellow",
    LogLevel.ERROR: "red",
    LogLevel.CRITICAL: "bold red",
}

#: Default Rich styles keyed by :class:`LogLevel` severity.


class RichConsoleAdapter(ConsolePort):
    """Render log events using Rich formatting with theme overrides."""

    def __init__(
        self,
        *,
        console: Console | None = None,
        force_color: bool = False,
        no_color: bool = False,
        styles: MutableMapping[LogLevel | str, str] | None = None,
    ) -> None:
        """Configure the console adapter with colour and style overrides."""
        if console is not None:
            self._console = console
        else:
            self._console = Console(force_terminal=force_color, no_color=no_color)
        self._force_color = force_color
        self._no_color = no_color
        if styles:
            merged = dict(_STYLE_MAP)
            for key, value in styles.items():
                level = LogLevel.from_name(key) if isinstance(key, str) else key
                if isinstance(level, LogLevel):
                    merged[level] = value
            self._style_map = merged
        else:
            self._style_map = dict(_STYLE_MAP)

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

    @staticmethod
    def _format_line(event: LogEvent) -> str:
        """Return a human-friendly console line for ``event``.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
        >>> 'msg' in RichConsoleAdapter._format_line(event)
        True
        """
        context = event.context.to_dict()
        extra = dict(event.extra)
        merged = {key: value for key, value in {**context, **extra}.items() if value is not None and value != {}}
        context_str = "" if not merged else " " + " ".join(f"{key}={value}" for key, value in sorted(merged.items()))
        return f"{event.timestamp.isoformat()} {event.level.icon} {event.level.severity.upper():>8} {event.logger_name} — {event.message}{context_str}"


__all__ = ["RichConsoleAdapter"]
