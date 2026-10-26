from __future__ import annotations

import sys
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import pytest

from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.enums import ConsoleStream, ConsoleTheme, FormatPreset, GraylogProtocol, QueuePolicy
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES
from lib_log_rich.runtime.settings import (
    ConsoleAppearance,
    PayloadLimits,
    RuntimeConfig,
    build_runtime_settings,
    coerce_graylog_endpoint,
    coerce_rate_limit,
    env_bool,
    parse_console_styles,
    parse_scrub_patterns,
    resolve_console,
    resolve_console_palette,
    resolve_dump_defaults,
    resolve_feature_flags,
    resolve_graylog,
    resolve_queue_maxsize,
    resolve_queue_policy,
    resolve_queue_stop_timeout,
    resolve_queue_timeout,
    resolve_scrub_patterns,
)
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


class _CustomMapping(Mapping[str, Any]):
    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def _base_config() -> RuntimeConfig:
    return RuntimeConfig(
        service="svc",
        environment="prod",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        graylog_endpoint=("graylog.example.com", 12201),
        graylog_level=LogLevel.ERROR,
        enable_ring_buffer=True,
        ring_buffer_size=5,
        enable_journald=True,
        enable_eventlog=True,
        enable_graylog=True,
        graylog_protocol=GraylogProtocol.UDP,
        graylog_tls=False,
        queue_enabled=True,
        queue_maxsize=4,
        queue_full_policy=QueuePolicy.BLOCK,
        queue_put_timeout=0.2,
        queue_stop_timeout=0.4,
        force_color=False,
        no_color=False,
        console_styles=None,
        console_theme=None,
        console_format_preset=None,
        console_format_template=None,
        scrub_patterns=None,
        dump_format_preset=None,
        dump_format_template=None,
        rate_limit=(10, 5.0),
        payload_limits=None,
        diagnostic_hook=None,
        console_adapter_factory=None,
    )


def test_build_runtime_settings_applies_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _base_config()
    overrides = {
        "LOG_SERVICE": "svc-env",
        "LOG_ENVIRONMENT": "prod-env",
        "LOG_CONSOLE_LEVEL": "DEBUG",
        "LOG_BACKEND_LEVEL": "ERROR",
        "LOG_GRAYLOG_LEVEL": "CRITICAL",
        "LOG_RING_BUFFER_SIZE": "12",
        "LOG_QUEUE_MAXSIZE": "9",
        "LOG_QUEUE_FULL_POLICY": "drop",
        "LOG_QUEUE_PUT_TIMEOUT": "0.1",
        "LOG_QUEUE_STOP_TIMEOUT": "0.3",
        "LOG_CONSOLE_STYLES": "INFO=green,ERROR=red",
        "LOG_CONSOLE_THEME": "classic",
        "LOG_DUMP_FORMAT_PRESET": "short",
        "LOG_ENABLE_GRAYLOG": "false",
        "LOG_GRAYLOG_PROTOCOL": "tcp",
        "LOG_GRAYLOG_TLS": "1",
        "LOG_GRAYLOG_ENDPOINT": "logs:12201",
        "LOG_RATE_LIMIT": "5:10",
        "LOG_SCRUB_PATTERNS": "apikey=.+",
    }
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)

    settings = build_runtime_settings(config=config)

    assert settings.service == "svc-env"
    assert settings.environment == "prod-env"
    assert settings.console_level is LogLevel.DEBUG
    assert settings.backend_level is LogLevel.ERROR
    assert settings.graylog_level is LogLevel.CRITICAL
    assert settings.ring_buffer_size == 12
    assert settings.queue_maxsize == 9
    assert settings.queue_full_policy is QueuePolicy.DROP
    assert settings.queue_put_timeout == 0.1
    assert settings.queue_stop_timeout == 0.3
    assert settings.dump.format_preset is FormatPreset.SHORT
    assert settings.graylog.enabled is False
    assert settings.graylog.protocol is GraylogProtocol.TCP
    assert settings.graylog.endpoint == ("logs", 12201)
    assert settings.rate_limit == (5, 10.0)
    assert settings.scrub_patterns["apikey"] == ".+"
    assert settings.console.styles is not None
    assert settings.console.styles["INFO"] == "green"


def test_build_runtime_settings_invalid_ring_buffer_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _base_config()
    monkeypatch.setenv("LOG_RING_BUFFER_SIZE", "not-a-number")
    with pytest.raises(ValueError, match="must be an integer"):
        build_runtime_settings(config=config)


def test_build_runtime_settings_ring_size_validation_without_env() -> None:
    base = _base_config()
    config = base.model_copy(update={"ring_buffer_size": 0})
    with pytest.raises(ValueError, match="ring_buffer_size must be positive"):
        build_runtime_settings(config=config)


