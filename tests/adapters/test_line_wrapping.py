"""One log event stays one line wherever no terminal width applies.

Rich hard-wraps at the console width, and a console that is not a terminal
gets Rich's default of 80 columns. A long event written to a redirected stderr,
a systemd journal or a file was split into several records, the first ending in
a trailing space. The HTML text dump and the queue console (without an explicit
``console_width``) split it the same way.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from io import StringIO
from queue import Queue

import pytest

from lib_log_rich.adapters.console.queue_console import QueueConsoleAdapter
from lib_log_rich.adapters.console.rich_console import RichConsoleAdapter
from lib_log_rich.adapters.dump import DumpAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.enums import ConsoleStream, ExportStyle
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

# Two words, so a word-wrapping renderer has a place to break the line.
_LONG_MESSAGE = "A" * 50 + " " + "B" * 50


def _long_event() -> LogEvent:
    context = LogContext(service="svc", environment="test", job_id="job")
    return LogEvent("evt", datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), "tests.wrap", LogLevel.INFO, _LONG_MESSAGE, context)


class _TerminalStream(StringIO):
    """A text stream that reports itself as a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def no_columns_override(monkeypatch: pytest.MonkeyPatch) -> None:
    # Rich reads COLUMNS for the width; a CI runner or a shell may set it.
    monkeypatch.delenv("COLUMNS", raising=False)


def test_a_long_event_to_a_stream_that_is_not_a_terminal_stays_one_line() -> None:
    target = StringIO()
    adapter = RichConsoleAdapter(stream=ConsoleStream.CUSTOM, stream_target=target, format_template="{message}")

    adapter.emit(_long_event(), colorize=False)

    assert target.getvalue() == _LONG_MESSAGE + "\n"


def test_a_long_event_to_the_default_stderr_stays_one_line_when_stderr_is_redirected(capsys: pytest.CaptureFixture[str]) -> None:
    adapter = RichConsoleAdapter(format_template="{message}")

    adapter.emit(_long_event(), colorize=False)

    assert capsys.readouterr().err == _LONG_MESSAGE + "\n"


def test_a_long_event_to_a_terminal_still_wraps_at_the_terminal_width() -> None:
    target = _TerminalStream()
    adapter = RichConsoleAdapter(stream=ConsoleStream.CUSTOM, stream_target=target, format_template="{message}")

    adapter.emit(_long_event(), colorize=False)

    plain = re.sub(r"\x1b\[[0-9;]*m", "", target.getvalue())
    assert [line.rstrip() for line in plain.splitlines()] == ["A" * 50, "B" * 50]


def test_a_long_event_through_the_queue_console_stays_one_segment_without_a_width() -> None:
    queue: Queue[str] = Queue()
    adapter = QueueConsoleAdapter(queue, export_style=ExportStyle.ANSI, format_template="{message}")

    adapter.emit(_long_event(), colorize=False)

    segments = [queue.get_nowait() for _ in range(queue.qsize())]
    assert segments == [_LONG_MESSAGE]


def test_a_long_event_in_the_html_text_dump_stays_one_line() -> None:
    payload = DumpAdapter().dump([_long_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=False)

    assert _LONG_MESSAGE in payload
