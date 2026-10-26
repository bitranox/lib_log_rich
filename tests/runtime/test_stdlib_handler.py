"""Tests for the stdlib logging bridge."""

from __future__ import annotations

import dataclasses
import json
import logging
import sys
from typing import TYPE_CHECKING

import pytest

from lib_log_rich.runtime import RuntimeConfig, StdlibLoggingHandler, attach_std_logging, dump, init, is_initialised, shutdown
from lib_log_rich.runtime._state import current_runtime
from tests.os_markers import OS_AGNOSTIC
from tests.process_fakes import RecordingProcess

if TYPE_CHECKING:
    from collections.abc import Iterable

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


def _init_ring_buffer_runtime() -> None:
    init(
        RuntimeConfig(
            service="stdlib-handler-fields",
            environment="tests",
            queue_enabled=False,
            enable_graylog=False,
            enable_journald=False,
            enable_eventlog=False,
        )
    )


def test_handler_forwards_level_and_mapping_args() -> None:
    _init_ring_buffer_runtime()
    record = logging.LogRecord(
        name="tests.stdlib.mapping",
        level=logging.WARNING,
        pathname=__file__,
        lineno=7,
        msg="user %(name)s",
        args=({"name": "alice"},),
        exc_info=None,
    )

    StdlibLoggingHandler().emit(record)

    (event,) = json.loads(dump(dump_format="json"))
    assert event["message"] == "user alice"
    assert event["level"] == "warning"


@pytest.mark.parametrize(
    ("raw_args", "forwarded"),
    [
        pytest.param(None, (), id="args-none"),
        pytest.param(5, (5,), id="args-scalar"),
        pytest.param(("a", 1), ("a", 1), id="args-tuple"),
        pytest.param({"name": "alice"}, {"name": "alice"}, id="args-mapping"),
    ],
)
def test_handler_hands_process_only_tuple_or_mapping_args(raw_args: object, forwarded: object) -> None:
    """Records from third-party code or makeLogRecord carry None or a bare scalar in args.

    typeshed admits only tuple | Mapping, so the bridge normalises before the typed pipeline.
    """
    _init_ring_buffer_runtime()
    recorder = RecordingProcess()
    runtime = dataclasses.replace(current_runtime(), process=recorder)
    record = logging.makeLogRecord({"name": "tests.stdlib.loose", "levelno": logging.INFO, "levelname": "INFO", "msg": "m", "args": raw_args})

    StdlibLoggingHandler(runtime_resolver=lambda: runtime).emit(record)

    assert recorder.last.args == forwarded


def test_handler_falls_back_to_info_for_an_unknown_level() -> None:
    """A level the pipeline cannot map lands as INFO and keeps the stdlib values in ``extra``."""
    _init_ring_buffer_runtime()
    record = logging.LogRecord(
        name="tests.stdlib.custom",
        level=5,
        pathname=__file__,
        lineno=9,
        msg="trace",
        args=(),
        exc_info=None,
    )
    record.levelname = "TRACE"

    StdlibLoggingHandler().emit(record)

    (event,) = json.loads(dump(dump_format="json"))
    assert event["level"] == "info"
    assert event["extra"]["stdlib_levelno"] == 5
    assert event["extra"]["stdlib_levelname"] == "TRACE"


@pytest.mark.parametrize(("name", "expected"), [("warning", logging.WARNING), (" Error ", logging.ERROR), ("WARN", logging.WARNING)])
def test_attach_std_logging_resolves_level_names(name: str, expected: int) -> None:
    target = logging.getLogger(f"tests.stdlib.levels.{name.strip().lower()}")
    handler = attach_std_logging(logger=target, handler_level=name, logger_level=name)
    try:
        assert handler.level == expected
        assert target.level == expected
    finally:
        target.removeHandler(handler)


def test_attach_std_logging_rejects_an_unknown_level_name() -> None:
    target = logging.getLogger("tests.stdlib.levels.unknown")
    with pytest.raises(ValueError, match="Unsupported logging level: 'loud'"):
        attach_std_logging(logger=target, handler_level="loud")
    for handler in tuple(target.handlers):
        target.removeHandler(handler)