def _with_payload_limits(base: RuntimeConfig, limits: object) -> RuntimeConfig:
    # model_copy(update=...) skips validation, so rebuild the config to exercise the parse.
    return RuntimeConfig.model_validate({**dict(base), "payload_limits": limits})


def test_build_runtime_settings_payload_limits_variants() -> None:
    base = _base_config()
    limits = PayloadLimits(message_max_chars=128)
    settings = build_runtime_settings(config=_with_payload_limits(base, limits))
    assert settings.limits is limits

    settings_mapping = build_runtime_settings(config=_with_payload_limits(base, {"message_max_chars": 256}))
    assert settings_mapping.limits.message_max_chars == 256

    settings_proxy = build_runtime_settings(config=_with_payload_limits(base, MappingProxyType({"message_max_chars": 300})))
    assert settings_proxy.limits.message_max_chars == 300

    settings_custom = build_runtime_settings(config=_with_payload_limits(base, _CustomMapping({"message_max_chars": 310})))
    assert settings_custom.limits.message_max_chars == 310

    assert build_runtime_settings(config=base).limits == PayloadLimits()


def test_build_runtime_settings_wraps_validation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _base_config()
    config = base.model_copy(update={"service": " "})
    with pytest.raises(ValueError, match="service must not be empty"):
        build_runtime_settings(config=config)


def test_resolve_feature_flags_respects_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_RING_BUFFER_ENABLED", "0")
    monkeypatch.setenv("LOG_ENABLE_JOURNALD", "1")
    monkeypatch.setenv("LOG_ENABLE_EVENTLOG", "1")
    monkeypatch.setenv("LOG_QUEUE_ENABLED", "1")
    monkeypatch.setattr(sys, "platform", "win32")

    flags = resolve_feature_flags(
        enable_ring_buffer=True,
        enable_journald=True,
        enable_eventlog=True,
        queue_enabled=False,
    )
    assert flags.queue is True
    assert flags.journald is False
    assert flags.eventlog is True


def test_resolve_console_palette_merges_styles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_THEME", "dark")
    theme, styles = resolve_console_palette(
        theme=ConsoleTheme.CLASSIC,
        explicit_styles={"INFO": "cyan"},
        env_styles={"ERROR": "red"},
    )
    assert theme is ConsoleTheme.CLASSIC
    assert styles is not None
    assert styles["INFO"] == "cyan"
    assert styles["ERROR"] in {"red", "bold red", "bold red3"}


def test_resolve_console_palette_uses_only_the_theme_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    # resolve_console applies LOG_CONSOLE_THEME before calling this; reading it
    # again here would let the variable override a caller that passed no theme.
    monkeypatch.setenv("LOG_CONSOLE_THEME", "classic")
    theme, styles = resolve_console_palette(theme=None, explicit_styles=None, env_styles=None)
    assert theme is None
    assert styles is None


@pytest.mark.parametrize(
    "value, default, expected",
    [
        ("1", False, True),
        ("false", True, False),
        ("", True, True),
        (None, False, False),
        ("maybe", True, True),
    ],
)
def test_env_bool_parsing(monkeypatch: pytest.MonkeyPatch, value: str | None, default: bool, expected: bool) -> None:
    key = "TEST_ENV_BOOL"
    if value is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, value)
    assert env_bool(key, default) is expected


def test_parse_console_styles_and_scrub_patterns_ignore_invalid_entries() -> None:
    assert parse_console_styles(None) is None
    assert parse_console_styles("INFO=green,,INVALID") == {"INFO": "green"}
    assert parse_console_styles(" =skip,DEBUG=blue") == {"DEBUG": "blue"}
    assert parse_scrub_patterns(None) is None
    assert parse_scrub_patterns("apikey=,foo") == {"apikey": ".+"}


def test_parse_console_styles_returns_none_when_only_invalid_entries() -> None:
    assert parse_console_styles("INVALID") is None


def test_parse_scrub_patterns_drops_blank_keys() -> None:
    assert parse_scrub_patterns("=secret, token=.+") == {"token": ".+"}


def test_coerce_graylog_endpoint_validation_errors() -> None:
    with pytest.raises(ValueError, match="HOST:PORT"):
        coerce_graylog_endpoint("invalid", None)

    with pytest.raises(ValueError, match="port must be an integer"):
        coerce_graylog_endpoint("host:abc", None)

    with pytest.raises(ValueError, match="port must be positive"):
        coerce_graylog_endpoint("host:0", None)

    assert coerce_graylog_endpoint(None, ("fallback", 12201)) == ("fallback", 12201)


