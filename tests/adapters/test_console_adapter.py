from __future__ import annotations

from datetime import datetime, timezone

import pytest

from typing import Mapping

from rich.console import Console

from lib_log_rich.adapters.console.rich_console import RichConsoleAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def build_event(*, event_id: str = "evt-1", message: str = "hello", extra: dict[str, object] | None = None) -> LogEvent:
    context = LogContext(service="svc", environment="test", job_id="job")
    return LogEvent(
        event_id=event_id,
        timestamp=datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message=message,
        context=context,
        extra=extra or {"foo": "bar"},
    )


def render_event(
    console: Console,
    *,
    colorize: bool = True,
    force_color: bool = False,
    no_color: bool = False,
    styles: Mapping[str, str] | None = None,
    format_preset: str | None = None,
    format_template: str | None = None,
) -> str:
    adapter = RichConsoleAdapter(
        console=console,
        force_color=force_color,
        no_color=no_color,
        styles=styles,
        format_preset=format_preset,
        format_template=format_template,
    )
    adapter.emit(build_event(), colorize=colorize)
    return console.export_text()


def test_console_output_contains_level(record_console: Console) -> None:
    output = render_event(record_console)
    assert "INFO" in output


def test_console_output_contains_extra_fields(record_console: Console) -> None:
    output = render_event(record_console)
    assert "foo=bar" in output


def test_console_output_contains_message(record_console: Console) -> None:
    output = render_event(record_console)
    assert "hello" in output


def test_console_respects_no_color_flag(record_console: Console) -> None:
    output = render_event(record_console, no_color=True)
    assert "[" not in output


@pytest.mark.parametrize("colorize", [True, False])
def test_console_emits_message_for_both_color_paths(record_console: Console, colorize: bool) -> None:
    output = render_event(record_console, colorize=colorize)
    assert "hello" in output


def test_console_short_preset_prefixes_timestamp(record_console: Console) -> None:
    output = render_event(record_console, colorize=False, format_preset="short").strip()
    assert output.startswith("12:00:00|INFO|tests:")


def test_console_short_preset_hides_extra_fields(record_console: Console) -> None:
    output = render_event(record_console, colorize=False, format_preset="short")
    assert "foo=bar" not in output


def test_console_custom_template_includes_clock(record_console: Console) -> None:
    template = "{hh}:{mm}:{ss} {level_icon} {LEVEL} {message}"
    output = render_event(record_console, colorize=False, format_template=template).strip()
    assert output.startswith("12:00:00")


def test_console_custom_template_includes_message(record_console: Console) -> None:
    template = "{hh}:{mm}:{ss} {level_icon} {LEVEL} {message}"
    output = render_event(record_console, colorize=False, format_template=template).strip()
    assert "hello" in output


def test_console_full_preset_trims_microseconds(record_console: Console) -> None:
    micro_event = build_event(event_id="evt-2", message="micro", extra={})
    micro_event = micro_event.replace(timestamp=datetime(2025, 9, 23, 12, 0, 0, 987654, tzinfo=timezone.utc))
    adapter = RichConsoleAdapter(console=record_console)
    adapter.emit(micro_event, colorize=False)
    first_line = record_console.export_text().splitlines()[0]
    assert ".987654" not in first_line


def test_console_full_preset_emits_iso_timestamp(record_console: Console) -> None:
    micro_event = build_event(event_id="evt-2", message="micro", extra={})
    adapter = RichConsoleAdapter(console=record_console)
    adapter.emit(micro_event, colorize=False)
    first_line = record_console.export_text().splitlines()[0]
    assert first_line.startswith("2025-09-23T12:00:00 ")


def test_console_full_preset_trims_timezone_suffix(record_console: Console) -> None:
    micro_event = build_event(event_id="evt-2", message="micro", extra={})
    adapter = RichConsoleAdapter(console=record_console)
    adapter.emit(micro_event, colorize=False)
    first_line = record_console.export_text().splitlines()[0]
    assert "+00:00" not in first_line
