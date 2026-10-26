"""Console adapters that enqueue rendered log lines for external consumers."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

from rich.console import Console

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain.enums import ExportStyle

from .rich_console import RichConsoleAdapter, require_enum_member

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from queue import Queue

    from lib_log_rich.domain.enums import FormatPreset
    from lib_log_rich.domain.events import LogEvent

LOGGER = logging.getLogger(__name__)


class _BaseQueueConsoleAdapter(ConsolePort):
    """Shared logic for queue-backed console adapters."""

    def __init__(
        self,
        *,
        export_style: ExportStyle = ExportStyle.ANSI,
        force_color: bool = False,
        no_color: bool = False,
        styles: Mapping[str, str] | None = None,
        format_preset: FormatPreset | None = None,
        format_template: str | None = None,
        console_width: int | None = None,
    ) -> None:
        """Initialize the base queue console adapter with formatting options.

        Raises:
            TypeError: If ``export_style`` is not an :class:`ExportStyle` member.

        """
        self._export_style = require_enum_member(export_style, ExportStyle, parameter="export_style")
        self._buffer = io.StringIO()
        # A width is the caller's request for fixed-width lines. Without one, the
        # buffer has no width of its own and Rich's default of 80 columns would
        # split one event into several queue segments.
        self._console = Console(
            file=self._buffer,
            record=True,
            force_terminal=force_color,
            no_color=no_color,
            width=console_width,
            soft_wrap=console_width is None,
        )
        self._no_color = no_color
        self._adapter = RichConsoleAdapter(
            console=self._console,
            force_color=force_color,
            no_color=no_color,
            styles=styles,
            format_preset=format_preset,
            format_template=format_template,
        )

    def _render_event(self, event: LogEvent, *, colorize: bool) -> Sequence[str]:
        """Render ``event`` and return newly produced segments."""
        self._adapter.emit(event, colorize=colorize)
        if self._export_style is ExportStyle.HTML:
            rendered = self._console.export_html(clear=True, inline_styles=True)
            self._buffer.seek(0)
            self._buffer.truncate(0)
            return [rendered] if rendered else []
        rendered_text = self._console.export_text(
            clear=True,
            styles=colorize and not self._no_color,
        )
        self._buffer.seek(0)
        self._buffer.truncate(0)
        if not rendered_text:
            return []
        return rendered_text.splitlines()


class QueueConsoleAdapter(_BaseQueueConsoleAdapter):
    """Console adapter that pushes rendered output into a thread-safe queue."""

    def __init__(
        self,
        queue: Queue[str],
        *,
        export_style: ExportStyle = ExportStyle.ANSI,
        force_color: bool = False,
        no_color: bool = False,
        styles: Mapping[str, str] | None = None,
        format_preset: FormatPreset | None = None,
        format_template: str | None = None,
        console_width: int | None = None,
    ) -> None:
        """Initialize the queue console adapter with a thread-safe queue."""
        super().__init__(
            export_style=export_style,
            force_color=force_color,
            no_color=no_color,
            styles=styles,
            format_preset=format_preset,
            format_template=format_template,
            console_width=console_width,
        )
        self._queue = queue

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        """Render the event and enqueue each output segment."""
        for segment in self._render_event(event, colorize=colorize):
            self._queue.put(segment)


class AsyncQueueConsoleAdapter(_BaseQueueConsoleAdapter):
    """Console adapter that pushes rendered output into an asyncio queue."""

    def __init__(
        self,
        queue: asyncio.Queue[str],
        *,
        export_style: ExportStyle = ExportStyle.ANSI,
        force_color: bool = False,
        no_color: bool = False,
        styles: Mapping[str, str] | None = None,
        format_preset: FormatPreset | None = None,
        format_template: str | None = None,
        console_width: int | None = None,
        on_drop: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the async queue console adapter with an asyncio queue."""
        super().__init__(
            export_style=export_style,
            force_color=force_color,
            no_color=no_color,
            styles=styles,
            format_preset=format_preset,
            format_template=format_template,
            console_width=console_width,
        )
        self._queue = queue
        self._on_drop = on_drop

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        """Render the event and enqueue segments, dropping on overflow."""
        for segment in self._render_event(event, colorize=colorize):
            try:
                self._queue.put_nowait(segment)
            except asyncio.QueueFull:  # pragma: no cover - defensive  # noqa: PERF203 - each segment must be tried independently
                self._handle_drop(segment)

    def _handle_drop(self, segment: str) -> None:
        if self._on_drop is not None:
            try:
                self._on_drop(segment)
            except Exception:  # pragma: no cover - defensive logging path
                LOGGER.warning("AsyncQueueConsoleAdapter drop handler raised", exc_info=True)
            return
        LOGGER.warning("AsyncQueueConsoleAdapter queue full; dropped console segment")


__all__ = [
    "AsyncQueueConsoleAdapter",
    "QueueConsoleAdapter",
]
