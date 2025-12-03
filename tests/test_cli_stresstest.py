"""End-to-end coverage for the stresstest CLI command."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

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


@pytest.mark.asyncio
async def test_stresstest_emits_console_queue_output() -> None:
    """The stresstest TUI should render queue-backed console output."""
    StressTestApp = stresstest_module.create_stresstest_app()

    app = StressTestApp()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app._rows["records_total"]._input.value = "5"
        app._rows["log_level"]._input.value = "INFO"
        app._rows["message_length"]._input.value = "12"
        app._rows["context_fields"]._input.value = "0"
        app._rows["extra_fields"]._input.value = "0"
        await pilot.click("#start")
        for _ in range(30):
            await pilot.pause(0.1)
            if app._run_task is None or app._run_task.done():
                break
        else:  # pragma: no cover - defensive guard for flakiness
            pytest.fail("stresstest run did not finish")
        await pilot.pause(0.1)

    operation_messages = [strip.text for strip in app._operations_log.lines]
    assert all("Run failed" not in message for message in operation_messages)
    assert app._metrics.emitted == app._metrics.planned == 5
    assert len(app._queued_console_log.lines) > 0
    assert len(app._async_console_log.lines) > 0
