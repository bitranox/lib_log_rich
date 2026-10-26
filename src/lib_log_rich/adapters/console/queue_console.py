"""Console adapters that enqueue rendered log lines for external consumers."""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

from rich.console import Console

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.domain.enums import ExportStyle, require_enum_member

from .rich_console import RichConsoleAdapter

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
    """Console adapter that pushes rendered output into an asyncio queue.

    ``asyncio.Queue`` is not thread-safe, and with the runtime queue enabled ``emit`` runs on
    the queue worker thread: a segment put there lands in the queue, but a consumer awaiting
    ``get()`` sleeps until something else wakes its loop. So the adapter remembers the event
    loop that owns the queue - the one running when it is built, or ``loop=`` - and an
    ``emit`` from any other thread hands each segment to that loop with
    ``call_soon_threadsafe``. Without a known loop it puts directly, as on the loop's thread.
    """

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
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Initialize the async queue console adapter with an asyncio queue.

        Args:
            queue: The queue the consumer awaits.
            export_style: How segments are rendered (ANSI text or HTML).
            force_color: Render colour even when the console is not a terminal.
            no_color: Render without colour.
            styles: Per-level Rich style overrides.
            format_preset: A preset line layout.
            format_template: A custom line template; overrides ``format_preset``.
            console_width: Fixed line width; ``None`` keeps each event on one segment.
            on_drop: Called with each segment that could not be queued.
            loop: The event loop that owns ``queue``. Defaults to the loop running at
                construction; give it when the adapter is built outside that loop.

        Raises:
            TypeError: If ``export_style`` is not an :class:`ExportStyle` member.

        """
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
        self._loop = loop if loop is not None else _running_loop()

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        """Render the event and enqueue segments, dropping on overflow."""
        segments = self._render_event(event, colorize=colorize)
        loop = self._loop
        if loop is None or _running_loop() is loop:
            for segment in segments:
                self._offer(segment)
            return
        for segment in segments:
            try:
                loop.call_soon_threadsafe(self._offer, segment)
            except RuntimeError:  # noqa: PERF203 - the loop closed; each segment is reported as dropped
                self._handle_drop(segment)

    def _offer(self, segment: str) -> None:
        """Put ``segment`` into the queue; runs on the loop's thread or without a known loop."""
        try:
            self._queue.put_nowait(segment)
        except asyncio.QueueFull:
            self._handle_drop(segment)

    def _handle_drop(self, segment: str) -> None:
        if self._on_drop is not None:
            try:
                self._on_drop(segment)
            except Exception:  # pragma: no cover - defensive logging path
                LOGGER.warning("AsyncQueueConsoleAdapter drop handler raised", exc_info=True)
            return
        LOGGER.warning("AsyncQueueConsoleAdapter queue full; dropped console segment")


def _running_loop() -> asyncio.AbstractEventLoop | None:
    """Return the event loop running on this thread, or ``None`` outside one."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


__all__ = [
    "AsyncQueueConsoleAdapter",
    "QueueConsoleAdapter",
]
