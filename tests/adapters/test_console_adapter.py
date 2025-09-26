from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lib_log_rich.adapters.console.rich_console import RichConsoleAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


def _event() -> LogEvent:
    context = LogContext(service="svc", environment="test", job_id="job")
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=context,
        extra={"foo": "bar"},
    )


def test_rich_console_adapter_renders_expected_line(record_console) -> None:
    adapter = RichConsoleAdapter(console=record_console)
    adapter.emit(_event(), colorize=True)
    output = record_console.export_text()
    assert "INFO" in output
    assert "foo=bar" in output
    assert "hello" in output


def test_rich_console_adapter_respects_no_color(record_console) -> None:
    adapter = RichConsoleAdapter(console=record_console, no_color=True)
    adapter.emit(_event(), colorize=True)
    output = record_console.export_text()
    assert "INFO" in output
    # When no_color is set, Rich will not inject ANSI sequences; the snapshot remains plain text.
    assert "[" not in output


@pytest.mark.parametrize("colorize", [True, False])
def test_rich_console_adapter_allows_color_flag(record_console, colorize: bool) -> None:
    adapter = RichConsoleAdapter(console=record_console)
    adapter.emit(_event(), colorize=colorize)
    output = record_console.export_text()
    assert "hello" in output
    assert "foo=bar" in output
