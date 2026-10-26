"""A dump's ``theme`` replaces the runtime's palette, never the styles a user configured.

``init()`` fills the runtime theme's palette into the console styles. A dump must
not treat those palette-derived entries as overrides, or ``dump(theme=X)`` renders
the runtime's colours whatever ``X`` is. Styles the user configured
(``console_styles`` / ``LOG_CONSOLE_STYLES``) still win for their levels, and a
per-call ``console_styles`` wins over both.
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import TYPE_CHECKING, Any

import pytest

from lib_log_rich import bind, dump, getLogger, shutdown
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.enums import ConsoleStream, ConsoleTheme
from lib_log_rich.runtime import RuntimeConfig, init
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [OS_AGNOSTIC]

_LEADING_STYLE = re.compile(r"^(?:\x1b\[[0-9;]*m)+")


@pytest.fixture(autouse=True)
def no_ambient_log_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("LOG_")]:
        monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def no_runtime_left_behind() -> Iterator[None]:
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            shutdown()


def _line_styles(**dump_kwargs: Any) -> dict[str, str]:
    """Return the leading ANSI style of the INFO and WARNING lines of a coloured text dump."""
    text = dump(dump_format=DumpFormat.TEXT, color=True, **dump_kwargs)
    styles: dict[str, str] = {}
    for line in text.splitlines():
        match = _LEADING_STYLE.match(line)
        for level in ("INFO", "WARNING"):
            if level in line and match is not None:
                styles[level] = match.group(0)
    return styles


def _styles_from_runtime(*, dump_kwargs: dict[str, Any] | None = None, **config: Any) -> dict[str, str]:
    init(RuntimeConfig(service="svc", environment="test", queue_enabled=False, console_stream=ConsoleStream.NONE, **config))
    with bind(job_id="dump-theme"):
        getLogger("tests.theme").info("an info")
        getLogger("tests.theme").warning("a warning")
    styles = _line_styles(**(dump_kwargs or {}))
    shutdown()
    return styles


def test_a_dump_theme_replaces_the_runtime_palette() -> None:
    classic = _styles_from_runtime(console_theme=ConsoleTheme.CLASSIC)

    dumped = _styles_from_runtime(console_theme=ConsoleTheme.DARK, dump_kwargs={"theme": ConsoleTheme.CLASSIC})

    assert dumped == classic


def test_a_dump_without_a_theme_keeps_the_runtime_palette() -> None:
    dark_default = _styles_from_runtime()

    dumped = _styles_from_runtime(console_theme=ConsoleTheme.DARK, dump_kwargs={})

    assert dumped == dark_default
    assert dumped != _styles_from_runtime(console_theme=ConsoleTheme.CLASSIC)


def test_a_configured_style_still_wins_for_its_level_under_a_dump_theme() -> None:
    green_info = _styles_from_runtime(console_theme=None, console_styles={"INFO": "green"})["INFO"]
    classic = _styles_from_runtime(console_theme=ConsoleTheme.CLASSIC)

    dumped = _styles_from_runtime(
        console_theme=ConsoleTheme.DARK,
        console_styles={"INFO": "green"},
        dump_kwargs={"theme": ConsoleTheme.CLASSIC},
    )

    assert dumped == {"INFO": green_info, "WARNING": classic["WARNING"]}


def test_an_environment_style_still_wins_for_its_level_under_a_dump_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    green_info = _styles_from_runtime(console_theme=None, console_styles={"INFO": "green"})["INFO"]
    classic = _styles_from_runtime(console_theme=ConsoleTheme.CLASSIC)
    monkeypatch.setenv("LOG_CONSOLE_STYLES", "INFO=green")

    dumped = _styles_from_runtime(console_theme=ConsoleTheme.DARK, dump_kwargs={"theme": ConsoleTheme.CLASSIC})

    assert dumped == {"INFO": green_info, "WARNING": classic["WARNING"]}


def test_per_call_console_styles_win_over_the_dump_theme() -> None:
    green_info = _styles_from_runtime(console_theme=None, console_styles={"INFO": "green"})["INFO"]

    dumped = _styles_from_runtime(dump_kwargs={"theme": ConsoleTheme.CLASSIC, "console_styles": {"INFO": "green"}})

    assert dumped["INFO"] == green_info
