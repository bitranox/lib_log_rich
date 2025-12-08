"""Tests for the stdlib logging bridge."""

from __future__ import annotations

import json
import logging
import sys
from typing import Iterable

import pytest

from lib_log_rich.runtime import RuntimeConfig, StdlibLoggingHandler, attach_std_logging, dump, init, is_initialised, shutdown
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def cleanup_runtime() -> None:
    if is_initialised():
        shutdown()


@pytest.fixture(autouse=True)
def reset_runtime() -> Iterable[None]:
    try:
        cleanup_runtime()
        yield
    finally:
        cleanup_runtime()


def test_handler_emits_record_into_ring_buffer() -> None:
    init(
        RuntimeConfig(
            service="stdlib-handler-unit",
            environment="tests",
            queue_enabled=False,
            enable_graylog=False,
            enable_journald=False,
            enable_eventlog=False,
        )
    )
    handler = StdlibLoggingHandler()

    record = logging.LogRecord(
        name="tests.stdlib.bridge",
        level=logging.ERROR,
        pathname="/tmp/test_stdlib_bridge.py",
        lineno=123,
        msg="failure %s",
        args=("payload",),
        exc_info=None,
        func="emit_record",
    )
    record.stacklevel = 3
    record.custom_field = "value"
    record.stack_info = "stack details"
    try:
        raise ValueError("boom")
    except ValueError:
        record.exc_info = sys.exc_info()

    handler.emit(record)

    # Records originating from lib_log_rich should be ignored to avoid recursion.
    internal = logging.LogRecord(
        name="lib_log_rich.runtime.debug",
        level=logging.INFO,
        pathname=__file__,
        lineno=200,
        msg="internal",
        args=(),
        exc_info=None,
        func="internal",
    )
    handler.emit(internal)

    payload = json.loads(dump(dump_format="json"))
    assert len(payload) == 1
    event = payload[0]
    assert event["logger_name"] == "tests.stdlib.bridge"
    assert event["message"] == "failure payload"
    assert "ValueError" in event.get("exc_info", "")
    assert event.get("stack_info") == "stack details"
    assert event["extra"]["custom_field"] == "value"
    assert event["extra"]["pathname"] == "/tmp/test_stdlib_bridge.py"
    assert event["extra"]["lineno"] == 123
    assert event["extra"]["funcName"] == "emit_record"


def test_attach_std_logging_wires_root_logger_dump() -> None:
    init(
        RuntimeConfig(
            service="stdlib-handler-integration",
            environment="tests",
            queue_enabled=False,
            enable_graylog=False,
            enable_journald=False,
            enable_eventlog=False,
        )
    )

    root_logger = logging.getLogger()
    original_level = root_logger.level
    original_handlers = tuple(root_logger.handlers)
    original_propagate = root_logger.propagate
    handler = attach_std_logging(logger_level=logging.INFO, propagate=False)

    logger = logging.getLogger("integration.stdlib")
    logger.info("bridge works", extra={"origin": "integration"})

    dump_text = dump()
    assert "bridge works" in dump_text
    assert "integration.stdlib" in dump_text

    if handler not in original_handlers and handler in root_logger.handlers:
        root_logger.removeHandler(handler)
    root_logger.setLevel(original_level)
    root_logger.propagate = original_propagate
