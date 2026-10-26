"""Console output renders log text literally, never as Rich console markup.

A log message, an ``extra`` value or a bound context field is data the program
does not control: a path, a unit name, an environment value, user input. Rich
reads a plain ``str`` as console markup, so such data could raise
``MarkupError`` (the whole line was dropped), vanish (``[red]``), lose a
backslash (``C:\\[x]``) or turn into an emoji (``:smile:``). These tests drive
the real ``init()`` pipeline, queue included, into a Console writing to a
``StringIO`` and require every value back verbatim.
"""

from __future__ import annotations

import contextlib
import html
import os
from datetime import datetime, timezone
from io import StringIO
from queue import Queue
from typing import TYPE_CHECKING

import pytest
from rich.color import Color
from rich.console import Console
from rich.style import Style
from rich.text import Text

from lib_log_rich.adapters.console import QueueConsoleAdapter
from lib_log_rich.adapters.console.rich_console import RichConsoleAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.enums import ConsoleStream, FormatPreset
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.runtime import RuntimeConfig, bind, dump, flush, getLogger, init, shutdown
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [OS_AGNOSTIC]

#: Values Rich markup would break, swallow, rewrite or replace.
HOSTILE_VALUES: tuple[str, ...] = (
    "skipped a[/]b",
    "[bold]x[/bold]",
    "[red]",
    "[link=https://example.com]x[/link]",
    "C:\\[x]\\y",
    ":smile:",
    "Backup [Daily]",
    "Job [1]",
)


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep host LOG_* overrides out and tear the runtime down even on failure."""
    for key in [name for name in os.environ if name.startswith("LOG_")]:
        monkeypatch.delenv(key, raising=False)
    # A Console on a StringIO wraps at 80 columns; the full preset with context
    # fields is longer, and a wrap inside a value would split the token the
    # assertions look for. Rich reads COLUMNS when it builds the Console.
    monkeypatch.setenv("COLUMNS", "1000")
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            shutdown()


_ = _isolated_runtime


def _init_console(
    buffer: StringIO,
    *,
    template: str | None = "{message}",
    preset: FormatPreset | None = None,
    force_color: bool = False,
    styles: dict[str, str] | None = None,
) -> None:
    init(
        RuntimeConfig(
            service="svc",
            environment="test",
            queue_enabled=True,
            enable_ring_buffer=False,
            enable_graylog=False,
            console_stream=ConsoleStream.CUSTOM,
            console_stream_target=buffer,
            console_format_template=template,
            console_format_preset=preset,
            force_color=force_color,
            no_color=not force_color,
            console_styles=styles,
        )
    )


@pytest.mark.parametrize("value", HOSTILE_VALUES)
def test_a_message_reaches_the_console_verbatim(value: str) -> None:
    buffer = StringIO()
    _init_console(buffer)
    logger = getLogger("tests.literal")
    logger.warning("before")
    logger.warning(value)
    logger.warning("after")
    flush()
    assert buffer.getvalue().splitlines() == ["before", value, "after"]


def test_a_markup_closing_tag_in_args_does_not_drop_the_line() -> None:
    buffer = StringIO()
    _init_console(buffer)
    getLogger("tests.literal").warning("skipped %s", "a[/]b")
    flush()
    assert buffer.getvalue().splitlines() == ["skipped a[/]b"]


@pytest.mark.parametrize("value", HOSTILE_VALUES)
def test_an_extra_field_named_by_the_template_reaches_the_console_verbatim(value: str) -> None:
    buffer = StringIO()
    _init_console(buffer, template="{message} path={extra[path]}")
    getLogger("tests.literal").warning("copied", extra={"path": value})
    flush()
    assert buffer.getvalue().splitlines() == [f"copied path={value}"]


@pytest.mark.parametrize("value", HOSTILE_VALUES)
def test_context_fields_in_the_full_preset_reach_the_console_verbatim(value: str) -> None:
    buffer = StringIO()
    _init_console(buffer, template=None, preset=FormatPreset.FULL)
    with bind(job_id="job", request_id=value):
        getLogger("tests.literal").warning("copied", extra={"path": value})
    flush()
    line = buffer.getvalue()
    assert f"request_id={value}" in line
    assert f"path={value}" in line


def test_the_short_preset_brackets_print_without_escape_backslashes() -> None:
    buffer = StringIO()
    _init_console(buffer, template=None, preset=FormatPreset.SHORT)
    getLogger("tests.literal").warning("hello")
    flush()
    line = buffer.getvalue().splitlines()[0]
    assert line.startswith("[")
    assert "\\" not in line
    assert "][tests.literal]: hello" in line


def test_the_level_style_spans_the_whole_literal_line_when_colour_is_on() -> None:
    buffer = StringIO()
    message = "a[/]b [red]x[/red] :smile:"
    _init_console(buffer, force_color=True, styles={"WARNING": "bold magenta"})
    getLogger("tests.literal").warning(message)
    flush()
    rendered = Text.from_ansi(buffer.getvalue().rstrip("\n"))
    assert rendered.plain == message
    assert [(span.start, span.end) for span in rendered.spans] == [(0, len(message))]
    style = rendered.spans[0].style
    assert isinstance(style, Style)
    assert style.bold is True
    assert style.color is not None
    # Text.from_ansi names a decoded colour by its number ("color(5)"), not "magenta".
    assert style.color.number == Color.parse("magenta").number


def test_the_queue_console_adapter_renders_text_literally() -> None:
    sink: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(sink, format_template="{message}", no_color=True)
    init(
        RuntimeConfig(
            service="svc",
            environment="test",
            queue_enabled=True,
            enable_ring_buffer=False,
            enable_graylog=False,
            console_adapter_factory=lambda _appearance: adapter,
        )
    )
    getLogger("tests.literal").warning("skipped a[/]b [red] :smile:")
    flush()
    assert sink.get_nowait() == "skipped a[/]b [red] :smile:"


def test_the_rich_adapter_records_markup_like_text_as_plain_text() -> None:
    console = Console(file=StringIO(), record=True, width=200)
    adapter = RichConsoleAdapter(console=console, format_template="{message}")
    for value in HOSTILE_VALUES:
        event = LogEvent(
            "id",
            datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc),
            "tests",
            LogLevel.INFO,
            value,
            LogContext(service="svc", environment="test", job_id="job"),
        )
        adapter.emit(event, colorize=False)
    assert console.export_text().splitlines() == list(HOSTILE_VALUES)


@pytest.mark.parametrize(("dump_format", "color"), [("text", False), ("text", True), ("html_txt", True)])
def test_dump_renders_markup_like_messages_literally(dump_format: str, color: bool) -> None:
    init(
        RuntimeConfig(
            service="svc",
            environment="test",
            queue_enabled=True,
            enable_ring_buffer=True,
            enable_graylog=False,
            console_stream=ConsoleStream.NONE,
        )
    )
    logger = getLogger("tests.literal")
    for value in HOSTILE_VALUES:
        logger.warning(value)
    flush()
    rendered = dump(dump_format=dump_format, console_format_template="{message}", color=color)
    plain = Text.from_ansi(rendered).plain if dump_format == "text" else html.unescape(rendered)
    for value in HOSTILE_VALUES:
        assert value in plain
