"""CLI behaviour coverage matching the rich-click adapter."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sys

import lib_cli_exit_tools
import pytest
import click
from click.testing import CliRunner

from lib_log_rich import __init__conf__
from lib_log_rich import cli as cli_mod
from lib_log_rich.lib_log_rich import summary_info
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

ANSI_RE = re.compile(r"\[[0-9;]*m")


@dataclass(frozen=True)
class CLIObservation:
    """Snapshot of a CLI invocation."""

    exit_code: int
    stdout: str
    exception: BaseException | None


def observe_cli(args: list[str] | None = None) -> CLIObservation:
    """Run the CLI with ``CliRunner`` and capture the outcome."""

    runner = CliRunner()
    original_argv = sys.argv
    sys.argv = [__init__conf__.shell_command]
    try:
        result = runner.invoke(
            cli_mod.cli,
            args or [],
            prog_name=__init__conf__.shell_command,
        )
    finally:
        sys.argv = original_argv
    return CLIObservation(result.exit_code, result.output, result.exception)


def observe_info_command() -> CLIObservation:
    """Invoke the ``info`` subcommand."""

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["info"])
    return CLIObservation(result.exit_code, result.output, result.exception)


def observe_no_traceback(monkeypatch: pytest.MonkeyPatch) -> CLIObservation:
    """Run ``--no-traceback`` and return post-run config state."""

    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", True, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", True, raising=False)
    outcome = observe_cli(["--no-traceback", "info"])
    return outcome


def observe_logdemo(theme: str) -> CLIObservation:
    """Invoke ``logdemo`` for ``theme`` and capture the result."""

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["logdemo", "--theme", theme])
    return CLIObservation(result.exit_code, result.output, result.exception)


def observe_hello_command() -> CLIObservation:
    """Call ``hello`` and capture the greeting."""

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["hello"])
    return CLIObservation(result.exit_code, result.output, result.exception)


def observe_fail_command() -> CLIObservation:
    """Call ``fail`` and capture the failure."""

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["fail"])
    return CLIObservation(result.exit_code, result.output, result.exception)


def observe_console_format(monkeypatch: pytest.MonkeyPatch) -> tuple[CLIObservation, dict[str, object]]:
    """Run ``logdemo`` with overridden console formatting and capture kwargs."""

    recorded: dict[str, object] = {}

    def fake_logdemo(**kwargs: object) -> dict[str, object]:  # noqa: ANN401
        recorded.update(kwargs)
        return {
            "theme": "classic",
            "styles": {},
            "events": [],
            "dump": None,
            "service": "svc",
            "environment": "env",
            "backends": {"graylog": False, "journald": False, "eventlog": False},
        }

    monkeypatch.setattr(cli_mod, "_logdemo", fake_logdemo)
    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        ["--console-format-preset", "short_loc", "logdemo"],
    )
    return CLIObservation(result.exit_code, result.output, result.exception), recorded


def observe_main_invocation(monkeypatch: pytest.MonkeyPatch, argv: list[str] | None = None) -> tuple[int, dict[str, bool]]:
    """Invoke ``main`` and capture the traceback flags afterwards."""

    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", True, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", True, raising=False)

    recorded: dict[str, bool] = {}

    def fake_run_cli(command: click.Command, argv_override: list[str] | None = None, *, prog_name: str | None = None, **_: object) -> int:
        runner = CliRunner()
        result = runner.invoke(command, argv_override or argv or ["hello"])
        if result.exception is not None:
            raise result.exception
        recorded["traceback"] = lib_cli_exit_tools.config.traceback
        recorded["traceback_force_color"] = lib_cli_exit_tools.config.traceback_force_color
        return result.exit_code

    monkeypatch.setattr(lib_cli_exit_tools, "run_cli", fake_run_cli)
    exit_code = cli_mod.main(argv)
    return exit_code, recorded


def strip_ansi(text: str) -> str:
    """Return ``text`` without ANSI colour codes."""

    return ANSI_RE.sub("", text)


def test_cli_root_exits_successfully() -> None:
    """The bare CLI returns success."""

    observation = observe_cli()
    assert observation.exit_code == 0


def test_cli_root_prints_the_summary() -> None:
    """The bare CLI prints the package summary."""

    observation = observe_cli()
    assert observation.stdout == summary_info()


def test_cli_info_exits_successfully() -> None:
    """The ``info`` subcommand exits with success."""

    observation = observe_info_command()
    assert observation.exit_code == 0


def test_cli_info_prints_the_summary() -> None:
    """The ``info`` subcommand mirrors the summary banner."""

    observation = observe_info_command()
    assert observation.stdout == summary_info()


def test_cli_no_traceback_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--no-traceback`` runs without error."""

    observation = observe_no_traceback(monkeypatch)
    assert observation.exit_code == 0


