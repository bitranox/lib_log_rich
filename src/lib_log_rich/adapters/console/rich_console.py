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

import io
import sys
from collections.abc import Mapping
from functools import lru_cache
from typing import IO, cast

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
    "full": "{timestamp_trimmed_naive} {level_icon}{LEVEL:>8} {logger_name} — {message}{context_fields}",
    "short": "\\[{hh}:{mm}:{ss}]\\[{level_code} {level_icon}]\\[{logger_name}]: {message}",
    "full_loc": "{timestamp_trimmed_naive_loc} {level_icon}{LEVEL:>8} {logger_name} — {message}{context_fields}",
    "short_loc": "\\[{hh_loc}:{mm_loc}:{ss_loc}]\\[{level_code}]: {message}",
    "short_loc_icon": "\\[{hh_loc}:{mm_loc}:{ss_loc}] {level_icon} {message}",
}
CONSOLE_PRESETS: tuple[str, ...] = tuple(_CONSOLE_PRESETS.keys())


def _default_preset() -> str:
    """Return platform-appropriate default console preset."""
    return "short_loc_icon" if sys.platform == "win32" else "short_loc"


def _flush_stream(stream: IO[str]) -> None:
    """Flush a single stream if it supports flushing."""
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def _stream_isatty(stream: IO[str]) -> bool:
    """Check if a single stream is a tty."""
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    return bool(isatty())


def _try_get_fileno(stream: IO[str]) -> int | None:
    """Try to get fileno from a single stream, returning None on failure."""
    fileno = getattr(stream, "fileno", None)
    if not callable(fileno):
        return None
    try:
        result = fileno()
    except (OSError, ValueError):  # pragma: no cover - depends on stream
        return None
    return result if isinstance(result, int) else None


class _ConsoleStreamTee(io.TextIOBase):
    """File-like object that mirrors writes to multiple text streams."""

    def __init__(self, *streams: IO[str]) -> None:
        """Initialize the tee with one or more output streams."""
        super().__init__()
        self._streams: tuple[IO[str], ...] = streams
        primary = streams[0] if streams else None
        self._encoding = getattr(primary, "encoding", "utf-8")

    @property
    def encoding(self) -> str:  # type: ignore[override]
        """Return the encoding of the primary stream."""
        return self._encoding

    def write(self, data: str) -> int:  # type: ignore[override]
        """Write data to all underlying streams."""
        length = len(data)
        for stream in self._streams:
            stream.write(data)
        return length

    def flush(self) -> None:  # type: ignore[override]
        """Flush all underlying streams."""
        for stream in self._streams:
            _flush_stream(stream)

    def isatty(self) -> bool:  # type: ignore[override]
        """Return True if any underlying stream is a TTY."""
        return any(_stream_isatty(stream) for stream in self._streams)

    def fileno(self) -> int:  # type: ignore[override]
        """Return the file descriptor of the first stream that has one."""
        for stream in self._streams:
            result = _try_get_fileno(stream)
            if result is not None:
                return result
        raise OSError("fileno is unsupported for tee console stream")

    def writable(self) -> bool:  # type: ignore[override]
        """Return True since this stream supports writing."""
        return True

    def readable(self) -> bool:  # type: ignore[override]
        """Return False since this stream does not support reading."""
        return False

    def close(self) -> None:  # type: ignore[override]
        """No-op; underlying streams are not closed by the tee."""
        # We deliberately do not close underlying streams.
        return None

    @property  # type: ignore[override]
    def closed(self) -> bool:
        """Return False; the tee is never closed."""
        return False


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
        stream: str = "stderr",
        stream_target: IO[str] | None = None,
    ) -> None:
        """Configure the console adapter with colour and style overrides.

        Args:
            console: Optional pre-configured Rich console instance.
            force_color: Force ANSI colour even when Rich would disable it.
            no_color: Disable colour regardless of terminal capabilities.
            styles: Mapping of levels to Rich style strings overriding defaults.
            format_preset: Named preset from :data:`_CONSOLE_PRESETS`.
            format_template: Custom ``str.format`` template overriding presets.
            stream: Destination stream selector: ``"stdout"``, ``"stderr"``,
                ``"both"``, ``"custom"``, or ``"none"``.
            stream_target: Custom text IO object used when ``stream == "custom"``.

        """
        if console is not None:
            self._console = console
        else:
            self._console = self._build_console(stream, stream_target, force_color, no_color)
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

        Example:
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
        payload = build_format_payload(event).to_dict()
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

    def _build_console(self, stream: str, stream_target: IO[str] | None, force_color: bool, no_color: bool) -> Console:
        """Create a Rich console routed to the requested stream."""
        stream_mode = stream.lower()
        if stream_mode == "stdout":
            return Console(force_terminal=force_color, no_color=no_color)
        if stream_mode == "stderr":
            return Console(stderr=True, force_terminal=force_color, no_color=no_color)
        if stream_mode == "both":
            tee = _ConsoleStreamTee(sys.stdout, sys.stderr)
            return Console(file=cast(IO[str], tee), force_terminal=force_color, no_color=no_color)
        if stream_mode == "custom":
            if stream_target is None:
                raise ValueError("stream_target must be provided when stream='custom'")
            return Console(file=stream_target, force_terminal=force_color, no_color=no_color)
        if stream_mode == "none":
            tee = _ConsoleStreamTee()
            return Console(file=cast(IO[str], tee), force_terminal=force_color, no_color=no_color)
        raise ValueError(f"Unsupported console stream: {stream_mode}")


@lru_cache(maxsize=16)
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
    preset = (format_preset or _default_preset()).lower()
    try:
        return _CONSOLE_PRESETS[preset], preset
    except KeyError as exc:
        raise ValueError(f"Unknown console format preset: {format_preset!r}") from exc


__all__ = ["CONSOLE_PRESETS", "RichConsoleAdapter"]
