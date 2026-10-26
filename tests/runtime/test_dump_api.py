"""Runtime façade dump regression tests ensuring helper wiring stays aligned.

Purpose
-------
Validate that `lib_log_rich.dump` honours caller overrides and runtime
defaults, matching the behaviour documented in `docs/systemdesign`.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

import pytest

from lib_log_rich import bind, dump, getLogger, shutdown
from lib_log_rich.domain import DumpFilter, LogLevel
from lib_log_rich.domain.enums import ConsoleTheme, FormatPreset
from lib_log_rich.runtime import RuntimeConfig, current_runtime, init
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from pathlib import Path

pytestmark = [OS_AGNOSTIC]


@pytest.fixture(autouse=True)
def _reset_runtime() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Tear down the singleton runtime before and after each test case."""
    with contextlib.suppress(RuntimeError):
        shutdown()
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            shutdown()


def _install_runtime(**overrides: Any) -> None:
    """Initialise a minimal runtime with predictable defaults."""
    config = RuntimeConfig(
        service="tests",
        environment="ci",
        queue_enabled=False,
        enable_graylog=False,
        **overrides,
    )
    init(config)


def _log_sample_event() -> None:
    """Emit a single INFO event so dumps always have content."""
    with bind(job_id="dump-suite"):
        getLogger("tests.dump").info("hello dump", extra={"request": "req-1"})


def test_dump_forwards_overrides(tmp_path: Path) -> None:
    """Ensure `dump` forwards caller overrides to the runtime renderer."""
    """`dump()` must pass caller overrides straight to the runtime renderer."""

    _install_runtime(console_theme=ConsoleTheme.PASTEL)
    _log_sample_event()
    runtime = current_runtime()
    observed: dict[str, Any] = {}

    def capture(**kwargs: Any) -> str:
        observed.update(kwargs)
        return "payload"

    runtime.capture_dump = capture
    console_styles = {"INFO": "green"}
    filters = {"service": "tests"}
    destination = tmp_path / "runtime.log"

    result = dump(
        dump_format="json",
        path=str(destination),
        level="warning",
        console_format_preset=FormatPreset.SHORT,
        console_format_template="{message}",
        theme=ConsoleTheme.NEON,
        console_styles=console_styles,
        context_filters=filters,
        color=True,
    )

    assert result == "payload"
    assert observed["dump_format"].name == "JSON"
    assert observed["path"] == destination
    assert observed["min_level"] == LogLevel.WARNING
    assert observed["format_preset"] is FormatPreset.SHORT
    assert observed["format_template"] == "{message}"
    assert "text_template" not in observed
    assert observed["theme"] is ConsoleTheme.NEON
    assert observed["console_styles"] is console_styles
    assert observed["colorize"] is True

    dump_filter = observed["dump_filter"]
    assert isinstance(dump_filter, DumpFilter)
    assert dump_filter.context[0].field == "service"
    assert dump_filter.context[0].predicates[0].expected == "tests"


def test_dump_uses_runtime_defaults() -> None:
    """Assert the façade reuses runtime defaults when no overrides are set."""
    """When no overrides are supplied, runtime defaults should flow through."""

    styles: Mapping[str, str] = {"INFO": "cyan"}
    _install_runtime(console_theme=ConsoleTheme.PASTEL, console_styles=styles)
    _log_sample_event()
    runtime = current_runtime()
    observed: dict[str, Any] = {}

    def capture(**kwargs: Any) -> str:
        observed.update(kwargs)
        return "snapshot"

    runtime.capture_dump = capture

    outcome = dump(dump_format="text")

    assert outcome == "snapshot"
    assert observed["theme"] is ConsoleTheme.PASTEL
    # Only the configured styles travel: the theme fills the other levels in the dump
    # itself, so a dump given another theme can replace that fill.
    assert observed["console_styles"] == {"INFO": "cyan"}
    assert observed["dump_filter"] is None
    assert observed["path"] is None
    assert observed["min_level"] is None


@pytest.mark.parametrize(
    ("parameter", "value", "enum_name"),
    [("theme", "neon", "ConsoleTheme"), ("console_format_preset", "full", "FormatPreset")],
)
def test_dump_refuses_a_plain_string_for_theme_and_preset(parameter: str, value: str, enum_name: str) -> None:
    _install_runtime()
    arguments: dict[str, Any] = {parameter: value}
    with pytest.raises(TypeError, match=f"^{parameter} must be a member of {enum_name}, got '{value}'$"):
        dump(**arguments)


def test_dump_still_takes_value_strings_for_format_and_level() -> None:
    _install_runtime()
    getLogger("tests").warning("kept")
    assert "kept" in dump(dump_format="json", level="warning")
