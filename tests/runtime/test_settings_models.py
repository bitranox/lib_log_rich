from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from pydantic import ValidationError

from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.enums import ConsoleStream, ConsoleTheme, FormatPreset, GraylogProtocol, QueuePolicy
from lib_log_rich.runtime.settings import (
    ConsoleAppearance,
    DumpDefaults,
    FeatureFlags,
    GraylogSettings,
    PayloadLimits,
    RuntimeConfig,
    RuntimeSettings,
    coerce_console_styles_input,
)
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def test_coerce_console_styles_input_handles_enum_keys() -> None:
    from_enum = coerce_console_styles_input({LogLevel.INFO: "cyan"})
    from_str = coerce_console_styles_input({" warn ": "yellow"})

    combined: dict[str, str] = {}
    if from_enum:
        combined.update(from_enum)
    if from_str:
        combined.update(from_str)

    assert combined == {"INFO": "cyan", "WARN": "yellow"}


def test_coerce_console_styles_input_returns_none_when_empty() -> None:
    assert coerce_console_styles_input({}) is None
    assert coerce_console_styles_input(None) is None


def test_coerce_console_styles_input_drops_blank_keys() -> None:
    styles = coerce_console_styles_input({"   ": "skip", "error": "red"})
    assert styles == {"ERROR": "red"}


def test_console_appearance_normalises_styles() -> None:
    appearance = ConsoleAppearance(styles={" info ": "cyan", "": "ignored"})
    assert appearance.styles == {"INFO": "cyan"}


def test_console_appearance_defaults_to_stderr_stream() -> None:
    appearance = ConsoleAppearance()
    assert appearance.stream is ConsoleStream.STDERR


def test_console_appearance_requires_custom_stream_target() -> None:
    with pytest.raises(ValueError, match="stream_target must be provided"):
        ConsoleAppearance(stream=ConsoleStream.CUSTOM)

    buffer = StringIO()
    appearance = ConsoleAppearance(stream=ConsoleStream.CUSTOM, stream_target=buffer)
    assert appearance.stream_target is buffer


def test_console_appearance_accepts_none_stream() -> None:
    appearance = ConsoleAppearance(stream=ConsoleStream.NONE)
    assert appearance.stream is ConsoleStream.NONE


def test_console_appearance_rejects_stream_target_for_standard_modes() -> None:
    with pytest.raises(ValueError, match="stream_target is only supported"):
        ConsoleAppearance(stream=ConsoleStream.STDOUT, stream_target=StringIO())


def test_graylog_settings_validators_enforce_endpoint() -> None:
    with pytest.raises(ValueError, match="host must be non-empty"):
        GraylogSettings(enabled=True, protocol=GraylogProtocol.TCP, endpoint=("", 12201))

    with pytest.raises(ValueError, match="must be positive"):
        GraylogSettings(enabled=True, protocol=GraylogProtocol.TCP, endpoint=("localhost", 0))

    settings = GraylogSettings(enabled=True, protocol=GraylogProtocol.UDP, endpoint=("graylog", 12201))
    assert settings.protocol is GraylogProtocol.UDP
    assert settings.endpoint == ("graylog", 12201)


def test_graylog_settings_refuse_enabled_without_endpoint() -> None:
    with pytest.raises(ValueError, match="no endpoint"):
        GraylogSettings(enabled=True)


def test_graylog_settings_allow_disabled_without_endpoint() -> None:
    assert GraylogSettings(enabled=False).endpoint is None


def test_payload_limits_validates_positive_values() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        PayloadLimits(message_max_chars=0)

    limits = PayloadLimits(extra_max_total_bytes=None, message_max_chars=10)
    assert limits.extra_max_total_bytes is None
    assert limits.message_max_chars == 10

    with pytest.raises(ValueError, match="extra_max_total_bytes must be positive or None"):
        PayloadLimits(extra_max_total_bytes=-1)


