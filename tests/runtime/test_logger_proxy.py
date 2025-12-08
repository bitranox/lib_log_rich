"""Tests for LoggerProxy level normalisation helpers."""

from __future__ import annotations

import logging
from types import TracebackType
from typing import Any, cast

import pytest

from lib_log_rich.application.use_cases._types import ProcessResult
from lib_log_rich.domain import LogLevel
from lib_log_rich.runtime import LoggerProxy, RuntimeConfig, getLogger, init, inspect_runtime, is_initialised, shutdown
from lib_log_rich.runtime._composition import coerce_level
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

ExcInfoTuple = tuple[type[BaseException], BaseException, TracebackType | None]


class RecordingProcess:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> ProcessResult:
        self.payload = dict(kwargs)
        return ProcessResult(ok=True)


def make_proxy(recorder: RecordingProcess, level: LogLevel = LogLevel.DEBUG) -> LoggerProxy:
    proxy = LoggerProxy("tests.logger", recorder)
    proxy.setLevel(level)
    return proxy


def test_logger_proxy_log_accepts_string_levels() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    result = proxy.log("warning", "string-level")

    assert recorder.payload["level"] is LogLevel.WARNING
    assert result.ok is True
    assert recorder.payload["message"] == "string-level"
    assert recorder.payload["args"] == ()


def test_logger_proxy_log_accepts_numeric_levels() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.log(logging.ERROR, "numeric-level")

    assert recorder.payload["level"] is LogLevel.ERROR
    assert recorder.payload["args"] == ()


def test_level_helpers_raise_on_unsupported_values() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    with pytest.raises(ValueError):
        proxy.log("fatal", "unsupported")

    with pytest.raises(TypeError):
        coerce_level(cast(Any, 3.14))

    with pytest.raises(TypeError):
        coerce_level(cast(Any, True))


def test_coerce_level_accepts_numeric_levels() -> None:
    assert coerce_level(logging.INFO) is LogLevel.INFO
    assert coerce_level(LogLevel.DEBUG) is LogLevel.DEBUG
    assert coerce_level("critical") is LogLevel.CRITICAL


def test_logger_proxy_preserves_message_args() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.info("value %s", "payload")

    assert recorder.payload["message"] == "value %s"
    assert recorder.payload["args"] == ("payload",)


def test_logger_proxy_normalizes_exc_info_true() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        proxy.error("failed", exc_info=True)

    exc_info = cast(ExcInfoTuple, recorder.payload["exc_info"])
    assert len(exc_info) == 3


def test_logger_proxy_exception_defaults_to_true_exc_info() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    try:
        raise ValueError("failed")
    except ValueError:
        proxy.exception("captured")

    exc_info = cast(ExcInfoTuple, recorder.payload["exc_info"])
    assert isinstance(exc_info[1], ValueError)
    assert recorder.payload["level"] is LogLevel.ERROR
    assert recorder.payload["stack_info"] is None


def test_logger_proxy_accepts_exc_info_instance() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    exc = ValueError("problem")
    proxy.error("failed", exc_info=exc)

    exc_info = cast(ExcInfoTuple, recorder.payload["exc_info"])
    assert exc_info[1] is exc


def test_logger_proxy_stack_info_true() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.info("with stack", stack_info=True)

    stack_info = recorder.payload["stack_info"]
    assert isinstance(stack_info, str)
    assert "test_logger_proxy_stack_info_true" in stack_info


def test_logger_proxy_exception_respects_exc_info_override() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)

    proxy.exception("suppressed", exc_info=False)

    assert recorder.payload["exc_info"] is None
    assert recorder.payload["level"] is LogLevel.ERROR


def test_logger_proxy_extra_is_copied() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder)
    extra: dict[str, Any] = {"foo": "bar"}

    proxy.info("copy", extra=extra)

    extra["foo"] = "mutated"
    assert recorder.payload["extra"]["foo"] == "bar"


def test_logger_proxy_set_level_filters_below_threshold() -> None:
    recorder = RecordingProcess()
    proxy = make_proxy(recorder, level=LogLevel.ERROR)

    skipped = proxy.info("will be skipped")

    assert recorder.payload == {}
    assert skipped.ok is False
    assert skipped.reason == "logger_level"


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
        assert skipped.reason == "logger_level"

        after = inspect_runtime()
        assert after.console_level is LogLevel.INFO
        assert after.backend_level is LogLevel.WARNING
    finally:
        shutdown()
