from __future__ import annotations

import sys
from types import MappingProxyType
from typing import Any, Mapping, Optional

import pytest

from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.enums import QueuePolicy
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES
from lib_log_rich.runtime._settings import (
    PayloadLimits,
    RuntimeConfig,
    build_runtime_settings,
    coerce_graylog_endpoint,
    coerce_rate_limit,
    env_bool,
    parse_console_styles,
    parse_scrub_patterns,
    resolve_console_palette,
    resolve_feature_flags,
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
        graylog_endpoint=None,
        graylog_level=LogLevel.ERROR,
        enable_ring_buffer=True,
        ring_buffer_size=5,
        enable_journald=True,
        enable_eventlog=True,
        enable_graylog=True,
        graylog_protocol="udp",
        graylog_tls=False,
        queue_enabled=True,
        queue_maxsize=4,
        queue_full_policy="block",
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
    assert settings.console_level == "DEBUG"
    assert settings.backend_level == "ERROR"
    assert settings.graylog_level == "CRITICAL"
    assert settings.ring_buffer_size == 12
    assert settings.queue_maxsize == 9
    assert settings.queue_full_policy == "drop"
    assert settings.queue_put_timeout == 0.1
    assert settings.queue_stop_timeout == 0.3
    assert settings.dump.format_preset == "short"
    assert settings.graylog.enabled is False
    assert settings.graylog.protocol == "tcp"
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
    config = RuntimeConfig(**base.model_dump() | {"ring_buffer_size": 0})
    with pytest.raises(ValueError, match="ring_buffer_size must be positive"):
        build_runtime_settings(config=config)


def test_build_runtime_settings_payload_limits_variants() -> None:
    base = _base_config()
    limits = PayloadLimits(message_max_chars=128)
    config = RuntimeConfig(**base.model_dump() | {"payload_limits": limits})
    settings = build_runtime_settings(config=config)
    assert settings.limits is limits

    config = RuntimeConfig(**base.model_dump() | {"payload_limits": {"message_max_chars": 256}})
    settings_mapping = build_runtime_settings(config=config)
    assert settings_mapping.limits.message_max_chars == 256

    proxy = RuntimeConfig(**base.model_dump() | {"payload_limits": MappingProxyType({"message_max_chars": 300})})
    settings_proxy = build_runtime_settings(config=proxy)
    assert settings_proxy.limits.message_max_chars == 300

    custom = RuntimeConfig(**base.model_dump() | {"payload_limits": _CustomMapping({"message_max_chars": 310})})
    settings_custom = build_runtime_settings(config=custom)
    assert settings_custom.limits.message_max_chars == 310


def test_build_runtime_settings_wraps_validation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _base_config()
    config = RuntimeConfig(**base.model_dump() | {"service": " "})
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
        theme="classic",
        explicit_styles={"INFO": "cyan"},
        env_styles={"ERROR": "red"},
    )
    assert theme == "classic"
    assert styles is not None
    assert styles["INFO"] == "cyan"
    assert styles["ERROR"] in {"red", "bold red", "bold red3"}


def test_resolve_console_palette_uses_env_when_no_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_THEME", "classic")
    theme, styles = resolve_console_palette(theme=None, explicit_styles=None, env_styles=None)
    assert theme == "classic"
    assert styles is not None
    assert styles["INFO"] == CONSOLE_STYLE_THEMES["classic"]["INFO"]


def test_resolve_console_palette_handles_unknown_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_CONSOLE_THEME", raising=False)
    theme, styles = resolve_console_palette(theme="unknown", explicit_styles=None, env_styles=None)
    assert theme == "unknown"
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
def test_env_bool_parsing(monkeypatch: pytest.MonkeyPatch, value: Optional[str], default: bool, expected: bool) -> None:
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