def test_runtime_settings_validators_normalise_inputs() -> None:
    appearance = ConsoleAppearance()
    dump = DumpDefaults(format_preset=FormatPreset.FULL)
    graylog = GraylogSettings(enabled=False)
    flags = FeatureFlags(queue=True, ring_buffer=False, journald=False, eventlog=False)
    limits = PayloadLimits()

    settings = RuntimeSettings(
        service=" svc ",
        environment=" prod ",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        graylog_level=LogLevel.ERROR,
        ring_buffer_size=10,
        console=appearance,
        dump=dump,
        graylog=graylog,
        flags=flags,
        rate_limit=(5, 10.0),
        limits=limits,
        scrub_patterns={" token ": " 123 "},
        console_factory=None,
        diagnostic_hook=None,
        queue_maxsize=8,
        queue_full_policy=QueuePolicy.DROP,
        queue_put_timeout=-1.0,
        queue_stop_timeout=0.0,
    )

    assert settings.service == "svc"
    assert settings.environment == "prod"
    assert settings.queue_full_policy is QueuePolicy.DROP
    assert settings.queue_put_timeout is None
    assert settings.queue_stop_timeout is None
    assert settings.scrub_patterns == {" token ": " 123 "}

    with pytest.raises(ValueError, match="service must not be empty"):
        RuntimeSettings(
            service=" ",
            environment="prod",
            console_level=LogLevel.INFO,
            backend_level=LogLevel.WARNING,
            graylog_level=LogLevel.ERROR,
            ring_buffer_size=10,
            console=appearance,
            dump=dump,
            graylog=graylog,
            flags=flags,
        )

    with pytest.raises(ValueError, match="environment must not be empty"):
        RuntimeSettings(
            service="svc",
            environment=" ",
            console_level=LogLevel.INFO,
            backend_level=LogLevel.WARNING,
            graylog_level=LogLevel.ERROR,
            ring_buffer_size=10,
            console=appearance,
            dump=dump,
            graylog=graylog,
            flags=flags,
        )

    with pytest.raises(ValueError, match="ring_buffer_size must be positive"):
        RuntimeSettings(
            service="svc",
            environment="prod",
            console_level=LogLevel.INFO,
            backend_level=LogLevel.WARNING,
            graylog_level=LogLevel.ERROR,
            ring_buffer_size=0,
            console=appearance,
            dump=dump,
            graylog=graylog,
            flags=flags,
        )

    with pytest.raises(ValueError, match="queue_maxsize must be positive"):
        RuntimeSettings(
            service="svc",
            environment="prod",
            console_level=LogLevel.INFO,
            backend_level=LogLevel.WARNING,
            graylog_level=LogLevel.ERROR,
            ring_buffer_size=10,
            console=appearance,
            dump=dump,
            graylog=graylog,
            flags=flags,
            queue_maxsize=0,
        )

    with pytest.raises(ValueError, match="rate_limit\\[0] must be positive"):
        RuntimeSettings(
            service="svc",
            environment="prod",
            console_level=LogLevel.INFO,
            backend_level=LogLevel.WARNING,
            graylog_level=LogLevel.ERROR,
            ring_buffer_size=10,
            console=appearance,
            dump=dump,
            graylog=graylog,
            flags=flags,
            rate_limit=(-1, 1.0),
        )

    with pytest.raises(ValueError, match="rate_limit\\[1] must be positive"):
        RuntimeSettings(
            service="svc",
            environment="prod",
            console_level=LogLevel.INFO,
            backend_level=LogLevel.WARNING,
            graylog_level=LogLevel.ERROR,
            ring_buffer_size=10,
            console=appearance,
            dump=dump,
            graylog=graylog,
            flags=flags,
            rate_limit=(1, 0.0),
        )

    settings_none_rate = RuntimeSettings(
        service="svc",
        environment="prod",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        graylog_level=LogLevel.ERROR,
        ring_buffer_size=10,
        console=appearance,
        dump=dump,
        graylog=graylog,
        flags=flags,
        rate_limit=None,
        queue_put_timeout=None,
        queue_stop_timeout=None,
        scrub_patterns={"": ""},
    )
    assert settings_none_rate.rate_limit is None
    assert settings_none_rate.queue_put_timeout is None
    assert settings_none_rate.queue_stop_timeout is None
    assert settings_none_rate.scrub_patterns == {}


