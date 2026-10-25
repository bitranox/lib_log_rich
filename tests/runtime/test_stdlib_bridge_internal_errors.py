"""The stdlib bridge must not swallow lib_log_rich's own error reports.

The bridge declines records from the ``lib_log_rich`` namespace so they cannot
loop back into the pipeline. Installed on root, it is also the handler that
makes stdlib skip ``logging.lastResort``, so a declined record that no other
handler sees reached no output at all: an adapter that raised under
``attach_std_logging()`` left exit code 0 and an empty stderr.
"""

from __future__ import annotations

import contextlib
import io
import logging
import os
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

from lib_log_rich.runtime import StdlibLoggingHandler
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

pytestmark = [OS_AGNOSTIC]

_RAISING_ADAPTER_SCRIPT = textwrap.dedent(
    """
    import logging
    import sys

    from lib_log_rich.runtime import RuntimeConfig, attach_std_logging, init, shutdown


    class ExplodingConsole:
        def emit(self, event, *, colorize):
            raise RuntimeError("console exploded")

        def flush(self):
            return None


    init(
        RuntimeConfig(
            service="bridge-probe",
            environment="tests",
            queue_enabled=sys.argv[1] == "queue",
            enable_graylog=False,
            enable_journald=False,
            enable_eventlog=False,
            console_adapter_factory=lambda appearance: ExplodingConsole(),
        )
    )
    attach_std_logging()
    logging.getLogger("app").warning("hello")
    shutdown()
    """
)


@pytest.mark.parametrize("mode", ["queue", "inline"])
def test_a_raising_adapter_is_reported_on_stderr_under_the_root_bridge(tmp_path: Path, mode: str) -> None:
    # A real process, because pytest installs its own handlers on root and
    # would catch the record before stderr could show whether it got lost.
    script = tmp_path / "raising_adapter.py"
    script.write_text(_RAISING_ADAPTER_SCRIPT, encoding="utf-8")

    completed = subprocess.run(  # noqa: S603 - fixed argv: this interpreter and a script the test wrote
        [sys.executable, str(script), mode],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ},
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "console exploded" in completed.stderr


@contextlib.contextmanager
def isolated_logger(name: str) -> Generator[logging.Logger, None, None]:
    """Yield a ``lib_log_rich.*`` logger cut off from root, restored afterwards.

    Use it inside the test body, never from a fixture: pytest adds its own
    capture handlers to every non-propagating logger when the call phase
    starts, and those real handlers would rightly keep the bridge quiet.
    """
    logger = logging.getLogger(f"lib_log_rich.tests.{name}")
    saved_handlers = list(logger.handlers)
    saved_propagate = logger.propagate
    saved_level = logger.level
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        yield logger
    finally:
        logger.handlers[:] = saved_handlers
        logger.propagate = saved_propagate
        logger.setLevel(saved_level)


def test_a_declined_internal_warning_reaches_stderr_when_the_bridge_is_its_only_handler(capsys: pytest.CaptureFixture[str]) -> None:
    with isolated_logger("bridge_alone") as logger:
        logger.addHandler(StdlibLoggingHandler())
        logger.warning("internal trouble")

    assert capsys.readouterr().err.count("internal trouble") == 1


def test_a_declined_internal_warning_is_left_to_a_real_handler_beside_the_bridge(capsys: pytest.CaptureFixture[str]) -> None:
    sink = io.StringIO()
    with isolated_logger("bridge_beside_handler") as logger:
        logger.addHandler(StdlibLoggingHandler())
        logger.addHandler(logging.StreamHandler(sink))
        logger.warning("internal trouble")

    assert sink.getvalue().count("internal trouble") == 1
    assert "internal trouble" not in capsys.readouterr().err


def test_two_bridges_on_one_chain_report_a_declined_warning_once(capsys: pytest.CaptureFixture[str]) -> None:
    with isolated_logger("two_bridges") as logger:
        child = logging.getLogger(f"{logger.name}.child")
        logger.addHandler(StdlibLoggingHandler())
        child.addHandler(StdlibLoggingHandler())
        try:
            child.warning("internal trouble")
        finally:
            child.handlers.clear()

    assert capsys.readouterr().err.count("internal trouble") == 1


def test_a_declined_internal_record_below_warning_stays_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    with isolated_logger("below_warning") as logger:
        logger.addHandler(StdlibLoggingHandler())
        logger.info("internal chatter")

    assert "internal chatter" not in capsys.readouterr().err


def test_a_record_the_caller_marked_skip_stays_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    logger = logging.getLogger("tests.bridge.skip_marked")
    saved_propagate = logger.propagate
    logger.propagate = False
    logger.addHandler(StdlibLoggingHandler())
    try:
        logger.warning("suppressed on purpose", extra={"lib_log_rich_skip": True})
    finally:
        logger.handlers.clear()
        logger.propagate = saved_propagate

    assert "suppressed on purpose" not in capsys.readouterr().err
