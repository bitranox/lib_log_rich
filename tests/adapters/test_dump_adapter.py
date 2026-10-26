from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pytest

from lib_log_rich.adapters.dump import DumpAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.enums import ConsoleTheme, FormatPreset
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
    format_preset: FormatPreset | None = None,
    format_template: str | None = None,
    colorize: bool = False,
    theme: ConsoleTheme | None = None,
    console_styles: dict[str, str] | None = None,
) -> str:
    adapter = DumpAdapter()
    return adapter.dump(
        events,
        dump_format=dump_format,
        path=path,
        min_level=min_level,
        format_preset=format_preset,
        format_template=format_template,
        colorize=colorize,
        theme=theme,
        console_styles=console_styles,
    )


def test_text_dump_includes_message() -> None:
    payload = render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.TEXT)
    assert "message-0" in payload


def test_text_dump_includes_event_id() -> None:
    payload = render_dump(build_ring_buffer().snapshot(), dump_format=DumpFormat.TEXT)
    assert "evt-1" in payload


def test_text_dump_respects_min_level() -> None:
    events = [build_event(level=LogLevel.INFO, message="info"), build_event(1, level=LogLevel.ERROR, message="error")]
    payload = render_dump(events, dump_format=DumpFormat.TEXT, min_level=LogLevel.ERROR, format_template="{level}:{message}:{event_id}")
    assert payload.splitlines() == ["ERROR:error:evt-1"]


def test_text_dump_respects_template_tokens() -> None:
    event = build_event(message="clock")
    payload = render_dump([event], dump_format=DumpFormat.TEXT, format_template="{YYYY}-{MM}-{DD}T{hh}:{mm}:{ss}")
    assert payload == "2025-09-23T12:00:00"


def test_text_dump_short_preset_prefixes_logger() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=FormatPreset.SHORT)
    assert payload.startswith("12:00:00|INFO|tests:")


def test_text_dump_colorizes_when_requested() -> None:
    payload = render_dump([build_event(level=LogLevel.WARNING)], dump_format=DumpFormat.TEXT, colorize=True)
    assert "\x1b[" in payload


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
    payload = render_dump([build_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=True, theme=ConsoleTheme.CLASSIC)
    assert "<span" in payload


def test_html_txt_dump_includes_message_when_colorized() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=True, theme=ConsoleTheme.CLASSIC)
    assert "message-0" in payload


def test_html_txt_dump_respects_monochrome() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=False)
    assert "<span" not in payload


def test_html_txt_dump_includes_message_when_monochrome() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=False)
    assert "message-0" in payload


def test_short_loc_preset_contains_logger() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=FormatPreset.SHORT_LOC)
    assert "|tests:" in payload


def test_short_loc_preset_contains_separator() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=FormatPreset.SHORT_LOC)
    assert ":" in payload.splitlines()[0]


@pytest.mark.parametrize("preset", list(FormatPreset), ids=lambda preset: preset.value)
def test_a_text_dump_renders_exactly_the_presets_the_domain_says_it_can(preset: FormatPreset) -> None:
    # Settings resolution refuses a dump preset by FormatPreset.renders_in_text_dump;
    # this keeps that rule and the adapter's template table from drifting apart.
    if preset.renders_in_text_dump:
        assert "message-0" in render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=preset)
    else:
        with pytest.raises(ValueError, match="Unknown text dump preset"):
            render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=preset)


def test_full_loc_preset_contains_timestamp() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=FormatPreset.FULL_LOC)
    assert "T" in payload


def test_full_loc_preset_contains_logger() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=FormatPreset.FULL_LOC)
    assert "tests" in payload


def test_short_loc_icon_preset_is_refused_by_text_dumps() -> None:
    with pytest.raises(ValueError, match="Unknown text dump preset: 'short_loc_icon'"):
        render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=FormatPreset.SHORT_LOC_ICON)


def test_an_untyped_unknown_preset_string_still_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown text dump preset: 'bogus'"):
        render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=cast("Any", "bogus"))


@pytest.mark.parametrize("preset", [preset for preset in FormatPreset if preset is not FormatPreset.SHORT_LOC_ICON])
def test_every_other_preset_renders_a_text_dump(preset: FormatPreset) -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=preset)
    assert "message-0" in payload


def test_an_explicit_template_wins_over_the_preset() -> None:
    payload = render_dump([build_event()], dump_format=DumpFormat.TEXT, format_preset=FormatPreset.FULL, format_template="{message}")
    assert payload == "message-0"


