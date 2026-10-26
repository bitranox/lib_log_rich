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
from contextlib import suppress
from functools import lru_cache
from typing import IO, TYPE_CHECKING, cast

from rich.console import Console
from rich.text import Text

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain.enums import ConsoleStream, FormatPreset, require_enum_member
from lib_log_rich.domain.levels import LogLevel

from .._formatting import build_format_payload

if TYPE_CHECKING:
    from collections.abc import Mapping

    from lib_log_rich.domain.events import LogEvent

_STYLE_MAP: Mapping[LogLevel, str] = {
    LogLevel.DEBUG: "dim",
    LogLevel.INFO: "cyan",
    LogLevel.WARNING: "yellow",
    LogLevel.ERROR: "red",
    LogLevel.CRITICAL: "bold red",
}

#: Default Rich styles keyed by :class:`LogLevel` severity.


#: Console line templates by preset. Brackets are plain text: every line is
#: printed as a :class:`rich.text.Text`, so no Rich markup is interpreted and no
#: escaping is needed (see :meth:`RichConsoleAdapter.emit`).
_CONSOLE_PRESETS: Mapping[FormatPreset, str] = {
    FormatPreset.FULL: "{timestamp_trimmed_naive} {level_icon}{LEVEL:>8} {logger_name} — {message}{context_fields}",
    FormatPreset.SHORT: "[{hh}:{mm}:{ss}][{level_code} {level_icon}][{logger_name}]: {message}",
    FormatPreset.FULL_LOC: "{timestamp_trimmed_naive_loc} {level_icon}{LEVEL:>8} {logger_name} — {message}{context_fields}",
    FormatPreset.SHORT_LOC: "[{hh_loc}:{mm_loc}:{ss_loc}][{level_code}]: {message}",
    FormatPreset.SHORT_LOC_ICON: "[{hh_loc}:{mm_loc}:{ss_loc}] {level_icon} {message}",
}


def _default_preset() -> FormatPreset:
    """Return platform-appropriate default console preset."""
    return FormatPreset.SHORT_LOC_ICON if sys.platform == "win32" else FormatPreset.SHORT_LOC


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


