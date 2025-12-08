from __future__ import annotations

from io import StringIO

import pytest

from lib_log_rich.domain import LogLevel
from lib_log_rich.domain.enums import ConsoleStream, GraylogProtocol, QueuePolicy
from lib_log_rich.runtime._settings import (
    ConsoleAppearance,
    DumpDefaults,
    FeatureFlags,
    GraylogSettings,
    PayloadLimits,
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
    assert coerce_console_styles_input(None) is None  # type: ignore[arg-type]


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
    dump = DumpDefaults(format_preset="full")
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
