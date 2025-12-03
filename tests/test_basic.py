"""Behavioral tests ensuring the placeholder helpers stay aligned with the system design.

These tests exercise the success and failure paths documented in docs/systemdesign/module_reference.md so doctests and runtime examples remain authoritative.
"""

from __future__ import annotations

import pytest

from lib_log_rich import hello_world, summary_info
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def test_hello_world_sings_hello_with_a_newline(capsys: pytest.CaptureFixture[str]) -> None:
    """`hello_world` always prints the canonical greeting with a trailing newline."""
    hello_world()
    captured = capsys.readouterr()
    assert captured.out == "Hello World\n"


def test_hello_world_keeps_stderr_silent(capsys: pytest.CaptureFixture[str]) -> None:
    """`hello_world` must never leak to stderr."""
    hello_world()
    captured = capsys.readouterr()
    assert captured.err == ""


def test_summary_info_announces_the_package_name() -> None:
    """`summary_info` banners always include the library name."""
    assert "Info for lib_log_rich" in summary_info()


def test_summary_info_mentions_the_version_line() -> None:
    """`summary_info` keeps the version line present for operators."""
    assert "version" in summary_info()


def test_summary_info_trails_with_a_newline() -> None:
    """`summary_info` should feel like a banner, ending with a newline."""
    assert summary_info().endswith("\n")


def test_summary_info_returns_consistent_text() -> None:
    """`summary_info` must be idempotent so docs stay stable."""
    first = summary_info()
    second = summary_info()
    assert first == second


def test_i_should_fail_raises_runtime_error() -> None:
    """`i_should_fail` always raises `RuntimeError` with the canonical message."""
    from lib_log_rich.lib_log_rich import i_should_fail

    with pytest.raises(RuntimeError, match="I should fail"):
        i_should_fail()


def test_shutdown_async_is_exposed() -> None:
    """Top-level façade exposes the async shutdown helper."""
    import lib_log_rich as log

    assert hasattr(log, "shutdown_async")