def _runtime_config(**fields: Any) -> RuntimeConfig:
    # Values arrive as a config file or CLI hands them over, so they are untyped here.
    return RuntimeConfig(service="svc", environment="env", **fields)


def test_runtime_config_category_defaults_are_enum_members() -> None:
    config = _runtime_config()

    assert config.graylog_protocol is GraylogProtocol.TCP
    assert config.queue_full_policy is QueuePolicy.BLOCK
    assert config.console_stream is ConsoleStream.STDERR
    assert config.console_format_preset is None
    assert config.dump_format_preset is None
    assert config.console_theme is ConsoleTheme.DARK


def test_runtime_config_parses_category_names_in_any_case() -> None:
    config = _runtime_config(
        graylog_protocol=" UDP ",
        queue_full_policy="Drop",
        console_stream="STDOUT",
        console_format_preset="Short_Loc",
        dump_format_preset="FULL_LOC",
        console_theme=" Neon ",
    )

    assert config.graylog_protocol is GraylogProtocol.UDP
    assert config.queue_full_policy is QueuePolicy.DROP
    assert config.console_stream is ConsoleStream.STDOUT
    assert config.console_format_preset is FormatPreset.SHORT_LOC
    assert config.dump_format_preset is FormatPreset.FULL_LOC
    assert config.console_theme is ConsoleTheme.NEON


def test_runtime_config_treats_a_blank_preset_or_theme_as_unset() -> None:
    config = _runtime_config(console_format_preset="", dump_format_preset="  ", console_theme=" ")

    assert config.console_format_preset is None
    assert config.dump_format_preset is None
    assert config.console_theme is None


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("graylog_protocol", "smtp", "Invalid Graylog protocol"),
        ("queue_full_policy", "spill", "Invalid queue policy"),
        ("console_stream", "printer", "Invalid console stream"),
        ("console_format_preset", "nope", "Invalid format preset"),
        ("dump_format_preset", "nope", "Invalid format preset"),
        ("console_theme", "nope", "Invalid console theme: 'nope'; must be one of 'classic', 'dark', 'neon', 'pastel'"),
    ],
)
def test_runtime_config_refuses_an_unknown_category_name(field: str, value: str, reason: str) -> None:
    with pytest.raises(ValidationError, match=reason) as caught:
        _runtime_config(**{field: value})

    assert caught.value.errors()[0]["loc"] == (field,)


def test_runtime_config_validates_a_payload_limits_mapping_into_the_model() -> None:
    config = _runtime_config(payload_limits={"message_max_chars": 64})

    assert isinstance(config.payload_limits, PayloadLimits)
    assert config.payload_limits.message_max_chars == 64


def test_runtime_config_refuses_an_invalid_payload_limits_mapping() -> None:
    with pytest.raises(ValidationError, match="payload limit values must be positive"):
        _runtime_config(payload_limits={"message_max_chars": 0})


def test_console_appearance_holds_a_format_preset_member() -> None:
    appearance = ConsoleAppearance(format_preset=FormatPreset.SHORT_LOC_ICON)

    assert appearance.format_preset is FormatPreset.SHORT_LOC_ICON


def test_console_appearance_built_directly_holds_level_names() -> None:
    appearance = ConsoleAppearance(styles={" Warning ": "yellow", "eRRor": "red", "  ": "ignored"})

    assert appearance.styles == {"WARNING": "yellow", "ERROR": "red"}


def test_runtime_config_refuses_an_unknown_setting_and_names_it() -> None:
    # A misspelled setting must not be dropped silently, or the intended value never applies.
    with pytest.raises(ValidationError) as excinfo:
        _runtime_config(ring_bufer_size=5)
    assert "ring_bufer_size" in str(excinfo.value)
    assert excinfo.value.errors()[0]["type"] == "extra_forbidden"


def test_runtime_config_refuses_an_unknown_payload_limit_key_and_names_it() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _runtime_config(payload_limits={"message_max_chars": 10, "mesage_max_chars": 5})
    assert "mesage_max_chars" in str(excinfo.value)
    assert excinfo.value.errors()[0]["type"] == "extra_forbidden"
