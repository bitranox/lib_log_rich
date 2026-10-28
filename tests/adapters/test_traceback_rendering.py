"""Every sink shows an event's ``exc_info`` and ``stack_info`` text, the way stdlib logging does.

The JSON dump and the checkpoint always carried the text; these tests cover the
sinks that used to drop it. Each one drives the real adapter: a recording Rich
console, the dump renderer, a real UDP socket for Graylog, and the injectable
senders of journald and the Windows Event Log.
"""

from __future__ import annotations

import contextlib
import io
import json
import queue
import socket
import struct
from datetime import datetime, timezone

from rich.console import Console

import lib_log_rich as log
from lib_log_rich.adapters.console.queue_console import QueueConsoleAdapter
from lib_log_rich.adapters.console.rich_console import RichConsoleAdapter
from lib_log_rich.adapters.dump import DumpAdapter
from lib_log_rich.adapters.graylog import GraylogAdapter
from lib_log_rich.adapters.structured.journald import JournaldAdapter, encode_journal_fields
from lib_log_rich.adapters.structured.windows_eventlog import WindowsEventLogAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.enums import ConsoleStream, GraylogProtocol
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.runtime import PayloadLimits, RuntimeConfig
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

EXC_TEXT = 'Traceback (most recent call last):\n  File "app.py", line 3, in <module>\n    main()\nValueError: boom'
STACK_TEXT = 'Stack (most recent call last):\n  File "app.py", line 9, in run\n    log()'


def _event(*, exc_info: str | None = EXC_TEXT, stack_info: str | None = None, extra: dict[str, object] | None = None) -> LogEvent:
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.ERROR,
        message="it failed",
        context=LogContext(service="svc", environment="test", job_id="job"),
        extra=extra or {},
        exc_info=exc_info,
        stack_info=stack_info,
    )


def _indented(text: str) -> list[str]:
    return [f"    {line}" for line in text.splitlines()]


def _console_lines(event: LogEvent) -> list[str]:
    console = Console(record=True, force_terminal=False, width=200)
    RichConsoleAdapter(console=console, no_color=True, format_template="{LEVEL} {message}").emit(event, colorize=False)
    return console.export_text().splitlines()


def test_console_prints_the_traceback_indented_under_the_line() -> None:
    lines = _console_lines(_event())

    assert lines == ["ERROR it failed", *_indented(EXC_TEXT)]


def test_console_prints_the_exception_before_the_stack() -> None:
    lines = _console_lines(_event(stack_info=STACK_TEXT))

    assert lines == ["ERROR it failed", *_indented(EXC_TEXT), *_indented(STACK_TEXT)]


def test_console_line_without_a_traceback_stays_one_line() -> None:
    assert _console_lines(_event(exc_info=None)) == ["ERROR it failed"]


def test_console_prints_traceback_brackets_literally() -> None:
    text = "Traceback (most recent call last):\nKeyError: '[bold]x[/bold]'"

    lines = _console_lines(_event(exc_info=text))

    assert lines[-1] == "    KeyError: '[bold]x[/bold]'"


def test_text_dump_puts_the_traceback_under_its_event_line() -> None:
    payload = DumpAdapter().dump([_event()], dump_format=DumpFormat.TEXT, format_template="{LEVEL} {message}")

    assert payload.splitlines() == ["ERROR it failed", *_indented(EXC_TEXT)]


def test_html_text_dump_carries_the_traceback() -> None:
    payload = DumpAdapter().dump([_event()], dump_format=DumpFormat.HTML_TXT, format_template="{LEVEL} {message}")

    assert "    ValueError: boom" in payload


def test_html_table_dump_puts_the_escaped_traceback_in_the_message_cell() -> None:
    payload = DumpAdapter().dump([_event()], dump_format=DumpFormat.HTML_TABLE)

    assert "<td>it failed<pre>" in payload
    assert "in &lt;module&gt;" in payload
    assert "ValueError: boom</pre></td>" in payload


def test_graylog_sends_the_traceback_as_full_message() -> None:
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(5)
    port = receiver.getsockname()[1]
    adapter = GraylogAdapter(host="127.0.0.1", port=port, protocol=GraylogProtocol.UDP)
    try:
        adapter.emit(_event(stack_info=STACK_TEXT))
        adapter.emit(_event(exc_info=None))
        with_trace = json.loads(receiver.recv(65535).rstrip(b"\x00"))
        without_trace = json.loads(receiver.recv(65535).rstrip(b"\x00"))
    finally:
        receiver.close()

    assert with_trace["short_message"] == "it failed"
    assert with_trace["full_message"] == f"{EXC_TEXT}\n{STACK_TEXT}"
    assert "full_message" not in without_trace


def _journald_fields(event: LogEvent) -> dict[str, object]:
    recorded: list[dict[str, object]] = []

    def sender(**fields: object) -> None:
        recorded.append(fields)

    JournaldAdapter(sender=sender).emit(event)
    return recorded[0]