def test_cli_no_traceback_disables_traceback_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--no-traceback`` clears the traceback flag."""

    observe_no_traceback(monkeypatch)
    assert lib_cli_exit_tools.config.traceback is False


def test_cli_no_traceback_disables_traceback_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--no-traceback`` disables coloured tracebacks as well."""

    observe_no_traceback(monkeypatch)
    assert lib_cli_exit_tools.config.traceback_force_color is False


def test_cli_hello_returns_success() -> None:
    """The ``hello`` command exits cleanly."""

    observation = observe_hello_command()
    assert observation.exit_code == 0


def test_cli_logdemo_rejects_unknown_dump_format() -> None:
    """An unsupported dump format should trigger a CLI error."""

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["logdemo", "--dump-format", "yaml"])
    assert result.exit_code != 0
    message = strip_ansi(result.output)
    assert "Invalid value for '--dump-format'" in message


def test_cli_logdemo_requires_valid_graylog_endpoint() -> None:
    """Graylog endpoint must be HOST:PORT."""

    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        ["logdemo", "--enable-graylog", "--graylog-endpoint", "bad-endpoint"],
    )
    assert result.exit_code != 0
    message = strip_ansi(result.output)
    assert "Expected HOST:PORT" in message


def test_cli_filters_require_key_value_pairs() -> None:
    """Filter options without KEY=VALUE pairs are rejected."""

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["logdemo", "--context-exact", "invalid"])
    assert result.exit_code != 0
    assert "expects KEY=VALUE pairs" in result.output


def test_cli_hello_prints_greeting() -> None:
    """The ``hello`` command prints the greeting."""

    observation = observe_hello_command()
    assert observation.stdout.strip() == "Hello World"


def test_cli_fail_returns_failure() -> None:
    """The ``fail`` command signals failure via exit code."""

    observation = observe_fail_command()
    assert observation.exit_code != 0


def test_cli_fail_raises_runtime_error() -> None:
    """The ``fail`` command raises the documented ``RuntimeError``."""

    observation = observe_fail_command()
    assert isinstance(observation.exception, RuntimeError)


def test_cli_fail_message_mentions_the_contract() -> None:
    """The ``fail`` command surfaces the canonical error message."""

    observation = observe_fail_command()
    assert str(observation.exception) == "I should fail"


def test_cli_logdemo_exits_successfully() -> None:
    """``logdemo`` returns success for known themes."""

    observation = observe_logdemo("classic")
    assert observation.exit_code == 0


def test_cli_logdemo_prints_theme_header() -> None:
    """``logdemo`` announces the selected theme."""

    observation = observe_logdemo("classic")
    assert "=== Theme: classic ===" in strip_ansi(observation.stdout)


def test_cli_logdemo_mentions_event_emission() -> None:
    """``logdemo`` output mentions emitted events."""

    observation = observe_logdemo("classic")
    assert "emitted" in strip_ansi(observation.stdout)


def test_cli_console_preset_returns_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Switching the console preset exits successfully."""

    observation, _ = observe_console_format(monkeypatch)
    assert observation.exit_code == 0


def test_cli_console_preset_captures_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    """The preset propagates into the delegated call."""

    _observation, recorded = observe_console_format(monkeypatch)
    assert recorded["console_format_preset"] == "short_loc"


def test_cli_console_preset_leaves_template_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No custom template should be forwarded when only a preset was provided."""

    _observation, recorded = observe_console_format(monkeypatch)
    assert recorded["console_format_template"] is None


def test_main_restores_traceback_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running ``main`` keeps global traceback flags untouched after execution."""

    exit_code, _ = observe_main_invocation(monkeypatch)
    assert exit_code == 0


def test_main_leaves_traceback_flags_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running ``main`` preserves traceback preferences in the config."""

    _exit_code, recorded = observe_main_invocation(monkeypatch)
    assert recorded == {"traceback": True, "traceback_force_color": True}


def test_main_consumes_sys_argv(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """``main`` reads from ``sys.argv`` when no arguments are provided."""

    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", False, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", False, raising=False)
    monkeypatch.setattr(sys, "argv", [__init__conf__.shell_command, "hello"], raising=False)

    exit_code = cli_mod.main()
    capsys.readouterr()
    assert exit_code == 0


def test_main_outputs_greeting_when_sys_argv_requests_it(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """``main`` prints the greeting when ``sys.argv`` specifies ``hello``."""

    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", False, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", False, raising=False)
    monkeypatch.setattr(sys, "argv", [__init__conf__.shell_command, "hello"], raising=False)

    cli_mod.main()
    captured = capsys.readouterr()
    assert "Hello World" in captured.out
