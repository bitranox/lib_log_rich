from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

import lib_log_rich.demo as demo_module
from lib_log_rich.demo import logdemo
from lib_log_rich.domain import ConsoleTheme, DumpFormat
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [OS_AGNOSTIC]


@pytest.mark.parametrize(
    ("parameter", "value", "enum_name"),
    [
        ("theme", "classic", "ConsoleTheme"),
        ("theme", "unknown-theme", "ConsoleTheme"),
        ("console_format_preset", "full", "FormatPreset"),
        ("dump_format_preset", "full", "FormatPreset"),
    ],
)
def test_logdemo_refuses_a_plain_string_for_theme_and_presets(parameter: str, value: str, enum_name: str) -> None:
    with pytest.raises(TypeError, match=f"^{parameter} must be a member of {enum_name}, got '{value}'$"):
        logdemo(**{parameter: cast("Any", value)})


def test_demo_identity_falls_back_to_defaults() -> None:
    service, environment = cast("Any", demo_module)._demo_identity(None, None, ConsoleTheme.CLASSIC)
    assert (service, environment) == ("logdemo", "demo-classic")


def test_demo_graylog_endpoint_provides_default_when_enabled() -> None:
    assert cast("Any", demo_module)._demo_graylog_endpoint(enable_graylog=True, endpoint=None) == ("127.0.0.1", 12201)


def test_demo_render_dump_returns_none_without_format() -> None:
    payload = cast("Any", demo_module)._demo_render_dump(
        dump_format=None,
        dump_path=None,
        color=None,
        dump_format_preset=None,
        dump_format_template=None,
        theme=ConsoleTheme.CLASSIC,
        styles={},
    )
    assert payload is None


def test_logdemo_rejects_when_runtime_already_initialised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lib_log_rich.demo.is_initialised", lambda: True)
    with pytest.raises(RuntimeError, match="requires lib_log_rich to be uninitialised"):
        logdemo()


def test_logdemo_emits_events_for_classic_theme() -> None:
    result = logdemo(theme=ConsoleTheme.CLASSIC)
    assert len(result.events) == 5


def test_logdemo_writes_dump_to_target_path(tmp_path: Path) -> None:
    target = tmp_path / "demo.json"
    logdemo(theme=ConsoleTheme.CLASSIC, dump_format=DumpFormat.JSON, dump_path=target)
    assert target.exists()
