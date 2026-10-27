"""An exception logged through the public API arrives in a JSON dump as traceback text.

Covers the two entry points a caller uses: ``getLogger(...).exception()`` and a
stdlib ``logging`` record bridged by ``attach_std_logging()``.
"""

from __future__ import annotations

import contextlib
import json
import logging
from typing import TYPE_CHECKING

import pytest

import lib_log_rich as log
from lib_log_rich.runtime import RuntimeConfig, attach_std_logging
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [OS_AGNOSTIC]


@pytest.fixture(autouse=True)
def _runtime() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    with contextlib.suppress(RuntimeError):
        log.shutdown()
    log.init(RuntimeConfig(service="tests", environment="ci", queue_enabled=False, enable_graylog=False))
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            log.shutdown()


def _last_dumped_exc_info() -> str:
    events = json.loads(log.dump(dump_format=log.DumpFormat.JSON))
    exc_info = events[-1]["exc_info"]
    assert isinstance(exc_info, str)
    return exc_info


def test_logger_exception_records_the_traceback_text() -> None:
    try:
        raise ValueError("bad input")
    except ValueError:
        log.getLogger("tests.exc").exception("failed")

    exc_info = _last_dumped_exc_info()

    assert exc_info.endswith("ValueError: bad input")
    assert 'File "' in exc_info


def test_a_bridged_stdlib_exception_records_the_traceback_text() -> None:
    std_logger = logging.getLogger("tests.exc.std")
    handler = attach_std_logging(logger=std_logger)
    try:
        try:
            raise KeyError("missing")
        except KeyError:
            std_logger.exception("std failed")
    finally:
        std_logger.removeHandler(handler)

    exc_info = _last_dumped_exc_info()

    assert exc_info.endswith("KeyError: 'missing'")
    assert 'File "' in exc_info