def test_coerce_rate_limit_parsing_and_validation() -> None:
    assert coerce_rate_limit(None, (1, 2.0)) == (1, 2.0)
    assert coerce_rate_limit("5:10", None) == (5, 10.0)
    with pytest.raises(ValueError, match="must be MAX:WINDOW_SECONDS"):
        coerce_rate_limit("invalid", None)
    with pytest.raises(ValueError, match="numeric values"):
        coerce_rate_limit("five:ten", None)
    with pytest.raises(ValueError, match="must be positive"):
        coerce_rate_limit("0:1", None)


def test_resolve_queue_helpers_handle_invalid_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_QUEUE_MAXSIZE", raising=False)
    assert resolve_queue_maxsize(5) == 5
    monkeypatch.setenv("LOG_QUEUE_MAXSIZE", "-1")
    assert resolve_queue_maxsize(5) == 5
    monkeypatch.setenv("LOG_QUEUE_MAXSIZE", "not-int")
    assert resolve_queue_maxsize(7) == 7
    monkeypatch.setenv("LOG_QUEUE_MAXSIZE", "20")
    assert resolve_queue_maxsize(5) == 20

    monkeypatch.setenv("LOG_QUEUE_FULL_POLICY", "unknown")
    assert resolve_queue_policy(QueuePolicy.BLOCK) is QueuePolicy.BLOCK
    monkeypatch.setenv("LOG_QUEUE_FULL_POLICY", " drop ")
    assert resolve_queue_policy(QueuePolicy.BLOCK) is QueuePolicy.DROP

    monkeypatch.setenv("LOG_QUEUE_PUT_TIMEOUT", "invalid")
    assert resolve_queue_timeout(0.5) == 0.5
    monkeypatch.setenv("LOG_QUEUE_PUT_TIMEOUT", "-1")
    assert resolve_queue_timeout(0.5) is None

    monkeypatch.setenv("LOG_QUEUE_STOP_TIMEOUT", "invalid")
    assert resolve_queue_stop_timeout(0.5) == 0.5
    monkeypatch.setenv("LOG_QUEUE_STOP_TIMEOUT", "-2")
    assert resolve_queue_stop_timeout(0.5) is None
    monkeypatch.setenv("LOG_QUEUE_STOP_TIMEOUT", "1.5")
    assert resolve_queue_stop_timeout(0.5) == 1.5


def test_resolve_scrub_patterns_merges_custom_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", "session=.+")
    merged = resolve_scrub_patterns({"api": "abc"})
    assert merged["api"] == "abc"
    assert merged["session"] == ".+"


def _console(**overrides: Any) -> ConsoleAppearance:
    arguments: dict[str, Any] = {
        "force_color": False,
        "no_color": False,
        "console_theme": None,
        "console_styles": None,
        "console_format_preset": None,
        "console_format_template": None,
        "console_stream": ConsoleStream.STDERR,
        "console_stream_target": None,
    }
    arguments.update(overrides)
    return resolve_console(**arguments)


