from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO
from typing import IO, cast

import pytest
from rich.console import Console

from lib_log_rich.adapters.console import rich_console as console_module
from lib_log_rich.adapters.console.rich_console import RichConsoleAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.enums import ConsoleStream, FormatPreset
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
    preset: FormatPreset | None = None,
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
    adapter, console = _make_adapter(preset=FormatPreset.FULL)
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
    adapter, console = _make_adapter(preset=FormatPreset.SHORT)
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True).splitlines()[0]
    assert text.startswith("[12:00:00][INFO ℹ][tests]:")


def test_console_short_preset_refuses_to_echo_extras() -> None:
    adapter, console = _make_adapter(preset=FormatPreset.SHORT)
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True)
    assert "mood=bright" not in text


def test_console_custom_template_wraps_the_message_in_a_chosen_song() -> None:
    adapter, console = _make_adapter(template="{hh}:{mm}:{ss} {message}")
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True).strip()
    assert text.startswith("12:00:00 hello")


@pytest.mark.parametrize("name", ["full", "mystery"])
def test_console_refuses_a_preset_given_as_a_plain_string(name: str) -> None:
    # A str-valued enum member hashes like its value, so "full" would still
    # find its template; the constructor must refuse it rather than half-work.
    with pytest.raises(TypeError, match="format_preset must be a member of FormatPreset"):
        RichConsoleAdapter(console=Console(file=StringIO()), format_preset=cast("FormatPreset", name))


@pytest.mark.parametrize("name", ["stderr", "custom"])
def test_console_refuses_a_stream_given_as_a_plain_string(name: str) -> None:
    with pytest.raises(TypeError, match="stream must be a member of ConsoleStream"):
        RichConsoleAdapter(stream=cast("ConsoleStream", name), stream_target=StringIO())


def test_console_module_no_longer_exports_preset_names() -> None:
    assert not hasattr(console_module, "CONSOLE_PRESETS")
    assert "CONSOLE_PRESETS" not in console_module.__all__


@pytest.mark.parametrize("preset", list(FormatPreset))
def test_console_renders_every_preset(preset: FormatPreset) -> None:
    adapter, console = _make_adapter(preset=preset)
    adapter.emit(_make_event(), colorize=False)
    assert "hello" in console.export_text(clear=True)


def test_console_falls_back_to_full_when_a_non_full_preset_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    real_payload = console_module.build_format_payload

    class PayloadWithoutClock:
        """Real payload minus ``hh``, which SHORT needs and FULL does not."""

        def __init__(self, event: LogEvent) -> None:
            self._fields = real_payload(event).to_dict()
            del self._fields["hh"]

        def to_dict(self) -> dict[str, object]:
            return self._fields

    monkeypatch.setattr(console_module, "build_format_payload", PayloadWithoutClock)
    adapter, console = _make_adapter(preset=FormatPreset.SHORT)
    adapter.emit(_make_event(), colorize=False)
    # FULL right-aligns the level before the logger name; SHORT brackets both.
    assert " INFO tests " in console.export_text(clear=True)


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
    adapter = RichConsoleAdapter(format_preset=FormatPreset.FULL)
    with pytest.raises(KeyError):
        adapter.emit(_make_event(), colorize=False)


def test_console_local_template_uses_local_clock_face() -> None:
    adapter, console = _make_adapter(preset=FormatPreset.SHORT_LOC)
    adapter.emit(_make_event(), colorize=False)
    text = console.export_text(clear=True).splitlines()[0]
    # short_loc format: [HH:MM:SS][level_code]: message
    assert "][INFO]:" in text


def test_console_stream_custom_writes_to_target() -> None:
    buffer = StringIO()
    adapter = RichConsoleAdapter(stream=ConsoleStream.CUSTOM, stream_target=buffer, format_template="{message}")
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
    adapter = RichConsoleAdapter(stream=ConsoleStream.BOTH)
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
    adapter = RichConsoleAdapter(stream=ConsoleStream.NONE, format_template="{message}")
    adapter.emit(_make_event(), colorize=False)

    mute_stream = captured["file"]
    assert mute_stream is not None
    assert mute_stream.__class__.__name__ == "_ConsoleStreamTee"
    stream = cast("IO[str]", mute_stream)
    assert stream.write("check") == len("check")
