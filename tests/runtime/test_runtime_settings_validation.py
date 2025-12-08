from __future__ import annotations

import importlib
import sys
from typing import Any, cast

import pytest

from lib_log_rich.domain.enums import GraylogProtocol, QueuePolicy
from lib_log_rich.runtime._settings import (
    FeatureFlags,
    GraylogSettings,
    PayloadLimits,
    RuntimeConfig,
    RuntimeSettings,
    build_runtime_settings,
)
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

SETTINGS = cast(Any, importlib.import_module("lib_log_rich.runtime._settings"))


def make_config(**overrides: object) -> RuntimeConfig:
    base_args: dict[str, Any] = {
        "service": "svc",
        "environment": "env",
        "queue_enabled": True,
    }
    base_args.update(overrides)
    return RuntimeConfig(**base_args)


def test_runtime_config_requires_service_and_environment() -> None:
    with pytest.raises(ValueError, match="service must not be empty"):
        cast(Any, RuntimeSettings)._require_service(" ")
    with pytest.raises(ValueError, match="environment must not be empty"):
        cast(Any, RuntimeSettings)._require_environment(" ")


def test_runtime_config_validates_positive_sizes() -> None:
    with pytest.raises(ValueError, match="ring_buffer_size must be positive"):
        cast(Any, RuntimeSettings)._positive_ring_buffer(0)
    with pytest.raises(ValueError, match="queue_maxsize must be positive"):
        cast(Any, RuntimeSettings)._positive_queue_maxsize(0)


def test_runtime_config_normalises_timeouts_and_patterns() -> None:
    config = make_config(queue_put_timeout=0, queue_stop_timeout=0, scrub_patterns={"token": r"\d+"})
    settings = build_runtime_settings(config=config)
    assert settings.queue_put_timeout is None
    assert settings.queue_stop_timeout is None
    assert settings.scrub_patterns["token"] == r"\d+"


def test_runtime_config_rate_limit_validator() -> None:
    with pytest.raises(ValueError, match=r"rate_limit\[0] must be positive"):
        cast(Any, RuntimeSettings)._validate_rate_limit((0, 1.0))
    with pytest.raises(ValueError, match=r"rate_limit\[1] must be positive"):
        cast(Any, RuntimeSettings)._validate_rate_limit((1, 0.0))


def test_build_runtime_settings_respects_ring_buffer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config()
    monkeypatch.setenv("LOG_RING_BUFFER_SIZE", "-5")
    with pytest.raises(ValueError, match="LOG_RING_BUFFER_SIZE must be positive"):
        build_runtime_settings(config=config)


def test_build_runtime_settings_payload_limits_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config(payload_limits={"message_max_chars": 2048})
    settings = build_runtime_settings(config=config)
    assert isinstance(settings.limits, PayloadLimits)
    assert settings.limits.message_max_chars == 2048


def test_build_runtime_settings_queue_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    config = make_config()
    monkeypatch.setenv("LOG_QUEUE_MAXSIZE", "512")
    monkeypatch.setenv("LOG_QUEUE_FULL_POLICY", "DROP")
    monkeypatch.setenv("LOG_QUEUE_PUT_TIMEOUT", "0.5")
    monkeypatch.setenv("LOG_QUEUE_STOP_TIMEOUT", "-1")
    settings = build_runtime_settings(config=config)
    assert settings.queue_maxsize == 512
    assert settings.queue_full_policy == "drop"
    assert settings.queue_put_timeout == 0.5
    assert settings.queue_stop_timeout is None


def test_feature_flags_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    flags = SETTINGS.resolve_feature_flags(
        enable_ring_buffer=True,
        enable_journald=True,
        enable_eventlog=False,
        queue_enabled=False,
    )
    assert isinstance(flags, FeatureFlags)
    assert flags.journald is False
    assert flags.eventlog is False  # defaults to env False


def test_feature_flags_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    flags = SETTINGS.resolve_feature_flags(
        enable_ring_buffer=False,
        enable_journald=False,
        enable_eventlog=True,
        queue_enabled=True,
    )
    assert flags.queue is True
    assert flags.eventlog is False


def test_resolve_graylog_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_ENABLE_GRAYLOG", "1")
    monkeypatch.setenv("LOG_GRAYLOG_PROTOCOL", "UDP")
    monkeypatch.setenv("LOG_GRAYLOG_TLS", "0")
    monkeypatch.setenv("LOG_GRAYLOG_ENDPOINT", "gray.example:12201")
    settings = SETTINGS.resolve_graylog(
        enable_graylog=False,
        graylog_endpoint=None,
        graylog_protocol="tcp",
        graylog_tls=True,
        graylog_level="info",
    )
    assert isinstance(settings, GraylogSettings)
    assert settings.enabled is True
    assert settings.protocol is GraylogProtocol.UDP
    assert settings.tls is False
    assert settings.endpoint == ("gray.example", 12201)