def _no_terminal_width(*streams: IO[str]) -> bool:
    """Return True when a target stream is not a terminal, so Rich must not wrap.

    Off a terminal Rich still hard-wraps at its default 80 columns, which split
    one event into several journal or log-file records, the first ending in a
    space. Judged on the real stream, not ``force_color``: a pipe with colour
    forced on has no width either. With several targets (``stream="both"``) one
    non-terminal is enough, since wrapping would split the lines it receives.
    """
    return not all(_stream_isatty(stream) for stream in streams)


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

    # typeshed declares TextIOBase.encoding a plain str attribute, but CPython's C base makes it
    # a read-only slot, so the per-instance value can only come from a property. Remove the
    # ignore once typeshed declares encoding as a read-only property.
    @property
    def encoding(self) -> str:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Return the encoding of the primary stream."""
        return self._encoding

    def write(self, data: str) -> int:
        """Write data to all underlying streams."""
        length = len(data)
        for stream in self._streams:
            stream.write(data)
        return length

    def flush(self) -> None:
        """Flush all underlying streams."""
        for stream in self._streams:
            _flush_stream(stream)

    def isatty(self) -> bool:
        """Return True if any underlying stream is a TTY."""
        return any(_stream_isatty(stream) for stream in self._streams)

    def fileno(self) -> int:
        """Return the file descriptor of the first stream that has one."""
        for stream in self._streams:
            result = _try_get_fileno(stream)
            if result is not None:
                return result
        raise OSError("fileno is unsupported for tee console stream")

    def writable(self) -> bool:
        """Return True since this stream supports writing."""
        return True

    def readable(self) -> bool:
        """Return False since this stream does not support reading."""
        return False

    def close(self) -> None:
        """No-op; underlying streams are not closed by the tee."""
        # We deliberately do not close underlying streams.
        return None

    @property
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
        format_preset: FormatPreset | None = None,
        format_template: str | None = None,
        stream: ConsoleStream = ConsoleStream.STDERR,
        stream_target: IO[str] | None = None,
    ) -> None:
        """Configure the console adapter with colour and style overrides.

        Args:
            console: Optional pre-configured Rich console instance.
            force_color: Force ANSI colour even when Rich would disable it.
            no_color: Disable colour regardless of terminal capabilities.
            styles: Mapping of levels to Rich style strings overriding defaults.
            format_preset: Line layout; ``None`` picks the platform default.
            format_template: Custom ``str.format`` template overriding presets.
            stream: Destination stream; ``ConsoleStream.CUSTOM`` writes to
                ``stream_target``.
            stream_target: Custom text IO object used with ``ConsoleStream.CUSTOM``.

        Raises:
            TypeError: If ``format_preset`` or ``stream`` is not a member of its enum.

        Example:
            >>> RichConsoleAdapter(stream='stderr')
            Traceback (most recent call last):
            ...
            TypeError: stream must be a member of ConsoleStream, got 'stderr'

        """
        selected_stream = require_enum_member(stream, ConsoleStream, parameter="stream")
        selected_preset = None if format_preset is None else require_enum_member(format_preset, FormatPreset, parameter="format_preset")
        if console is not None:
            self._console = console
        else:
            self._console = self._build_console(selected_stream, stream_target, force_color=force_color, no_color=no_color)
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
        self._template, self._preset = _resolve_template(selected_preset, format_template)

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        r"""Print ``event`` using Rich with optional colour.

        The rendered line is printed literally: square brackets, backslashes and
        ``:emoji:`` codes in the message, in ``extra`` values or in the template
        reach the console unchanged, and the level style colours the whole line.

        Args:
            event: Log event to render.
            colorize: Apply the level style when colour is not disabled.

        Example:
            >>> from datetime import datetime, timezone
            >>> from io import StringIO
            >>> from lib_log_rich.domain.context import LogContext
            >>> from lib_log_rich.domain.events import LogEvent
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
            >>> console = Console(file=StringIO(), record=True)
            >>> adapter = RichConsoleAdapter(console=console)
            >>> adapter.emit(event, colorize=False)
            >>> 'msg' in console.export_text()
            True
            >>> hostile = LogEvent('id2', event.timestamp, 'svc', LogLevel.INFO, 'a[/]b [red] C:\\[x] :smile:', ctx)
            >>> adapter = RichConsoleAdapter(console=console, format_template='{message}')
            >>> _ = console.export_text(clear=True)
            >>> adapter.emit(hostile, colorize=False)
            >>> console.export_text()
            'a[/]b [red] C:\\[x] :smile:\n'

        """
        style = self._style_map.get(event.level, "") if colorize and not self._no_color else ""
        line = self._format_line(event)
        # A plain str passed to Console.print is parsed as Rich markup and emoji
        # codes, and log text is data the program does not control: "[/]" raised
        # MarkupError (the line was lost), "[red]" vanished, "\[x]" lost its
        # backslash, ":smile:" became an emoji. Text is never parsed, and unlike
        # rich.markup.escape() it also leaves emoji codes alone, so the line is
        # printed exactly as formatted while style= still colours all of it.
        self._console.print(Text(line, style=style), highlight=False)

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
            # A custom template (preset None) or a non-FULL preset that fails to
            # render falls back to FULL; FULL itself has nothing to fall back to.
            if self._preset is not FormatPreset.FULL:
                fallback = _CONSOLE_PRESETS[FormatPreset.FULL]
                try:
                    return fallback.format(**payload)
                except Exception as exc:  # pragma: no cover - defensive
                    raise ValueError("Console format template failed to render") from exc
            raise

    def _build_console(self, stream: ConsoleStream, stream_target: IO[str] | None, *, force_color: bool, no_color: bool) -> Console:
        """Create a Rich console routed to the requested stream."""
        if stream is ConsoleStream.STDOUT:
            return Console(force_terminal=force_color, no_color=no_color, soft_wrap=_no_terminal_width(sys.stdout))
        if stream is ConsoleStream.BOTH:
            tee = _ConsoleStreamTee(sys.stdout, sys.stderr)
            return Console(file=cast("IO[str]", tee), force_terminal=force_color, no_color=no_color, soft_wrap=_no_terminal_width(sys.stdout, sys.stderr))
        if stream is ConsoleStream.CUSTOM:
            if stream_target is None:
                raise ValueError("stream_target must be provided when stream='custom'")
            return Console(file=stream_target, force_terminal=force_color, no_color=no_color, soft_wrap=_no_terminal_width(stream_target))
        if stream is ConsoleStream.NONE:
            tee = _ConsoleStreamTee()
            return Console(file=cast("IO[str]", tee), force_terminal=force_color, no_color=no_color, soft_wrap=True)
        # ConsoleStream.STDERR, the one member left.
        return Console(stderr=True, force_terminal=force_color, no_color=no_color, soft_wrap=_no_terminal_width(sys.stderr))

    def flush(self) -> None:
        """Flush the underlying console stream.

        Ensures all buffered output is written to the stream. Handles custom
        streams, tee streams, and standard stdout/stderr gracefully. Safe to
        call even if the stream doesn't support flushing.

        Example:
            >>> from io import StringIO
            >>> buffer = StringIO()
            >>> adapter = RichConsoleAdapter(stream=ConsoleStream.CUSTOM, stream_target=buffer)
            >>> adapter.flush()  # no-op but doesn't raise

        """
        file = self._console.file
        flush = getattr(file, "flush", None)
        if callable(flush):
            # Stream may be closed, redirected, or not support flush.
            with suppress(Exception):
                flush()


@lru_cache(maxsize=16)
def _resolve_template(format_preset: FormatPreset | None, format_template: str | None) -> tuple[str, FormatPreset | None]:
    """Select the console template and the preset it came from.

    Args:
        format_preset: Preset to load when ``format_template`` is empty;
            ``None`` picks the platform default.
        format_template: Custom template string overriding presets.

    Returns:
        ``(template, preset)`` where ``preset`` is ``None`` for a custom template.

    Examples:
        >>> _resolve_template(FormatPreset.FULL, None)[1] is FormatPreset.FULL
        True
        >>> _resolve_template(FormatPreset.FULL, '{message}')
        ('{message}', None)

    """
    if format_template:
        return format_template, None
    preset = format_preset or _default_preset()
    return _CONSOLE_PRESETS[preset], preset


__all__ = ["RichConsoleAdapter"]
