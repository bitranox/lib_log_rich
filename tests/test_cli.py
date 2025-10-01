"""CLI behaviour coverage matching the rich-click adapter."""

from __future__ import annotations

import re
import sys
from typing import Callable

import lib_cli_exit_tools
import pytest
from click.testing import CliRunner

from lib_log_rich import __init__conf__
from lib_log_rich import cli as cli_mod
from lib_log_rich.lib_log_rich import summary_info

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Return ``text`` without ANSI colour codes.

    Examples
    --------
    >>> strip_ansi("\x1b[31mred\x1b[0m")
    'red'
    """

    return ANSI_RE.sub("", text)


def run_cli(args: list[str] | None = None) -> tuple[int, str, BaseException | None]:
    """Invoke the rich-click command with ``CliRunner`` and capture output."""

    import sys

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
    return result.exit_code, result.output, result.exception


def test_cli_without_subcommand_prints_summary() -> None:
    exit_code, stdout, _ = run_cli()

    assert exit_code == 0
    assert stdout == summary_info()


def test_cli_info_command_matches_summary() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["info"])

    assert result.exit_code == 0
    assert result.output == summary_info()


def test_cli_no_traceback_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-traceback` should disable verbose tracebacks for subsequent commands."""

    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", True, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", True, raising=False)

    exit_code, _stdout, _exception = run_cli(["--no-traceback", "info"])

    assert exit_code == 0
    assert lib_cli_exit_tools.config.traceback is False
    assert lib_cli_exit_tools.config.traceback_force_color is False


def test_cli_hello_and_fail_commands() -> None:
    runner = CliRunner()

    success = runner.invoke(cli_mod.cli, ["hello"])
    assert success.exit_code == 0
    assert success.output.strip() == "Hello World"

    failure = runner.invoke(cli_mod.cli, ["fail"])
    assert failure.exit_code != 0
    assert isinstance(failure.exception, RuntimeError)
    assert str(failure.exception) == "I should fail"


def test_cli_logdemo_runs_for_single_theme() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["logdemo", "--theme", "classic"])

    assert result.exit_code == 0
    plain_output = strip_ansi(result.output)
    assert "=== Theme: classic ===" in plain_output
    assert "emitted" in plain_output


def test_cli_global_console_format_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert result.exit_code == 0
    assert recorded["console_format_preset"] == "short_loc"
    assert recorded["console_format_template"] is None


def test_main_restores_traceback_preferences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", True, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", True, raising=False)

    recorded: dict[str, bool] = {}

    def fake_run_cli(command: Callable[..., int], argv: list[str] | None = None, *, prog_name: str | None = None, **_: object) -> int:
        runner = CliRunner()
        result = runner.invoke(command, ["hello"] if argv is None else argv)
        if result.exception is not None:
            raise result.exception
        recorded["traceback"] = lib_cli_exit_tools.config.traceback
        recorded["traceback_force_color"] = lib_cli_exit_tools.config.traceback_force_color
        return result.exit_code

    monkeypatch.setattr(lib_cli_exit_tools, "run_cli", fake_run_cli)

    exit_code = cli_mod.main(["hello"])

    assert exit_code == 0
    assert lib_cli_exit_tools.config.traceback is True
    assert lib_cli_exit_tools.config.traceback_force_color is True
    assert recorded == {"traceback": True, "traceback_force_color": True}


def test_main_consumes_sys_argv(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", False, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", False, raising=False)
    monkeypatch.setattr(sys, "argv", [__init__conf__.shell_command, "hello"], raising=False)

    exit_code = cli_mod.main()
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Hello World" in captured.out
