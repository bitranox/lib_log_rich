from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

import lib_log_rich.adapters.dump as dump_module
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


def test_json_dump_enriches_level_metadata() -> None:
    payload = render_dump([build_event(level=LogLevel.CRITICAL)], dump_format=DumpFormat.JSON)
    data = json.loads(payload)[0]
    assert data["level_name"] == "CRITICAL"
    assert data["level_value"] == LogLevel.CRITICAL.value
    assert data["level_code"] == LogLevel.CRITICAL.code
    assert data["level_icon"] == LogLevel.CRITICAL.icon
    assert data["context"]["process_id_chain"] == [5, 10]


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


def test_unknown_text_preset_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown text dump preset"):
        cast(Any, dump_module)._resolve_preset("unknown")


def test_render_text_returns_empty_string_for_no_events() -> None:
    assert cast(Any, DumpAdapter)._render_text([], template=None, colorize=False) == ""


def test_render_text_uses_theme_styles_when_colorized() -> None:
    event = build_event(extra={"theme": "classic"})
    payload = cast(Any, DumpAdapter)._render_text([event], template="{message}", colorize=True)
    assert "message-0" in payload


def test_render_text_handles_missing_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    event = build_event()

    class FakeCapture:
        def __enter__(self) -> "FakeCapture":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self) -> str:
            return "no-marker"

    class FakeConsole:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._capture = FakeCapture()

        def capture(self) -> FakeCapture:
            return self._capture

        def print(self, *_args: object, **_kwargs: object) -> None:
            return None

    monkeypatch.setattr("lib_log_rich.adapters.dump.Console", FakeConsole)
    payload = cast(Any, DumpAdapter)._render_text([event], template="{message}", colorize=True, console_styles={"INFO": "green"})
    assert payload == "message-0"


def test_render_html_text_returns_minimal_document_when_no_events() -> None:
    expected_html = "<html><head><title>lib_log_rich dump</title></head><body></body></html>"
    assert cast(Any, DumpAdapter)._render_html_text([], template=None, colorize=False) == expected_html


def test_render_html_table_handles_string_process_chain() -> None:
    event = build_event(extra={})

    class CustomContext:
        """Mock context with string process_id_chain for testing HTML escaping."""

        service = "svc"
        environment = "test"
        job_id = "job"
        process_id = 1
        process_id_chain = "root>child"
        user_name = None
        hostname = None
        request_id = None
        user_id = None
        trace_id = None
        span_id = None
        extra: dict[str, object] = {}

        def to_dict(self, *, include_none: bool = False) -> dict[str, object]:  # noqa: ARG002
            return {
                "timestamp": event.timestamp.isoformat(),
                "service": self.service,
                "environment": self.environment,
                "job_id": self.job_id,
                "logger_name": event.logger_name,
                "process_id": self.process_id,
                "process_id_chain": self.process_id_chain,
            }

    object.__setattr__(event, "context", CustomContext())
    html_table = cast(Any, DumpAdapter)._render_html_table([event])
    assert "root&gt;child" in html_table


def test_render_html_table_handles_missing_process_chain() -> None:
    event = build_event(extra={})

    class CustomContext:
        """Mock context with empty process_id_chain for testing omission."""

        service = "svc"
        environment = "test"
        job_id = "job"
        process_id = 1
        process_id_chain: tuple[int, ...] = ()
        user_name = None
        hostname = None
        request_id = None
        user_id = None
        trace_id = None
        span_id = None
        extra: dict[str, object] = {}

        def to_dict(self, *, include_none: bool = False) -> dict[str, object]:  # noqa: ARG002
            return {
                "timestamp": event.timestamp.isoformat(),
                "service": self.service,
                "environment": self.environment,
                "job_id": self.job_id,
                "process_id": self.process_id,
            }

    object.__setattr__(event, "context", CustomContext())
    html_table = cast(Any, DumpAdapter)._render_html_table([event])
    assert "PROCESS_ID_CHAIN" not in html_table


def test_theme_placeholder_uses_extra_field() -> None:
    event = build_event(extra={"theme": "classic"})
    payload = render_dump([event], dump_format=DumpFormat.TEXT, format_template="{theme}")
    assert payload == "classic"


def test_text_dump_uses_console_styles_overrides() -> None:
    event = build_event(level=LogLevel.INFO)
    payload = render_dump(
        [event],
        dump_format=DumpFormat.TEXT,
        colorize=True,
        console_styles={"INFO": "bold magenta"},
    )
    assert "[1;35m" in payload
    assert "[32m" not in payload


def test_text_dump_uses_theme_from_event_extra() -> None:
    event = build_event(level=LogLevel.INFO, extra={"theme": "classic"})
    payload = render_dump([event], dump_format=DumpFormat.TEXT, colorize=True)
    assert "[36m" in payload
    assert "[32m" not in payload


def test_html_text_dump_uses_console_styles() -> None:
    event = build_event(level=LogLevel.ERROR)
    payload = render_dump(
        [event],
        dump_format=DumpFormat.HTML_TXT,
        format_template="{message}",
        colorize=True,
        console_styles={"ERROR": "red"},
    )
    assert "color: #800000" in payload


def test_html_text_dump_falls_back_to_default_styles() -> None:
    event = build_event(level=LogLevel.INFO)
    payload = render_dump([event], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=True)
    assert "color: #008000" in payload


def test_dump_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "logs" / "dump.txt"
    render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.TEXT, path=target)
    assert target.exists()


def test_dump_propagates_write_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "dump.txt"
    original_write_text = Path.write_text

    def failing_write(
        self: Path,
        data: str,
        *,
        encoding: str = "utf-8",
        errors: str | None = None,
        newline: str | None = None,
    ) -> int:
        if self == target:
            raise OSError("disk full")
        return original_write_text(self, data, encoding=encoding, errors=errors, newline=newline)

    monkeypatch.setattr(Path, "write_text", failing_write)
    with pytest.raises(OSError, match="disk full"):
        render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.TEXT, path=target)
