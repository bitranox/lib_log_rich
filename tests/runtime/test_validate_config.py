"""validate_config judges a configuration exactly as init() would, without starting a runtime.

The contract under test is equivalence: every configuration init() refuses,
validate_config refuses with the same message, and every configuration init()
accepts, validate_config accepts. A caller can then check a config while a
runtime is running without shutting it down to find out.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

import lib_log_rich
from lib_log_rich.domain.enums import ConsoleStream, FormatPreset
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.runtime import (
    ConsoleAppearance,
    RichConsoleAdapter,
    RuntimeConfig,
    build_runtime_settings,
    init,
    inspect_runtime,
    is_initialised,
    shutdown,
    validate_config,
)
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Iterator

    from lib_log_rich.application.ports import ConsolePort

pytestmark = [OS_AGNOSTIC]


def _config(**overrides: Any) -> RuntimeConfig:
    base: dict[str, Any] = {
        "service": "svc",
        "environment": "test",
        "queue_enabled": False,
        "console_stream": "none",
    }
    base.update(overrides)
    return RuntimeConfig(**base)


def _silent_console(_appearance: ConsoleAppearance) -> ConsolePort:
    return RichConsoleAdapter(stream=ConsoleStream.NONE)


# (case id, RuntimeConfig overrides, environment overrides)
REFUSED: list[tuple[str, dict[str, Any], dict[str, str]]] = [
    ("console_level", {"console_level": "bogus"}, {}),
    ("backend_level", {"backend_level": "bogus"}, {}),
    ("graylog_level", {"graylog_level": "bogus"}, {}),
    ("LOG_CONSOLE_LEVEL", {}, {"LOG_CONSOLE_LEVEL": "bogus"}),
    ("scrub_patterns", {"scrub_patterns": {"token": "("}}, {}),
    ("LOG_SCRUB_PATTERNS", {}, {"LOG_SCRUB_PATTERNS": "token=("}),
    ("LOG_CONSOLE_FORMAT_PRESET", {}, {"LOG_CONSOLE_FORMAT_PRESET": "nope"}),
    ("LOG_DUMP_FORMAT_PRESET", {}, {"LOG_DUMP_FORMAT_PRESET": "nope"}),
    ("console-only dump_format_preset", {"dump_format_preset": "short_loc_icon"}, {}),
    ("console-only LOG_DUMP_FORMAT_PRESET", {}, {"LOG_DUMP_FORMAT_PRESET": "short_loc_icon"}),
    ("LOG_CONSOLE_STREAM", {}, {"LOG_CONSOLE_STREAM": "printer"}),
    ("LOG_GRAYLOG_PROTOCOL", {}, {"LOG_GRAYLOG_PROTOCOL": "smtp"}),
    ("console_styles", {"console_styles": {"NOTALEVEL": "red"}}, {}),
    ("LOG_CONSOLE_STYLES", {}, {"LOG_CONSOLE_STYLES": "NOTALEVEL=red"}),
    (
        "graylog TLS over UDP",
        {"enable_graylog": True, "graylog_endpoint": ("graylog.example.com", 12201), "graylog_protocol": "udp", "graylog_tls": True},
        {},
    ),
    (
        "LOG_GRAYLOG_PROTOCOL with TLS",
        {"enable_graylog": True, "graylog_endpoint": ("graylog.example.com", 12201), "graylog_tls": True},
        {"LOG_GRAYLOG_PROTOCOL": "udp"},
    ),
    ("graylog enabled with no endpoint", {"enable_graylog": True}, {}),
    ("LOG_ENABLE_GRAYLOG with no endpoint", {}, {"LOG_ENABLE_GRAYLOG": "1"}),
    ("TLS over UDP with no endpoint", {"enable_graylog": True, "graylog_protocol": "udp", "graylog_tls": True}, {}),
]

ACCEPTED: list[tuple[str, dict[str, Any], dict[str, str]]] = [
    ("defaults", {}, {}),
    ("level names in any case", {"console_level": "debug", "backend_level": "Error"}, {}),
    ("a template beside a preset", {"console_format_preset": "short", "console_format_template": "{message}"}, {}),
    (
        "category names in any case",
        {"console_format_preset": "SHORT_LOC", "dump_format_preset": "Full", "queue_full_policy": "DROP", "graylog_protocol": "UDP"},
        {},
    ),
    (
        "a custom console factory owns the style keys",
        {"console_styles": {"NOTALEVEL": "red"}, "console_adapter_factory": _silent_console},
        {},
    ),
    ("a blank scrub key is skipped", {"scrub_patterns": {"   ": "("}}, {}),
    ("the icon preset on the console", {"console_format_preset": "short_loc_icon"}, {"LOG_CONSOLE_FORMAT_PRESET": "short_loc_icon"}),
    ("level members and names mixed as style keys", {"console_styles": {LogLevel.INFO: "green", "error": "red"}}, {}),
    ("TLS over UDP while Graylog is disabled", {"graylog_protocol": "udp", "graylog_tls": True}, {}),
    ("an endpoint from LOG_GRAYLOG_ENDPOINT enables Graylog", {"enable_graylog": True}, {"LOG_GRAYLOG_ENDPOINT": "graylog.example.com:12201"}),
]


@pytest.fixture(autouse=True)
def no_ambient_log_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every case states the environment it needs; an inherited LOG_* variable (a
    # developer's .env, a CI secret) would silently change what init() is judging.
    for name in [name for name in os.environ if name.startswith("LOG_")]:
        monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def no_runtime_left_behind() -> Iterator[None]:
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            shutdown()


def _apply_env(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _init_refusal(config: RuntimeConfig) -> str:
    with pytest.raises(ValueError) as caught:
        init(config)
    return str(caught.value)


@pytest.mark.parametrize(("overrides", "env"), [pytest.param(o, e, id=i) for i, o, e in REFUSED])
def test_build_runtime_settings_refuses_what_init_refuses(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], env: dict[str, str]) -> None:
    _apply_env(monkeypatch, env)
    config = _config(**overrides)
    _init_refusal(config)

    with pytest.raises(ValueError):
        build_runtime_settings(config=config)


@pytest.mark.parametrize(("overrides", "env"), [pytest.param(o, e, id=i) for i, o, e in REFUSED])
def test_validate_config_reports_exactly_what_init_reports(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], env: dict[str, str]) -> None:
    _apply_env(monkeypatch, env)
    config = _config(**overrides)
    init_message = _init_refusal(config)

    with pytest.raises(ValueError) as caught:
        validate_config(config)

    assert str(caught.value) == init_message


@pytest.mark.parametrize(("overrides", "env"), [pytest.param(o, e, id=i) for i, o, e in ACCEPTED])
def test_validate_config_accepts_what_init_accepts(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], env: dict[str, str]) -> None:
    _apply_env(monkeypatch, env)
    config = _config(**overrides)

    validate_config(config)
    init(config)

    assert is_initialised() is True


def test_mixed_style_keys_resolve_to_the_levels_they_name() -> None:
    config = _config(console_styles={LogLevel.INFO: "green", "error": "red"})

    styles = build_runtime_settings(config=config).console.styles or {}

    assert {key: styles.get(key) for key in ("INFO", "ERROR")} == {"INFO": "green", "ERROR": "red"}


def test_validate_config_does_not_start_a_runtime() -> None:
    validate_config(_config())

    assert is_initialised() is False


def test_validate_config_leaves_a_running_runtime_alone() -> None:
    init(_config(service="running"))

    with pytest.raises(ValueError, match="Unknown log level"):
        validate_config(_config(service="candidate", console_level="bogus"))
    validate_config(_config(service="candidate"))

    assert inspect_runtime().service == "running"


def test_validate_config_is_part_of_the_package_facade() -> None:
    assert lib_log_rich.validate_config is validate_config
    assert "validate_config" in lib_log_rich.__all__


def test_resolved_levels_are_log_levels() -> None:
    settings = build_runtime_settings(config=_config(console_level="debug", backend_level="Error", graylog_level=LogLevel.CRITICAL))

    assert settings.console_level is LogLevel.DEBUG
    assert settings.backend_level is LogLevel.ERROR
    assert settings.graylog_level is LogLevel.CRITICAL


def test_refusal_names_the_field_and_hides_validation_internals() -> None:
    with pytest.raises(ValueError) as caught:
        validate_config(_config(console_level="bogus"))

    message = str(caught.value)
    assert message == "Invalid runtime settings: console_level: Unknown log level: 'bogus'"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({}, id="alone"),
        pytest.param({"console_format_template": "{message}"}, id="beside a template"),
        pytest.param({"console_adapter_factory": _silent_console}, id="with a custom console factory"),
    ],
)
def test_an_unknown_console_preset_is_refused_when_the_config_is_built(overrides: dict[str, Any]) -> None:
    # The preset is a FormatPreset, so no configuration can hold a name outside it,
    # whatever else would have made the preset unused.
    with pytest.raises(ValidationError, match="Invalid format preset: 'nope'"):
        _config(console_format_preset="nope", **overrides)


@pytest.mark.parametrize(
    ("overrides", "env", "source"),
    [
        pytest.param({"dump_format_preset": FormatPreset.SHORT_LOC_ICON}, {}, "dump_format_preset", id="config field"),
        pytest.param({}, {"LOG_DUMP_FORMAT_PRESET": "short_loc_icon"}, "LOG_DUMP_FORMAT_PRESET", id="environment"),
    ],
)
def test_a_console_only_dump_preset_is_refused_naming_its_source_and_the_dump_presets(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any], env: dict[str, str], source: str
) -> None:
    # Text dumps have no template for the icon preset, so before this refusal init()
    # accepted it and every later text dump raised "Unknown text dump preset".
    _apply_env(monkeypatch, env)

    with pytest.raises(ValueError) as caught:
        validate_config(_config(**overrides))

    assert str(caught.value) == (
        f"Invalid runtime settings: {source}: short_loc_icon is a console-only preset; a text dump takes one of: full, short, full_loc, short_loc"
    )


def test_an_unknown_console_theme_is_refused_when_the_config_is_built() -> None:
    # An unknown name used to be kept and then ignored, leaving the console unstyled.
    with pytest.raises(ValidationError, match="Invalid console theme: 'nope'"):
        _config(console_theme="nope")