def test_dump_no_longer_accepts_text_template() -> None:
    adapter = cast("Any", DumpAdapter())
    with pytest.raises(TypeError, match="text_template"):
        adapter.dump([build_event()], dump_format=DumpFormat.TEXT, text_template="{message}")


def test_render_text_returns_empty_string_for_no_events() -> None:
    assert cast("Any", DumpAdapter)._render_text([], template=None, colorize=False) == ""


def test_render_text_uses_theme_styles_when_colorized() -> None:
    event = build_event(extra={"theme": "classic"})
    payload = cast("Any", DumpAdapter)._render_text([event], template="{message}", colorize=True)
    assert "message-0" in payload


def test_render_text_handles_missing_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    event = build_event()

    class FakeCapture:
        def __enter__(self) -> FakeCapture:
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
    payload = cast("Any", DumpAdapter)._render_text([event], template="{message}", colorize=True, console_styles={"INFO": "green"})
    assert payload == "message-0"


def test_render_html_text_returns_minimal_document_when_no_events() -> None:
    expected_html = "<html><head><title>lib_log_rich dump</title></head><body></body></html>"
    assert cast("Any", DumpAdapter)._render_html_text([], template=None, colorize=False) == expected_html


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
    assert "\x1b[1;35m" in payload
    assert "\x1b[32m" not in payload


def test_text_dump_uses_theme_from_event_extra() -> None:
    event = build_event(level=LogLevel.INFO, extra={"theme": "classic"})
    payload = render_dump([event], dump_format=DumpFormat.TEXT, colorize=True)
    assert "\x1b[36m" in payload
    assert "\x1b[32m" not in payload


def test_an_event_theme_extra_in_any_case_picks_that_palette() -> None:
    event = build_event(level=LogLevel.INFO, extra={"theme": " Classic "})
    payload = render_dump([event], dump_format=DumpFormat.TEXT, colorize=True, theme=ConsoleTheme.NEON)
    assert "\x1b[36m" in payload  # classic INFO is cyan


@pytest.mark.parametrize("extra_theme", ["nope", 7])
def test_an_event_theme_extra_that_names_no_palette_keeps_the_dump_theme(extra_theme: object) -> None:
    event = build_event(level=LogLevel.INFO, extra={"theme": extra_theme})
    payload = render_dump([event], dump_format=DumpFormat.TEXT, colorize=True, theme=ConsoleTheme.CLASSIC)
    assert "\x1b[36m" in payload


def test_an_html_event_theme_extra_that_names_no_palette_keeps_the_dump_theme() -> None:
    plain = render_dump(
        [build_event(level=LogLevel.INFO)], dump_format=DumpFormat.HTML_TXT, format_template="{message}", colorize=True, theme=ConsoleTheme.CLASSIC
    )
    odd = render_dump(
        [build_event(level=LogLevel.INFO, extra={"theme": "nope"})],
        dump_format=DumpFormat.HTML_TXT,
        format_template="{message}",
        colorize=True,
        theme=ConsoleTheme.CLASSIC,
    )
    assert odd == plain


def test_an_untyped_unknown_dump_theme_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown console theme: 'nope'"):
        render_dump([build_event()], dump_format=DumpFormat.TEXT, colorize=True, theme=cast("Any", "nope"))


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


def test_html_table_dump_renders_an_empty_process_chain_as_an_empty_cell() -> None:
    """An event without a PID chain closes its row with an empty chain cell."""
    event = LogEvent(
        event_id="evt-empty",
        timestamp=datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="no chain",
        context=LogContext(service="svc", environment="test", job_id="job"),
    )
    payload = render_dump([event], dump_format=DumpFormat.HTML_TABLE)
    assert "<td>no chain</td><td></td><td></td><td></td><td></td></tr>" in payload


def test_html_table_dump_joins_the_process_chain_with_escaped_arrows() -> None:
    """A multi-PID chain renders as one escaped ``a>b>c`` cell."""
    event = LogEvent(
        event_id="evt-chain",
        timestamp=datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="chained",
        context=LogContext(service="svc", environment="test", job_id="job", process_id=3, process_id_chain=(1, 2, 3)),
    )
    payload = render_dump([event], dump_format=DumpFormat.HTML_TABLE)
    assert "<td>3</td><td>1&gt;2&gt;3</td></tr>" in payload
