"""Tests for LoggerProxy level normalisation helpers."""

from __future__ import annotations

import logging
from typing import Any, cast

import pytest

from lib_log_rich.domain import LogLevel
from lib_log_rich.runtime import LoggerProxy
from lib_log_rich.runtime._composition import coerce_level


class RecordingProcess:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.payload = dict(kwargs)
        return self.payload


def test_logger_proxy_log_accepts_string_levels() -> None:
    recorder = RecordingProcess()
    proxy = LoggerProxy("tests.logger", recorder)

    result = proxy.log("warning", "string-level")

    assert recorder.payload["level"] is LogLevel.WARNING
    assert result["level"] is LogLevel.WARNING


def test_logger_proxy_log_accepts_numeric_levels() -> None:
    recorder = RecordingProcess()
    proxy = LoggerProxy("tests.logger", recorder)

    proxy.log(logging.ERROR, "numeric-level")

    assert recorder.payload["level"] is LogLevel.ERROR


def test_level_helpers_raise_on_unsupported_values() -> None:
    recorder = RecordingProcess()
    proxy = LoggerProxy("tests.logger", recorder)

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
