from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib_log_rich.adapters.dump import DumpAdapter
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.ring_buffer import RingBuffer
from lib_log_rich.domain.context import LogContext


def _make_event(index: int) -> LogEvent:
    return LogEvent(
        event_id=f"evt-{index}",
        timestamp=datetime(2025, 9, 23, 12, index, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message=f"message-{index}",
        context=LogContext(
            service="svc",
            environment="test",
            job_id="job",
            process_id=10 + index,
            process_id_chain=(5, 10 + index),
        ),
    )


@pytest.fixture
def ring_buffer() -> RingBuffer:
    buffer = RingBuffer(max_events=10)
    buffer.append(_make_event(0))
    buffer.append(_make_event(1))
    return buffer


def test_dump_adapter_text_format(ring_buffer: RingBuffer) -> None:
    adapter = DumpAdapter()
    payload = adapter.dump(ring_buffer.snapshot(), dump_format=DumpFormat.TEXT)
    assert "message-0" in payload
    assert "evt-1" in payload


def test_dump_adapter_text_format_with_template_and_level_filter() -> None:
    adapter = DumpAdapter()
    events = [
        LogEvent(
            event_id="evt-info",
            timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
            logger_name="tests",
            level=LogLevel.INFO,
            message="info",
            context=LogContext(service="svc", environment="test", job_id="job"),
        ),
        LogEvent(
            event_id="evt-error",
            timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
            logger_name="tests",
            level=LogLevel.ERROR,
            message="error",
            context=LogContext(service="svc", environment="test", job_id="job"),
        ),
    ]
    payload = adapter.dump(
        events,
        dump_format=DumpFormat.TEXT,
        min_level=LogLevel.ERROR,
        text_template="{level}:{message}:{event_id}",
    )
    assert payload.splitlines() == ["ERROR:error:evt-error"]


def test_dump_adapter_text_template_date_components() -> None:
    adapter = DumpAdapter()
    event = LogEvent(
        event_id="evt",
        timestamp=datetime(2025, 9, 23, 4, 5, 6, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="clock",
        context=LogContext(service="svc", environment="test", job_id="job"),
    )
    payload = adapter.dump(
        [event],
        dump_format=DumpFormat.TEXT,
        text_template="{YYYY}-{MM}-{DD}T{hh}:{mm}:{ss}",
    )
    assert payload == "2025-09-23T04:05:06"


def test_dump_adapter_respects_short_preset() -> None:
    adapter = DumpAdapter()
    event = _make_event(0)
    payload = adapter.dump(
        [event],
        dump_format=DumpFormat.TEXT,
        format_preset="short",
    )
    assert payload.startswith("12:00:00|INFO|tests:")


def test_dump_adapter_text_colorize() -> None:
    adapter = DumpAdapter()
    events = [
        LogEvent(
            event_id="evt",
            timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
            logger_name="tests",
            level=LogLevel.WARNING,
            message="warn",
            context=LogContext(service="svc", environment="test", job_id="job"),
        )
    ]
    payload = adapter.dump(events, dump_format=DumpFormat.TEXT, colorize=True)
    assert "\033[" in payload


def test_dump_adapter_json_format(ring_buffer: RingBuffer) -> None:
    adapter = DumpAdapter()
    payload = adapter.dump(ring_buffer.snapshot(), dump_format=DumpFormat.JSON)
    data = json.loads(payload)
    assert len(data) == 2
    assert data[0]["event_id"] == "evt-0"


def test_dump_adapter_html_table_format_writes_file(ring_buffer: RingBuffer, tmp_path: Path) -> None:
    adapter = DumpAdapter()
    target = tmp_path / "dump.html"
    payload = adapter.dump(ring_buffer.snapshot(), dump_format=DumpFormat.HTML_TABLE, path=target)
    assert target.exists()
    html_text = target.read_text(encoding="utf-8")
    assert "<html" in html_text
    assert "PID Chain" in html_text
    assert "5&gt;10" in html_text  # chain marker
    assert payload.startswith("<html")


def test_dump_adapter_html_txt_colorized_theme() -> None:
    adapter = DumpAdapter()
    event = _make_event(0)
    payload = adapter.dump(
        [event],
        dump_format=DumpFormat.HTML_TXT,
        format_template="{message}",
        colorize=True,
        theme="classic",
    )
    assert "<span" in payload
    assert "message-0" in payload


def test_dump_adapter_html_txt_monochrome() -> None:
    adapter = DumpAdapter()
    event = _make_event(0)
    payload = adapter.dump(
        [event],
        dump_format=DumpFormat.HTML_TXT,
        format_template="{message}",
        colorize=False,
    )
    assert "<span" not in payload
    assert "message-0" in payload


def test_dump_adapter_short_loc_preset() -> None:
    adapter = DumpAdapter()
    event = _make_event(0)
    payload = adapter.dump([event], dump_format=DumpFormat.TEXT, format_preset="short_loc")
    assert "|tests:" in payload
    assert ":" in payload.splitlines()[0]


def test_dump_adapter_full_loc_preset() -> None:
    adapter = DumpAdapter()
    event = _make_event(0)
    payload = adapter.dump([event], dump_format=DumpFormat.TEXT, format_preset="full_loc")
    assert "T" in payload
    assert "tests" in payload


def test_dump_adapter_theme_placeholder() -> None:
    adapter = DumpAdapter()
    event = LogEvent(
        event_id="evt-theme",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="themed",
        context=LogContext(service="svc", environment="test", job_id="job"),
        extra={"theme": "classic"},
    )
    payload = adapter.dump([event], dump_format=DumpFormat.TEXT, format_template="{theme}")
    assert payload == "classic"


def test_dump_adapter_theme_palette_colorize() -> None:
    adapter = DumpAdapter()
    event = LogEvent(
        event_id="evt-theme",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="themed",
        context=LogContext(service="svc", environment="test", job_id="job"),
        extra={"theme": "dark"},
    )
    payload = adapter.dump([event], dump_format=DumpFormat.TEXT, colorize=True)
    assert "[" in payload
    assert "[97m" in payload  # bright_white from dark theme INFO


def test_dump_adapter_theme_argument_colorize() -> None:
    adapter = DumpAdapter()
    event = _make_event(0)
    payload = adapter.dump([event], dump_format=DumpFormat.TEXT, colorize=True, theme="classic")
    assert "\u001b[36m" in payload  # classic theme INFO is cyan
    assert "\u001b[32m" not in payload  # fallback INFO colour is green


def test_dump_adapter_console_styles_override_theme() -> None:
    adapter = DumpAdapter()
    event = _make_event(0)
    payload = adapter.dump(
        [event],
        dump_format=DumpFormat.TEXT,
        colorize=True,
        console_styles={LogLevel.INFO: "magenta"},
    )
    assert "\u001b[35m" in payload  # magenta override applied