def test_journald_appends_the_traceback_to_message_and_sends_it_as_fields() -> None:
    fields = _journald_fields(_event(stack_info=STACK_TEXT))

    assert fields["MESSAGE"] == f"it failed\n{EXC_TEXT}\n{STACK_TEXT}"
    assert fields["EXCEPTION_TEXT"] == EXC_TEXT
    assert fields["STACK_TEXT"] == STACK_TEXT


def test_journald_without_a_traceback_sends_no_traceback_fields() -> None:
    fields = _journald_fields(_event(exc_info=None))

    assert fields["MESSAGE"] == "it failed"
    assert "EXCEPTION_TEXT" not in fields
    assert "STACK_TEXT" not in fields


def test_journald_moves_a_caller_extra_named_like_a_traceback_field_aside() -> None:
    fields = _journald_fields(_event(extra={"exception_text": "mine"}))

    assert fields["EXCEPTION_TEXT"] == EXC_TEXT
    assert fields["EXTRA_EXCEPTION_TEXT"] == "mine"


def test_windows_event_log_adds_the_traceback_strings() -> None:
    reported: list[list[str]] = []

    def reporter(*, app_name: str, event_id: int, event_type: int, strings: list[str]) -> None:
        reported.append(strings)

    WindowsEventLogAdapter(reporter=reporter).emit(_event(stack_info=STACK_TEXT))

    assert f"exc_info={EXC_TEXT}" in reported[0]
    assert f"stack_info={STACK_TEXT}" in reported[0]


def test_logger_exception_shows_its_traceback_on_the_console() -> None:
    buffer = io.StringIO()
    with contextlib.suppress(RuntimeError):
        log.shutdown()
    log.init(
        RuntimeConfig(
            service="tests",
            environment="ci",
            queue_enabled=False,
            enable_graylog=False,
            console_stream=ConsoleStream.CUSTOM,
            console_stream_target=buffer,
            no_color=True,
        )
    )
    try:
        try:
            raise ValueError("bad input")
        except ValueError:
            log.getLogger("tests.exc").exception("failed")
    finally:
        log.shutdown()

    output = buffer.getvalue()
    assert "failed" in output
    assert "    ValueError: bad input" in output


def test_journald_reserves_the_traceback_field_names_for_an_event_without_one() -> None:
    event = _event(exc_info=None, extra={"exception_text": "mine", "stack_text": "theirs"})

    fields = _journald_fields(event)

    assert fields["EXTRA_EXCEPTION_TEXT"] == "mine"
    assert fields["EXTRA_STACK_TEXT"] == "theirs"
    assert "EXCEPTION_TEXT" not in fields


def test_journald_socket_encoding_sends_a_multi_line_value_length_prefixed() -> None:
    encoded = encode_journal_fields({"MESSAGE": "a\nb", "PRIORITY": 3})

    assert encoded == b"MESSAGE\n" + struct.pack("<Q", 3) + b"a\nb\n" + b"PRIORITY=3\n"


def test_queue_console_keeps_a_traceback_event_in_one_segment() -> None:
    segments: queue.Queue[str] = queue.Queue()
    adapter = QueueConsoleAdapter(segments, format_template="{LEVEL} {message}", no_color=True)

    adapter.emit(_event(), colorize=False)

    assert segments.get_nowait() == "\n".join(["ERROR it failed", *_indented(EXC_TEXT)])
    assert segments.empty()


def test_colourised_text_dump_colours_each_traceback_line_on_its_own() -> None:
    payload = DumpAdapter().dump([_event()], dump_format=DumpFormat.TEXT, format_template="{LEVEL} {message}", colorize=True)

    lines = payload.split("\n")
    assert len(lines) == 1 + len(EXC_TEXT.splitlines())
    assert all(line.startswith("\x1b[") and line.endswith("\x1b[0m") for line in lines)


def _stack_of_native_call() -> str:
    with contextlib.suppress(RuntimeError):
        log.shutdown()
    log.init(
        RuntimeConfig(
            service="tests",
            environment="ci",
            queue_enabled=False,
            enable_graylog=False,
            console_stream=ConsoleStream.NONE,
            payload_limits=PayloadLimits(extra_max_value_chars=1_000_000, stacktrace_max_frames=10_000),
        )
    )
    try:
        _caller_frame_marker()
        events = json.loads(log.dump(dump_format=DumpFormat.JSON))
    finally:
        log.shutdown()
    stack = events[-1]["stack_info"]
    assert isinstance(stack, str)
    return stack


def _caller_frame_marker() -> None:
    log.getLogger("tests.stack").warning("with stack", stack_info=True)


def test_native_stack_info_reads_like_stdlib_and_ends_at_the_caller() -> None:
    stack = _stack_of_native_call()

    assert stack.startswith("Stack (most recent call last):\n")
    assert stack.rstrip().endswith('log.getLogger("tests.stack").warning("with stack", stack_info=True)')
    assert "in _caller_frame_marker" in stack
    assert "_normalise_stack_info" not in stack
    assert "\n\n" not in stack
    assert not stack.endswith("\n")
