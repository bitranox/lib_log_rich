"""End-to-end coverage for the stresstest CLI command.

The parsing helpers are pinned by the module's own doctests; these tests drive
the real Textual app through ``create_stresstest_app``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from textual.widgets.select import InvalidSelectValueError

from lib_log_rich import cli as cli_mod
from lib_log_rich import cli_stresstest as stresstest_module
from lib_log_rich.domain import DumpFormat
from lib_log_rich.domain.enums import FormatPreset
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib_log_rich.application.ports import ConsolePort
    from lib_log_rich.domain import LogEvent
    from lib_log_rich.runtime.settings import ConsoleAppearance

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
async def test_stresstest_dump_preset_select_offers_only_the_presets_a_text_dump_renders() -> None:
    """The icon preset has no text dump template, so the dump preset select must not offer it."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        dump_preset = app._rows["dump_format_preset"]._input
        for preset in FormatPreset:
            if preset.renders_in_text_dump:
                dump_preset.value = preset.value
            else:
                with pytest.raises(InvalidSelectValueError):
                    dump_preset.value = preset.value
        app._rows["console_format_preset"]._input.value = FormatPreset.SHORT_LOC_ICON.value


@pytest.mark.asyncio
async def test_stresstest_no_longer_offers_a_yaml_dump() -> None:
    """The library has no YAML dump, so the form must not offer one."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        with pytest.raises(InvalidSelectValueError):
            app._rows["dump_format"]._input.value = "yaml"


@pytest.mark.asyncio
@pytest.mark.parametrize("dump_format", [member.value for member in DumpFormat])
async def test_stresstest_captures_a_dump_in_every_offered_format(dump_format: str) -> None:
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="3", log_level="INFO", dump_format=dump_format)

    assert app._metrics.emitted == 3
    assert "Ring buffer dump captured." in _operation_messages(app)


@pytest.mark.asyncio
async def test_stresstest_lists_each_rate_limited_record_as_a_result_failure() -> None:
    """A record the rate limiter refuses is counted and listed with its index and drop reason.

    Each refusal shows twice: the limiter's own ``rate_limited`` diagnostic, then the
    TUI's ``result_failure`` for the record.
    """
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="3", log_level="INFO", rate_limit="1:60")

    assert app._metrics.failed == 2
    assert app._metrics.diagnostics["result_failure"] == 2
    assert app._diagnostic_history == [
        "rate_limited: rate limited logger stress.tui",
        "result_failure: result failure#1: rate_limited",
        "rate_limited: rate limited logger stress.tui",
        "result_failure: result failure#2: rate_limited",
    ]


class _ExplodingConsole:
    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        raise RuntimeError("console exploded")

    def flush(self) -> None:
        return None


def _exploding_console(appearance: ConsoleAppearance) -> ConsolePort:
    return _ExplodingConsole()


@pytest.mark.asyncio
async def test_stresstest_shuts_down_after_a_failing_console_with_the_queue_on() -> None:
    """The queue worker reports ``adapter_error`` through call_from_thread while the app awaits shutdown."""
    base = stresstest_module.create_stresstest_app()

    class ExplodingConsoleApp(base):
        def _console_adapter_factory(self, width_hint: int | None) -> Callable[[ConsoleAppearance], ConsolePort]:
            return _exploding_console

    app = ExplodingConsoleApp()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="2", log_level="INFO", queue_stop_timeout="2")

    assert not any("shutdown async failed" in message for message in _operation_messages(app))
    assert app._metrics.diagnostics["adapter_error"] >= 1
    assert not app._runtime.is_initialised()


@pytest.mark.asyncio
async def test_stresstest_counts_a_diagnostic_sent_on_the_app_thread() -> None:
    """The rate limiter reports on the caller's thread, which here is the app's own."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="3", log_level="INFO", rate_limit="1:60")

    assert app._metrics.diagnostics["rate_limited"] == 2


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
async def test_stresstest_summarises_a_known_diagnostic_and_skips_the_per_record_ones() -> None:
    """A known diagnostic renders its summary; ``emitted`` and ``queued`` are neither counted nor listed."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app._handle_diagnostic(app._active_run_token, "emitted", {}, 10)
        app._handle_diagnostic(app._active_run_token, "queued", {"event_id": "e1"}, 10)
        app._handle_diagnostic(app._active_run_token, "queue_full", {"logger": "app"}, 10)
        await pilot.pause(0.1)

    assert dict(app._metrics.diagnostics) == {"queue_full": 1}
    assert app._diagnostic_history == ["queue_full: queue full for logger app"]


@pytest.mark.asyncio
async def test_stresstest_cycle_mode_emits_every_level_in_order() -> None:
    """Cycle mode walks DEBUG through CRITICAL, one level per record, and the ring buffer keeps each level."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="5", log_level="CYCLE", dump_format="json", dump_level="", context_fields="0", extra_fields="0")

    assert app._metrics.emitted == 5
    assert app._metrics.failed == 0
    assert any(message.endswith("(cycle: DEBUG -> INFO -> WARNING -> ERROR -> CRITICAL)") for message in _operation_messages(app))
    dumped = "\n".join(strip.text for strip in app._dump_log.lines)
    positions = [dumped.find(f'"{name}"') for name in ("debug", "info", "warning", "error", "critical")]
    assert all(position >= 0 for position in positions), dumped
    assert positions == sorted(positions)


@pytest.mark.asyncio
async def test_stresstest_each_output_toggle_switches_only_its_own_pane() -> None:
    """Unticking a toggle hides the console pane it names and leaves the other pane enabled."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        app.query_one("#toggle-console-queued").value = False
        await pilot.pause(0.1)
        after_queued = (app._queued_output_enabled, app._async_output_enabled)
        app.query_one("#toggle-console-async").value = False
        await pilot.pause(0.1)
        after_async = (app._queued_output_enabled, app._async_output_enabled)

    assert after_queued == (False, True)
    assert after_async == (False, False)


@pytest.mark.asyncio
async def test_stresstest_control_buttons_restore_after_a_run_and_clear_empties_the_logs() -> None:
    """After a run Start is enabled and Stop disabled again; Clear Logs leaves only its own notice."""
    app = stresstest_module.create_stresstest_app()()

    async with app.run_test() as pilot:
        await _run_once(app, pilot, records_total="2", log_level="INFO")
        start_disabled = app.query_one("#start").disabled
        stop_disabled = app.query_one("#stop").disabled
        await pilot.click("#clear")
        await pilot.pause(0.1)

    assert (start_disabled, stop_disabled) == (False, True)
    assert _operation_messages(app) == ["Logs cleared."]
    assert app._dump_log.lines == []
