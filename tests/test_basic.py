"""Behavioral tests ensuring the placeholder helpers stay aligned with the system design.

These tests exercise the success and failure paths documented in docs/systemdesign/module_reference.md so doctests and runtime examples remain authoritative.
"""

from __future__ import annotations

import pytest

from lib_log_rich import hello_world, summary_info


def test_hello_world_prints_greeting(capsys: pytest.CaptureFixture[str]) -> None:
    """Ensure `hello_world` emits the canonical greeting with a trailing newline.

    Aligns the placeholder success path with the expectations spelled out in
    `docs/systemdesign/module_reference.md`.
    """

    hello_world()
    captured = capsys.readouterr()
    assert captured.out == "Hello World\n"
    assert captured.err == ""


def test_summary_info_contains_metadata() -> None:
    """Verify the metadata banner matches the documented structure.

    The assertion keeps doctest guidance and runtime output consistent with
    the module reference and README narrative.
    """

    summary = summary_info()
    assert "Info for lib_log_rich" in summary
    assert "version" in summary
    assert summary.endswith("\n")


def test_summary_info_is_idempotent() -> None:
    """Guard against accidental mutations in the metadata helper.

    Idempotence is required so system design examples remain stable between
    calls.
    """

    assert summary_info() == summary_info()


def test_i_should_fail_raises_runtime_error() -> None:
    """Confirm the intentional failure path surfaces the documented exception.

    This test ensures tooling relying on the deterministic error scenario
    behaves as described in the module reference.
    """

    from lib_log_rich.lib_log_rich import i_should_fail

    with pytest.raises(RuntimeError, match="I should fail"):
        i_should_fail()
