"""Console adapters that enqueue rendered log lines for external consumers."""

from __future__ import annotations

import asyncio
import io
import logging
from collections.abc import Callable, Mapping, Sequence
from queue import Queue
from typing import Literal

from rich.console import Console

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain.events import LogEvent

from .rich_console import RichConsoleAdapter

LOGGER = logging.getLogger(__name__)

ExportStyle = Literal["ansi", "html"]


class _BaseQueueConsoleAdapter(ConsolePort):
    """Shared logic for queue-backed console adapters."""

    def __init__(
        self,
        *,
        export_style: ExportStyle = "ansi",
        force_color: bool = False,
        no_color: bool = False,
        styles: Mapping[str, str] | None = None,
        format_preset: str | None = None,
        format_template: str | None = None,
        console_width: int | None = None,
    ) -> None:
        """Initialize the base queue console adapter with formatting options."""
        self._export_style = export_style
        self._buffer = io.StringIO()
        self._console = Console(
            file=self._buffer,
            record=True,
            force_terminal=force_color,
            no_color=no_color,
            width=console_width,
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
        if self._export_style == "html":
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
        export_style: ExportStyle = "ansi",
        force_color: bool = False,
        no_color: bool = False,
        styles: Mapping[str, str] | None = None,
        format_preset: str | None = None,
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
        export_style: ExportStyle = "ansi",
        force_color: bool = False,
        no_color: bool = False,
        styles: Mapping[str, str] | None = None,
        format_preset: str | None = None,
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
            except asyncio.QueueFull:  # pragma: no cover - defensive
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
    "QueueConsoleAdapter",
    "AsyncQueueConsoleAdapter",
    "ExportStyle",
]
