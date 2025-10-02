from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typing import Any

from lib_log_rich.adapters.dump import DumpAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.dump_filter import DumpFilter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.ring_buffer import RingBuffer
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def build_event(
    index: int = 0,
    *,
    level: LogLevel = LogLevel.INFO,
    message: str | None = None,
    extra: dict[str, Any] | None = None,
) -> LogEvent:
    return LogEvent(
        event_id=f"evt-{index}",
        timestamp=datetime(2025, 9, 23, 12, index, tzinfo=timezone.utc),
        logger_name="tests",
        level=level,
        message=message or f"message-{index}",
        context=LogContext(
            service="svc",
            environment="test",
            job_id="job",
            process_id=10 + index,
            process_id_chain=(5, 10 + index),
        ),
        extra=extra or {},
    )


def build_ring_buffer() -> RingBuffer:
    buffer = RingBuffer(max_events=10)
    buffer.extend([build_event(0), build_event(1)])
    return buffer


def render_dump(
    events: list[LogEvent],
    *,
    dump_format: DumpFormat,
    path: Path | None = None,
    min_level: LogLevel | None = None,
    format_preset: str | None = None,
    format_template: str | None = None,
    text_template: str | None = None,
    colorize: bool = False,
    theme: str | None = None,
    console_styles: dict[str, str] | None = None,
    filters: DumpFilter | None = None,
) -> str:
    adapter = DumpAdapter()
    return adapter.dump(
        events,
        dump_format=dump_format,
        path=path,
        min_level=min_level,
        format_preset=format_preset,
        format_template=format_template,
        text_template=text_template,
        colorize=colorize,
        theme=theme,
        console_styles=console_styles,
        filters=filters,
    )


def test_text_dump_includes_message() -> None:
    payload = render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.TEXT)
    assert "message-0" in payload


def test_text_dump_includes_event_id() -> None:
    payload = render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.TEXT)
    assert "evt-1" in payload


def test_text_dump_respects_min_level() -> None:
    events = [build_event(level=LogLevel.INFO, message="info"), build_event(1, level=LogLevel.ERROR, message="error")]
    payload = render_dump(events, dump_format=DumpFormat.TEXT, min_level=LogLevel.ERROR, text_template="{level}:{message}:{event_id}")
    assert payload.splitlines() == ["ERROR:error:evt-1"]


def test_text_dump_respects_template_tokens() -> None:
    event = build_event(message="clock")
    payload = render_dump([event], dump_format=DumpFormat.TEXT, text_template="{YYYY}-{MM}-{DD}T{hh}:{mm}:{ss}")
    assert payload == "2025-09-23T12:00:00"


def test_text_dump_short_preset_prefixes_logger() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset="short")
    assert payload.startswith("12:00:00|INFO|tests:")


def test_text_dump_colorizes_when_requested() -> None:
    payload = render_dump([build_event(level=LogLevel.WARNING)], dump_format=DumpFormat.TEXT, colorize=True)
    assert "[" in payload


def test_json_dump_serializes_all_events() -> None:
    payload = render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.JSON)
    data = json.loads(payload)
    assert len(data) == 2


def test_json_dump_preserves_event_ids() -> None:
    payload = render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.JSON)
    data = json.loads(payload)
    assert data[0]["event_id"] == "evt-0"


def test_html_table_dump_returns_html_string(tmp_path: Path) -> None:
    target = tmp_path / "dump.html"
    payload = render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.HTML_TABLE, path=target)
    assert payload.startswith("<html")


def test_html_table_dump_writes_target_file(tmp_path: Path) -> None:
    target = tmp_path / "dump.html"
    render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.HTML_TABLE, path=target)
    assert target.exists()


def test_html_table_dump_includes_pid_chain(tmp_path: Path) -> None:
    target = tmp_path / "dump.html"
    render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.HTML_TABLE, path=target)
    html_text = target.read_text(encoding="utf-8")
    assert "PID Chain" in html_text


def test_html_table_dump_escapes_greater_than(tmp_path: Path) -> None:
    target = tmp_path / "dump.html"
    render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.HTML_TABLE, path=target)
    html_text = target.read_text(encoding="utf-8")
    assert "5&gt;10" in html_text


def test_html_txt_dump_colorizes_with_theme() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=True, theme="classic")
    assert "<span" in payload


def test_html_txt_dump_includes_message_when_colorized() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=True, theme="classic")
    assert "message-0" in payload


def test_html_txt_dump_respects_monochrome() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=False)
    assert "<span" not in payload


def test_html_txt_dump_includes_message_when_monochrome() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=False)
    assert "message-0" in payload


def test_short_loc_preset_contains_logger() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset="short_loc")
    assert "|tests:" in payload


def test_short_loc_preset_contains_separator() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset="short_loc")
    assert ":" in payload.splitlines()[0]


def test_full_loc_preset_contains_timestamp() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset="full_loc")
    assert "T" in payload


def test_full_loc_preset_contains_logger() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset="full_loc")
    assert "tests" in payload


def test_theme_placeholder_uses_extra_field() -> None:
    event = build_event(extra={"theme": "classic"})
    payload = render_dump([event], dump_format=DumpFormat.TEXT, format_template="{theme}")
    assert payload == "classic"
