"""Tests for LoggerProxy level normalisation helpers."""

from __future__ import annotations

import json
import logging
from types import TracebackType
from typing import Any, cast

import pytest

from lib_log_rich import bind, dump
from lib_log_rich.domain import DropReason, LogLevel
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.runtime import LoggerProxy, RuntimeConfig, getLogger, init, inspect_runtime, is_initialised, shutdown
from tests.os_markers import OS_AGNOSTIC
from tests.process_fakes import RecordingProcess

pytestmark = [OS_AGNOSTIC]

EMPTY_FORMAT_MAPPING: dict[str, object] = {}

ExcInfoTuple = tuple[type[BaseException], BaseException, TracebackType | None]


def make_proxy(recorder: RecordingProcess, level: LogLevel = LogLevel.DEBUG) -> LoggerProxy:
    proxy = LoggerProxy("tests.logger", recorder)
    proxy.setLevel(level)
    return proxy


def test_logger_proxy_log_accepts_string_levels() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    result = proxy.log("warning", "string-level")

    assert recorder.last.level is LogLevel.WARNING
    assert result.ok is True
    assert recorder.last.message == "string-level"
    assert recorder.last.args == ()


def test_logger_proxy_log_accepts_numeric_levels() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.log(logging.ERROR, "numeric-level")

    assert recorder.last.level is LogLevel.ERROR
    assert recorder.last.args == ()


def test_level_helpers_raise_on_unsupported_values() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    with pytest.raises(ValueError):
        proxy.log("fatal", "unsupported")

    with pytest.raises(TypeError):
        LogLevel.coerce(cast("Any", 3.14))

    with pytest.raises(TypeError):
        LogLevel.coerce(cast("Any", True))


def test_level_coerce_accepts_numeric_levels() -> None:
    assert LogLevel.coerce(logging.INFO) is LogLevel.INFO
    assert LogLevel.coerce(LogLevel.DEBUG) is LogLevel.DEBUG
    assert LogLevel.coerce("critical") is LogLevel.CRITICAL


def test_logger_proxy_preserves_message_args() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.info("value %s", "payload")

    assert recorder.last.message == "value %s"
    assert recorder.last.args == ("payload",)


def test_logger_proxy_unwraps_a_single_mapping_argument_like_stdlib() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.info("%(a)s here", {"a": 1})

    assert recorder.last.args == {"a": 1}


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param((EMPTY_FORMAT_MAPPING,), (EMPTY_FORMAT_MAPPING,), id="empty-mapping-stays-positional"),
        pytest.param(({"a": 1}, "x"), ({"a": 1}, "x"), id="mapping-beside-another-arg-stays-positional"),
    ],
)
def test_logger_proxy_keeps_positional_args_where_stdlib_does(args: tuple[object, ...], expected: tuple[object, ...]) -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.info("value", *args)

    assert recorder.last.args == expected
    assert logging.LogRecord("n", logging.INFO, "p", 1, "value", args, None).args == expected


def test_get_logger_formats_a_single_mapping_argument_end_to_end() -> None:
    if is_initialised():
        shutdown()
    init(RuntimeConfig(service="mapping-args", environment="test", queue_enabled=False, enable_journald=False, enable_eventlog=False))
    try:
        with bind(job_id="mapping-args"):
            getLogger("tests.logger").info("%(a)s here", {"a": 1})
        events = json.loads(dump(dump_format=DumpFormat.JSON))
    finally:
        shutdown()

    assert [event["message"] for event in events] == ["1 here"]


def test_logger_proxy_normalizes_exc_info_true() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        proxy.error("failed", exc_info=True)

    exc_info = cast("ExcInfoTuple", recorder.last.exc_info)
    assert len(exc_info) == 3


def test_logger_proxy_exception_defaults_to_true_exc_info() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    try:
        raise ValueError("failed")
    except ValueError:
        proxy.exception("captured")

    exc_info = cast("ExcInfoTuple", recorder.last.exc_info)
    assert isinstance(exc_info[1], ValueError)
    assert recorder.last.level is LogLevel.ERROR
    assert recorder.last.stack_info is None


def test_logger_proxy_accepts_exc_info_instance() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    exc = ValueError("problem")
    proxy.error("failed", exc_info=exc)

    exc_info = cast("ExcInfoTuple", recorder.last.exc_info)
    assert exc_info[1] is exc


def test_logger_proxy_stack_info_true() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.info("with stack", stack_info=True)

    stack_info = recorder.last.stack_info
    assert isinstance(stack_info, str)
    assert "test_logger_proxy_stack_info_true" in stack_info


def test_logger_proxy_exception_respects_exc_info_override() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.exception("suppressed", exc_info=False)

    assert recorder.last.exc_info is None
    assert recorder.last.level is LogLevel.ERROR


def test_logger_proxy_extra_is_copied() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)
    extra: dict[str, Any] = {"foo": "bar"}

    proxy.info("copy", extra=extra)

    extra["foo"] = "mutated"
    assert recorder.last.extra == {"foo": "bar"}


def test_logger_proxy_set_level_filters_below_threshold() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder, level=LogLevel.ERROR)

    skipped = proxy.info("will be skipped")

    assert recorder.calls == []
    assert skipped.ok is False
    assert skipped.reason is DropReason.LOGGER_LEVEL


def test_logger_proxy_set_level_does_not_mutate_runtime_console_level() -> None:
    if is_initialised():
        shutdown()
    config = RuntimeConfig(
        service="set-level",
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        queue_enabled=False,
        enable_ring_buffer=False,
    )
    init(config)
    try:
        logger = getLogger("tests.logger")
        before = inspect_runtime()
        assert before.console_level is LogLevel.INFO
        assert before.backend_level is LogLevel.WARNING

        logger.setLevel(LogLevel.ERROR)
        skipped = logger.info("suppressed")
        assert skipped.ok is False
        assert skipped.reason is DropReason.LOGGER_LEVEL

        after = inspect_runtime()
        assert after.console_level is LogLevel.INFO
        assert after.backend_level is LogLevel.WARNING
    finally:
        shutdown()
