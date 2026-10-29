"""A log line reaches a stream that is not a terminal as one write.

Rich switches to legacy Windows rendering whenever the console mode of the
process's stdout handle cannot be read, which is the case for every pipe and
redirect. That renderer writes each styled segment (timestamp, space, icon,
message, newline) as its own call, so a line another thread writes in between
lands mid-line in CI logs. The adapter therefore turns legacy rendering off for
any target that is not a terminal and leaves Rich's own detection in place for a
real console.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO

import pytest
import rich.console

from lib_log_rich.adapters.console import rich_console as console_module
from lib_log_rich.adapters.console.rich_console import RichConsoleAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.enums import ConsoleStream
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC, WINDOWS_ONLY

pytestmark = [OS_AGNOSTIC]


def _event() -> LogEvent:
    context = LogContext(service="svc", environment="test", job_id="job")
    return LogEvent("evt", datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), "tests.legacy", LogLevel.INFO, "hello", context)


class _TerminalStream(StringIO):
    """A text stream that reports itself as a terminal."""

    def isatty(self) -> bool:
        return True


@pytest.fixture
def rich_detects_a_legacy_console(monkeypatch: pytest.MonkeyPatch) -> None:
    # What Rich's detection answers on Windows when stdout is a pipe: the console
    # mode cannot be read, so it reports a console without VT support.
    monkeypatch.setattr(rich.console, "detect_legacy_windows", lambda: True)


@pytest.fixture
def built_consoles(monkeypatch: pytest.MonkeyPatch) -> list[rich.console.Console]:
    consoles: list[rich.console.Console] = []
    original = console_module.Console

    def build(*args: object, **kwargs: object) -> rich.console.Console:
        console = original(*args, **kwargs)
        consoles.append(console)
        return console

    monkeypatch.setattr(console_module, "Console", build)
    return consoles


@pytest.mark.usefixtures("rich_detects_a_legacy_console")
def test_a_custom_target_that_is_not_a_terminal_never_gets_legacy_rendering(built_consoles: list[rich.console.Console]) -> None:
    RichConsoleAdapter(stream=ConsoleStream.CUSTOM, stream_target=StringIO(), format_template="{message}")

    assert [console.legacy_windows for console in built_consoles] == [False]


@pytest.mark.usefixtures("rich_detects_a_legacy_console")
def test_a_redirected_stderr_never_gets_legacy_rendering(built_consoles: list[rich.console.Console], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stderr", StringIO())

    RichConsoleAdapter(stream=ConsoleStream.STDERR, format_template="{message}")

    assert [console.legacy_windows for console in built_consoles] == [False]


@pytest.mark.usefixtures("rich_detects_a_legacy_console")
def test_a_redirected_stdout_never_gets_legacy_rendering(built_consoles: list[rich.console.Console], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdout", StringIO())

    RichConsoleAdapter(stream=ConsoleStream.STDOUT, format_template="{message}")

    assert [console.legacy_windows for console in built_consoles] == [False]


@pytest.mark.usefixtures("rich_detects_a_legacy_console")
def test_both_streams_get_no_legacy_rendering_when_one_is_redirected(built_consoles: list[rich.console.Console], monkeypatch: pytest.MonkeyPatch) -> None:
    # The tee reports stdout's file descriptor, so Rich would render it the legacy way too.
    monkeypatch.setattr("sys.stdout", _TerminalStream())
    monkeypatch.setattr("sys.stderr", StringIO())

    RichConsoleAdapter(stream=ConsoleStream.BOTH, format_template="{message}")

    assert [console.legacy_windows for console in built_consoles] == [False]


@pytest.mark.usefixtures("rich_detects_a_legacy_console")
def test_a_terminal_target_keeps_rich_s_own_legacy_detection(built_consoles: list[rich.console.Console]) -> None:
    RichConsoleAdapter(stream=ConsoleStream.CUSTOM, stream_target=_TerminalStream(), format_template="{message}")

    assert [console.legacy_windows for console in built_consoles] == [True]


class _CountingStderr(StringIO):
    """Reports file descriptor 2, the condition for Rich's legacy renderer, and counts writes."""

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def write(self, text: str) -> int:
        if text:
            self.writes.append(text)
        return super().write(text)

    def fileno(self) -> int:
        return 2


@WINDOWS_ONLY
def test_one_event_reaches_a_piped_stderr_as_one_write_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    # Under pytest the process's stdout is a pipe, so on unfixed code Rich's own
    # detection picks the legacy renderer here and writes the line in pieces.
    target = _CountingStderr()
    monkeypatch.setattr("sys.stderr", target)
    adapter = RichConsoleAdapter(stream=ConsoleStream.STDERR, format_template="{message}")

    adapter.emit(_event(), colorize=True)

    assert target.writes == [target.getvalue()]
    assert target.getvalue().endswith("hello\n")
