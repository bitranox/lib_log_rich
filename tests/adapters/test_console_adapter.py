from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from typing import Callable, cast

import pytest
from rich.console import Console

from lib_log_rich.adapters.console import rich_console as console_module
from lib_log_rich.adapters.console.rich_console import RichConsoleAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC, POSIX_ONLY, WINDOWS_ONLY

pytestmark = [OS_AGNOSTIC]


def _make_event(*, message: str = "hello") -> LogEvent:
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message=message,
        context=LogContext(service="svc", environment="test", job_id="job"),
        extra={"mood": "bright"},
    )


def _make_adapter(
    *,
    console: Console | None = None,
    force_color: bool = False,
    no_color: bool = False,
    styles: dict[str, str] | None = None,
    preset: str | None = None,
    template: str | None = None,
) -> tuple[RichConsoleAdapter, Console]:
    live_console = console or Console(record=True, force_terminal=True, width=120)
    adapter = RichConsoleAdapter(
        console=live_console,
        force_color=force_color,
        no_color=no_color,
        styles=styles,
        format_preset=preset,
        format_template=template,
    )
    return adapter, live_console


def test_console_line_carries_the_message_when_color_sleeps() -> None:
    adapter, console = _make_adapter()
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True)
    assert "hello" in text


def test_console_line_wraps_extra_metadata_like_a_trailing_ribbon() -> None:
    adapter, console = _make_adapter(preset="full")
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True)
    assert "mood=bright" in text


def test_console_styles_can_be_repainted_with_magenta_whispers() -> None:
    adapter, console = _make_adapter(styles={"INFO": "magenta"})
    adapter.emit(_make_event(), colorize=True)
    rainbow = console.export_text(clear=True, styles=True)
    assert "\x1b[35m" in rainbow


def test_console_switches_off_colour_when_no_colour_is_requested() -> None:
    adapter, console = _make_adapter(no_color=True)
    adapter.emit(_make_event(), colorize=True)
    text = console.export_text(clear=True, styles=True)
    assert "\x1b[" not in text


@POSIX_ONLY
def test_console_sings_with_truecolour_on_posix_terminals(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = Console(record=True, force_terminal=True)

    def build_console(*args: object, **kwargs: object) -> Console:
        assert kwargs.get("force_terminal") is True
        return recorded

    monkeypatch.setattr(console_module, "Console", build_console)
    adapter = RichConsoleAdapter(force_color=True)
    adapter.emit(_make_event(), colorize=True)
    palette = recorded.export_text(clear=True, styles=True)
    assert "\x1b[" in palette


@WINDOWS_ONLY
def test_console_forces_ansi_colour_on_windows_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded = Console(record=True, force_terminal=True)

    def build_console(*args: object, **kwargs: object) -> Console:
        return recorded

    monkeypatch.setattr(console_module, "Console", build_console)
    adapter = RichConsoleAdapter(force_color=True)
    adapter.emit(_make_event(), colorize=True)
    palette = recorded.export_text(clear=True, styles=True)
    assert "\x1b[" in palette


def test_console_short_preset_begins_with_the_clock_strike() -> None:
    adapter, console = _make_adapter(preset="short")
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True).splitlines()[0]
    assert text.startswith("[12:00:00][INFO ℹ][tests]:")


def test_console_short_preset_refuses_to_echo_extras() -> None:
    adapter, console = _make_adapter(preset="short")
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True)
    assert "mood=bright" not in text


def test_console_custom_template_wraps_the_message_in_a_chosen_song() -> None:
    adapter, console = _make_adapter(template="{hh}:{mm}:{ss} {message}")
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True).strip()
    assert text.startswith("12:00:00 hello")


def test_console_unknown_preset_is_rejected_with_a_clear_voice() -> None:
    with pytest.raises(ValueError):
        _make_adapter(preset="mystery")


def test_console_falls_back_to_full_template_when_custom_placeholders_break() -> None:
    adapter, console = _make_adapter(template="{missing}")
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True)
    assert "tests — hello" in text


def test_console_raise_when_full_template_itself_shatters(monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenPayload:
        """Mock payload that returns incomplete dict from to_dict()."""

        def to_dict(self) -> dict[str, object]:
            return {"message": "only"}

    def broken_payload(_: LogEvent) -> BrokenPayload:
        return BrokenPayload()

    monkeypatch.setattr(console_module, "build_format_payload", broken_payload)
    adapter = RichConsoleAdapter(format_preset="full")
    with pytest.raises(KeyError):
        adapter.emit(_make_event(), colorize=False)


def test_console_local_template_uses_local_clock_face() -> None:
    adapter, console = _make_adapter(preset="short_loc")
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True).splitlines()[0]
    # short_loc format: [HH:MM:SS][level_code]: message
    assert "][INFO]:" in text


def test_console_stream_custom_writes_to_target() -> None:
    buffer = StringIO()
    adapter = RichConsoleAdapter(stream="custom", stream_target=buffer, format_template="{message}")
    adapter.emit(_make_event(), colorize=False)
    assert "hello" in buffer.getvalue()


def test_console_stream_both_uses_tee(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object | None] = {"file": None, "stderr": None}
    original_console = console_module.Console

    def build_console(*args: object, **kwargs: object) -> Console:
        captured["file"] = kwargs.get("file")
        captured["stderr"] = kwargs.get("stderr")
        return original_console(*args, **kwargs)

    monkeypatch.setattr(console_module, "Console", build_console)
    adapter = RichConsoleAdapter(stream="both")
    adapter.emit(_make_event(), colorize=False)

    file_obj = captured["file"]
    assert file_obj is not None
    assert file_obj.__class__.__name__ == "_ConsoleStreamTee"
    assert captured["stderr"] is None


def test_console_stream_none_uses_muted_tee(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object | None] = {"file": None}
    original_console = console_module.Console

    def build_console(*args: object, **kwargs: object) -> Console:
        captured["file"] = kwargs.get("file")
        return original_console(*args, **kwargs)

    monkeypatch.setattr(console_module, "Console", build_console)
    adapter = RichConsoleAdapter(stream="none", format_template="{message}")
    adapter.emit(_make_event(), colorize=False)

    mute_stream = captured["file"]
    assert mute_stream is not None
    assert mute_stream.__class__.__name__ == "_ConsoleStreamTee"
    writer = cast(Callable[[str], int], getattr(mute_stream, "write"))
    assert writer("check") == len("check")
