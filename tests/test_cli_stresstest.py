"""End-to-end coverage for the stresstest CLI command.

The parsing helpers are pinned by the module's own doctests; these tests drive
the real Textual app through ``create_stresstest_app``.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from lib_log_rich import cli as cli_mod
from lib_log_rich import cli_stresstest as stresstest_module
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


async def _run_once(app: Any, pilot: Any, **settings: str) -> None:
    """Fill the given settings into the live form, press Start and wait for the run to end."""
    await pilot.pause(0.1)
    for key, text in settings.items():
        app._rows[key]._input.value = text
    await pilot.click("#start")
    for _ in range(30):
        await pilot.pause(0.1)
        if app._run_task is None or app._run_task.done():
            break
    else:  # pragma: no cover - defensive guard for flakiness
        pytest.fail("stresstest run did not finish")
    await pilot.pause(0.1)


def _operation_messages(app: Any) -> list[str]:
    return [strip.text for strip in app._operations_log.lines]


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
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="5", log_level="INFO", message_length="12", context_fields="0", extra_fields="0")

    operation_messages = _operation_messages(app)
    assert all("Run failed" not in message for message in operation_messages)
    assert app._metrics.emitted == app._metrics.planned == 5
    assert len(app._queued_console_log.lines) > 0
    assert len(app._async_console_log.lines) > 0
    assert "Ring buffer dump captured." in operation_messages


@pytest.mark.asyncio
async def test_stresstest_yaml_dump_choice_reports_the_unsupported_format() -> None:
    """The form offers YAML, which the library cannot dump: the run completes and the dump step says so."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="3", log_level="INFO", dump_format="yaml")

    assert app._metrics.emitted == 3
    assert "Dump failed: Unsupported dump format: 'yaml'" in _operation_messages(app)


@pytest.mark.asyncio
async def test_stresstest_lists_each_rate_limited_record_as_a_result_failure() -> None:
    """A record the rate limiter refuses is counted and listed with its index and drop reason."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="3", log_level="INFO", rate_limit="1:60")

    assert app._metrics.failed == 2
    assert app._metrics.diagnostics["result_failure"] == 2
    assert app._diagnostic_history == [
        "result_failure: result failure#1: rate_limited",
        "result_failure: result failure#2: rate_limited",
    ]


@pytest.mark.asyncio
async def test_stresstest_status_shows_a_rejected_setting_literally() -> None:
    """A rejected setting value is echoed in the status line without markup parsing."""
    app = stresstest_module.create_stresstest_app()()
    hostile = "[bold]5[/bold] a[/]b"

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app._rows["records_total"]._input.value = hostile
        await pilot.click("#start")
        await pilot.pause(0.1)
        shown = app._status.visual.plain

    assert f"(got '{hostile}')" in shown


@pytest.mark.asyncio
async def test_stresstest_lists_an_unknown_diagnostic_with_its_raw_payload() -> None:
    """A hook name this TUI does not know is counted and shown, never a crash."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app._handle_diagnostic(app._active_run_token, "custom_probe", {"answer": 42}, 10)
        await pilot.pause(0.1)

    assert app._metrics.diagnostics["custom_probe"] == 1
    assert app._diagnostic_history == ["custom_probe: {'answer': 42}"]


@pytest.mark.asyncio
async def test_stresstest_summarises_a_known_diagnostic_and_skips_emitted() -> None:
    """A known diagnostic renders its summary; ``emitted`` is neither counted nor listed."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app._handle_diagnostic(app._active_run_token, "emitted", {}, 10)
        app._handle_diagnostic(app._active_run_token, "queue_full", {"logger": "app"}, 10)
        await pilot.pause(0.1)

    assert dict(app._metrics.diagnostics) == {"queue_full": 1}
    assert app._diagnostic_history == ["queue_full: queue full for logger app"]
