"""End-to-end coverage for the stresstest CLI command."""

from __future__ import annotations

from click.testing import CliRunner
import pytest

from lib_log_rich import cli as cli_mod
from lib_log_rich import cli_stresstest as stresstest_module
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def test_cli_stresstest_invokes_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``stresstest`` subcommand should call the module entry point."""

    calls: list[None] = []

    def fake_run() -> None:
        calls.append(None)

    monkeypatch.setattr(stresstest_module, "run", fake_run)
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["stresstest"])

    assert result.exit_code == 0
    assert calls == [None]


def test_cli_stresstest_help_mentions_tui() -> None:
    """Help text should mention the purpose of the stresstest TUI."""

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["stresstest", "--help"])

    assert result.exit_code == 0
    assert "stress-test tui" in result.output.lower()