def test_resolve_queue_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_QUEUE_MAXSIZE", "not-int")
    assert SETTINGS.resolve_queue_maxsize(128) == 128
    monkeypatch.setenv("LOG_QUEUE_FULL_POLICY", "  drop  ")
    assert SETTINGS.resolve_queue_policy(QueuePolicy.BLOCK) is QueuePolicy.DROP
    monkeypatch.setenv("LOG_QUEUE_PUT_TIMEOUT", "-5")
    assert SETTINGS.resolve_queue_timeout(1.0) is None
    monkeypatch.setenv("LOG_QUEUE_STOP_TIMEOUT", "1.25")
    assert SETTINGS.resolve_queue_stop_timeout(None) == 1.25


def test_coerce_graylog_endpoint_validation() -> None:
    with pytest.raises(ValueError, match="HOST:PORT"):
        SETTINGS.coerce_graylog_endpoint("example", None)
    with pytest.raises(ValueError, match="must be an integer"):
        SETTINGS.coerce_graylog_endpoint("example:abc", None)
    with pytest.raises(ValueError, match="must be positive"):
        SETTINGS.coerce_graylog_endpoint("example:0", None)
    assert SETTINGS.coerce_graylog_endpoint(None, ("fallback", 1)) == ("fallback", 1)


def test_coerce_rate_limit_validation() -> None:
    assert SETTINGS.coerce_rate_limit(None, (5, 1.0)) == (5, 1.0)
    with pytest.raises(ValueError, match="MAX:WINDOW_SECONDS"):
        SETTINGS.coerce_rate_limit("invalid", None)
    with pytest.raises(ValueError, match="numeric values"):
        SETTINGS.coerce_rate_limit("five:ten", None)
    with pytest.raises(ValueError, match="values must be positive"):
        SETTINGS.coerce_rate_limit("0:10", None)
    assert SETTINGS.coerce_rate_limit("5:2.5", None) == (5, 2.5)


def test_env_bool_interpretation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLAG", "YES")
    assert SETTINGS.env_bool("FLAG", False) is True
    monkeypatch.setenv("FLAG", "off")
    assert SETTINGS.env_bool("FLAG", True) is False
    monkeypatch.setenv("FLAG", "maybe")
    assert SETTINGS.env_bool("FLAG", True) is True


def test_parse_console_styles() -> None:
    assert SETTINGS.parse_console_styles(None) is None
    assert SETTINGS.parse_console_styles("") is None
    styles = SETTINGS.parse_console_styles("info=cyan, warning = yellow ,invalid")
    assert styles == {"INFO": "cyan", "WARNING": "yellow"}


def test_parse_scrub_patterns() -> None:
    assert SETTINGS.parse_scrub_patterns(None) is None
    assert SETTINGS.parse_scrub_patterns("") is None
    patterns = SETTINGS.parse_scrub_patterns("token=\\w+, empty=")
    assert patterns == {"token": r"\w+", "empty": ".+"}


def test_resolve_console_palette_merges_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_THEME", "classic")
    theme, styles = SETTINGS.resolve_console_palette("classic", {"INFO": "green"}, {"ERROR": "red"})
    assert theme == "classic"
    assert styles and styles["INFO"] == "green"
    assert styles["ERROR"] == "red"


def test_resolve_dump_defaults_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_DUMP_FORMAT_PRESET", "short")
    monkeypatch.setenv("LOG_DUMP_FORMAT_TEMPLATE", "{message}")
    defaults = SETTINGS.resolve_dump_defaults(dump_format_preset=None, dump_format_template=None)
    assert defaults.format_preset == "short"
    assert defaults.format_template == "{message}"


def test_resolve_rate_limit_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_RATE_LIMIT", "10:1.5")
    assert SETTINGS.resolve_rate_limit((5, 2.0)) == (10, 1.5)


def test_runtime_settings_public_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    from lib_log_rich.runtime import settings as public_settings

    monkeypatch.setenv("LOG_QUEUE_MAXSIZE", "256")
    config = public_settings.RuntimeConfig(service="svc", environment="env", queue_enabled=True)
    settings = public_settings.build_runtime_settings(config=config)
    assert settings.queue_maxsize == 256

    assert public_settings.resolve_queue_policy(QueuePolicy.DROP) is QueuePolicy.DROP
    assert public_settings.parse_console_styles("info=cyan") == {"INFO": "cyan"}