def test_resolve_console_keeps_the_configured_members(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_CONSOLE_FORMAT_PRESET", raising=False)
    monkeypatch.delenv("LOG_CONSOLE_STREAM", raising=False)

    appearance = _console(console_format_preset=FormatPreset.SHORT, console_stream=ConsoleStream.STDOUT)

    assert appearance.format_preset is FormatPreset.SHORT
    assert appearance.stream is ConsoleStream.STDOUT


def test_resolve_console_parses_the_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_FORMAT_PRESET", " Full_Loc ")
    monkeypatch.setenv("LOG_CONSOLE_STREAM", "NONE")

    appearance = _console(console_format_preset=FormatPreset.SHORT)

    assert appearance.format_preset is FormatPreset.FULL_LOC
    assert appearance.stream is ConsoleStream.NONE


def test_resolve_dump_defaults_falls_back_to_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_DUMP_FORMAT_PRESET", raising=False)

    assert resolve_dump_defaults(dump_format_preset=None, dump_format_template=None).format_preset is FormatPreset.FULL


def test_resolve_graylog_keeps_the_configured_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_GRAYLOG_PROTOCOL", raising=False)

    settings = resolve_graylog(
        enable_graylog=False, graylog_endpoint=None, graylog_protocol=GraylogProtocol.UDP, graylog_tls=False, graylog_level=LogLevel.ERROR
    )

    assert settings.protocol is GraylogProtocol.UDP


@pytest.mark.parametrize(
    ("variable", "reason"),
    [
        ("LOG_CONSOLE_FORMAT_PRESET", "Invalid format preset: 'bogus'"),
        ("LOG_CONSOLE_STREAM", "Invalid console stream: 'bogus'"),
        ("LOG_CONSOLE_THEME", "Invalid console theme: 'bogus'; must be one of 'classic', 'dark', 'neon', 'pastel'"),
        ("LOG_DUMP_FORMAT_PRESET", "Invalid format preset: 'bogus'"),
        ("LOG_GRAYLOG_PROTOCOL", "Invalid Graylog protocol: 'bogus'"),
    ],
)
def test_an_unknown_environment_choice_is_refused_naming_the_variable(monkeypatch: pytest.MonkeyPatch, variable: str, reason: str) -> None:
    monkeypatch.setenv(variable, "bogus")

    with pytest.raises(ValueError, match=f"^{variable}: {reason}"):
        build_runtime_settings(config=_base_config())


def test_an_empty_environment_choice_keeps_the_configured_value(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("LOG_CONSOLE_FORMAT_PRESET", "LOG_CONSOLE_STREAM", "LOG_CONSOLE_THEME", "LOG_DUMP_FORMAT_PRESET", "LOG_GRAYLOG_PROTOCOL"):
        monkeypatch.setenv(variable, "")

    settings = build_runtime_settings(config=_base_config())

    assert settings.graylog.protocol is GraylogProtocol.UDP
    assert settings.console.stream is ConsoleStream.STDERR
    assert settings.console.format_preset is None
    assert settings.dump.format_preset is FormatPreset.FULL


_CONSOLE_STYLE_VARIABLES = ("LOG_CONSOLE_STYLES", "LOG_CONSOLE_THEME")


@pytest.fixture
def no_console_style_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for variable in _CONSOLE_STYLE_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return monkeypatch


def _console_styles_from(
    *,
    styles: Mapping[str | LogLevel, str] | None = None,
    theme: ConsoleTheme | None = None,
) -> dict[str, str] | None:
    config = RuntimeConfig(service="svc", environment="prod", console_styles=styles, console_theme=theme)
    return build_runtime_settings(config=config).console.styles


def test_config_style_keys_reach_the_console_as_level_names(no_console_style_env: pytest.MonkeyPatch) -> None:
    styles = _console_styles_from(styles={" info ": "cyan", "Warning ": "yellow", LogLevel.ERROR: "red"})

    assert styles == {"INFO": "cyan", "WARNING": "yellow", "ERROR": "red"}


def test_a_config_style_key_in_any_case_replaces_the_theme_default(no_console_style_env: pytest.MonkeyPatch) -> None:
    styles = _console_styles_from(styles={" info ": "green"}, theme=ConsoleTheme.CLASSIC)

    assert styles == {**CONSOLE_STYLE_THEMES[ConsoleTheme.CLASSIC], "INFO": "green"}


def test_env_style_keys_reach_the_console_as_level_names(no_console_style_env: pytest.MonkeyPatch) -> None:
    no_console_style_env.setenv("LOG_CONSOLE_STYLES", " info = cyan , Error=red")

    assert _console_styles_from() == {"INFO": "cyan", "ERROR": "red"}


def test_an_env_style_key_replaces_a_config_key_spelled_differently(no_console_style_env: pytest.MonkeyPatch) -> None:
    no_console_style_env.setenv("LOG_CONSOLE_STYLES", "INFO=cyan")

    assert _console_styles_from(styles={"info ": "blue"}) == {"INFO": "cyan"}


def test_theme_defaults_reach_the_console_as_level_names(no_console_style_env: pytest.MonkeyPatch) -> None:
    assert _console_styles_from(theme=ConsoleTheme.CLASSIC) == CONSOLE_STYLE_THEMES[ConsoleTheme.CLASSIC]


def test_builtin_palettes_are_keyed_by_level_names() -> None:
    # The resolver merges palette defaults without re-normalising their keys, so a
    # palette keyed in any other spelling would sit beside the configured style.
    level_names = {level.name for level in LogLevel}
    for palette in CONSOLE_STYLE_THEMES.values():
        assert set(palette) <= level_names


def test_the_env_theme_reaches_the_console(no_console_style_env: pytest.MonkeyPatch) -> None:
    no_console_style_env.setenv("LOG_CONSOLE_THEME", "neon")
    config = RuntimeConfig(service="svc", environment="prod", console_theme=ConsoleTheme.DARK)

    console = build_runtime_settings(config=config).console

    assert console.theme is ConsoleTheme.NEON
    assert console.styles == CONSOLE_STYLE_THEMES[ConsoleTheme.NEON]


def test_the_env_theme_is_parsed_in_any_case(no_console_style_env: pytest.MonkeyPatch) -> None:
    no_console_style_env.setenv("LOG_CONSOLE_THEME", " Pastel ")

    assert build_runtime_settings(config=RuntimeConfig(service="svc", environment="prod")).console.theme is ConsoleTheme.PASTEL


def test_every_theme_has_exactly_one_palette() -> None:
    # The palettes are keyed by the enum, but a dict literal cannot make a type
    # checker prove that it covers every member.
    assert set(CONSOLE_STYLE_THEMES) == set(ConsoleTheme)
