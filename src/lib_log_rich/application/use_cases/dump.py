"""Use case exporting buffered events through a dump adapter.

Purpose
-------
Provide the application-layer glue between the ring buffer and dump adapter.

System Role
-----------
Invoked by :func:`lib_log_rich.dump` to render, persist, and flush buffered
events.

Alignment Notes
---------------
The callable returned here mirrors the behaviour described in
``docs/systemdesign/module_reference.md`` for dump workflows (filtering by level,
optional templates, colour toggles).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from lib_log_rich.domain.enums import ConsoleTheme, FormatPreset

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from lib_log_rich.application.ports.dump import DumpPort
    from lib_log_rich.domain import RingBuffer
    from lib_log_rich.domain.dump import DumpFormat
    from lib_log_rich.domain.dump_filter import DumpFilter
    from lib_log_rich.domain.levels import LogLevel


class CaptureDump(Protocol):
    """The dump renderer :func:`create_capture_dump` returns, keyword arguments only."""

    def __call__(
        self,
        *,
        dump_format: DumpFormat,
        path: Path | None = None,
        min_level: LogLevel | None = None,
        format_preset: FormatPreset | None = None,
        format_template: str | None = None,
        theme: ConsoleTheme | None = None,
        console_styles: Mapping[str, str] | None = None,
        dump_filter: DumpFilter | None = None,
        colorize: bool = False,
    ) -> str:
        """Render the ring buffer's events and flush the buffer."""
        ...


def _resolve_template_and_preset(
    format_template: str | None,
    format_preset: FormatPreset | None,
    default_template: str | None,
    default_format_preset: FormatPreset,
) -> tuple[str | None, FormatPreset | None]:
    """Resolve the final template and preset values for dump rendering.

    Args:
        format_template: Explicit template provided at call time.
        format_preset: Explicit preset provided at call time.
        default_template: Fallback template from configuration.
        default_format_preset: Fallback preset from configuration.

    Returns:
        Tuple of (resolved_template, resolved_preset).

    Example:
        >>> _resolve_template_and_preset(None, None, None, FormatPreset.SHORT)
        (None, <FormatPreset.SHORT: 'short'>)
        >>> _resolve_template_and_preset('{message}', FormatPreset.FULL, None, FormatPreset.SHORT)
        ('{message}', None)

    """
    template = format_template
    preset = format_preset
    if template is None:
        template = default_template
        if preset is None:
            preset = default_format_preset
    else:
        preset = None

    return template, preset


def create_capture_dump(
    *,
    ring_buffer: RingBuffer,
    dump_port: DumpPort,
    default_template: str | None = None,
    default_format_preset: FormatPreset = FormatPreset.FULL,
    default_theme: ConsoleTheme | None = None,
    default_console_styles: Mapping[str, str] | None = None,
) -> CaptureDump:
    """Return a callable capturing the current dependencies.

    Exposing a closure allows the composition root to configure dump behaviour
    once while giving the public API a pure function focussing on rendering.

    Args:
        ring_buffer: Buffer supplying the events to export.
        dump_port: Adapter responsible for formatting and persistence.
        default_template: Optional fallback template when none is provided at
            call time.
        default_format_preset: Preset to use when neither a custom template
            nor explicit preset is provided. Defaults to ``FormatPreset.FULL``.
        default_theme: Palette stored on the runtime; used when callers do
            not supply a specific theme for dumps.
        default_console_styles: Style mapping associated with the runtime's
            console output; used to colour text dumps when no override is supplied.

    Example:
        >>> from lib_log_rich.application.ports.dump import DumpPort
        >>> from lib_log_rich.domain import RingBuffer
        >>> from lib_log_rich.domain.dump import DumpFormat
        >>> class DummyDump(DumpPort):
        ...     def __init__(self):
        ...         self.calls = []
        ...     def dump(self, events, *, dump_format, path, min_level, format_preset, format_template, theme, console_styles, colorize):
        ...         self.calls.append((
        ...             len(list(events)), dump_format, path, min_level, format_preset,
        ...             format_template, theme, console_styles, colorize,
        ...         ))
        ...         return 'payload'
        >>> ring = RingBuffer(max_events=5)
        >>> dump_port = DummyDump()
        >>> capture = create_capture_dump(ring_buffer=ring, dump_port=dump_port, default_template='{message}')
        >>> result = capture(
        ...     dump_format=DumpFormat.TEXT,
        ...     path=None,
        ...     min_level=None,
        ...     format_preset=None,
        ...     format_template=None,
        ...     dump_filter=None,
        ...     colorize=False,
        ... )
        >>> result
        'payload'
        >>> dump_port.calls[0][1] is DumpFormat.TEXT
        True

    """

    def capture(
        *,
        dump_format: DumpFormat,
        path: Path | None = None,
        min_level: LogLevel | None = None,
        format_preset: FormatPreset | None = None,
        format_template: str | None = None,
        theme: ConsoleTheme | None = None,
        console_styles: Mapping[str, str] | None = None,
        dump_filter: DumpFilter | None = None,
        colorize: bool = False,
    ) -> str:
        """Render the ring buffer and flush it after a successful dump.

        Ensures dumps represent the exact events flushed to disk while keeping
        the in-memory buffer clean for subsequent captures.

        Note:
            Calls :meth:`RingBuffer.flush` after invoking the adapter.
        """
        template, preset = _resolve_template_and_preset(format_template, format_preset, default_template, default_format_preset)
        resolved_theme = theme if theme is not None else default_theme
        resolved_styles = console_styles if console_styles is not None else default_console_styles

        events = ring_buffer.snapshot()
        if dump_filter and dump_filter.is_active():
            events = [event for event in events if dump_filter.matches(event)]
        payload = dump_port.dump(
            events,
            dump_format=dump_format,
            path=path,
            min_level=min_level,
            format_preset=preset,
            format_template=template,
            theme=resolved_theme,
            console_styles=resolved_styles,
            colorize=colorize,
        )
        ring_buffer.flush()
        return payload

    return capture


__all__ = ["CaptureDump", "create_capture_dump"]
