"""CLI behaviour coverage matching the rich-click adapter."""

from __future__ import annotations

import importlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, cast

import click
import lib_cli_exit_tools
import pytest
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


def observe_cli(args: List[str] | None = None) -> CLIObservation:
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


def observe_main_invocation(monkeypatch: pytest.MonkeyPatch, argv: List[str] | None = None) -> tuple[int, dict[str, bool]]:
    """Invoke ``main`` and capture the traceback flags after execution."""
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", True, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", True, raising=False)

    if argv is None:
        monkeypatch.setattr(
            sys,
            "argv",
            [__init__conf__.shell_command, "hello"],
            raising=False,
        )

    exit_code = cli_mod.main(argv)
    final_state = {
        "traceback": bool(lib_cli_exit_tools.config.traceback),
        "traceback_force_color": bool(lib_cli_exit_tools.config.traceback_force_color),
    }
    return exit_code, final_state


def strip_ansi(text: str) -> str:
    """Return ``text`` without ANSI colour codes."""
    return ANSI_RE.sub("", text)


def reset_config_module() -> None:
    """Reload the config module so dotenv state returns to defaults."""
    importlib.reload(cli_mod.config_module)


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


def test_main_restores_traceback_preferences(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Running ``main`` keeps global traceback flags untouched after execution."""
    exit_code, final_state = observe_main_invocation(monkeypatch)
    capsys.readouterr()
    assert exit_code == 0
    assert final_state == {"traceback": True, "traceback_force_color": True}


def test_main_leaves_traceback_flags_unchanged(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Running ``main`` preserves traceback preferences in the config."""
    _exit_code, final_state = observe_main_invocation(monkeypatch)
    capsys.readouterr()
    assert final_state == {"traceback": True, "traceback_force_color": True}


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
    assert "Hello World" in strip_ansi(captured.out)


def test_cli_regex_invalid_pattern_reports_friendly_error() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["logdemo", "--extra-regex", "field=[", "--theme", "classic"])
    assert result.exit_code == 2
    assert "Invalid regular expression" in result.output


def test_parse_key_value_rejects_blank_key() -> None:
    with pytest.raises(click.BadParameter, match="non-empty key"):
        cast(Any, cli_mod)._parse_key_value("=value", "--context")


def test_collect_field_filters_merges_variants() -> None:
    filters = cast(Any, cli_mod)._collect_field_filters(
        option_prefix="--context",
        exact=["service=svc"],
        contains=["service=prod"],
        icontains=["service=PROD"],
        regex=["service=^svc"],
    )
    service_filters = filters["service"]
    assert isinstance(service_filters, list)
    assert len(cast(List[Any], service_filters)) == 4


def test_resolve_dump_path_uses_existing_directory(tmp_path: Path) -> None:
    base = tmp_path / "dumps"
    base.mkdir()
    # New signature: _resolve_dump_path(base, preset, theme, fmt)
    resolved = cast(Any, cli_mod)._resolve_dump_path(base, "short", "classic", "text")
    assert resolved.parent == base


def test_cli_root_hello_flag_returns_zero() -> None:
    observation = observe_cli(["--hello"])
    assert observation.exit_code == 0


def test_cli_root_hello_flag_sings_hello_world() -> None:
    observation = observe_cli(["--hello"])
    assert strip_ansi(observation.stdout).startswith("Hello World")


def test_cli_logdemo_honours_preset_option() -> None:
    observation = observe_cli(
        [
            "logdemo",
            "--preset",
            "short_loc",
            "--theme",
            "classic",
        ]
    )
    assert observation.exit_code == 0


def test_cli_logdemo_honours_console_template_option() -> None:
    observation = observe_cli(
        [
            "logdemo",
            "--theme",
            "classic",
            "--console-format-template",
            "{message}",
        ]
    )
    assert observation.exit_code == 0


def test_cli_logdemo_filters_context_down_to_empty_dump() -> None:
    observation = observe_cli(
        [
            "logdemo",
            "--preset",
            "short",
            "--theme",
            "classic",
            "--dump-format",
            "json",
            "--context-exact",
            "job=never-match",
        ]
    )
    assert "--- dump (json) preset=short theme=classic ---\n[]" in strip_ansi(observation.stdout)


def test_cli_logdemo_dump_path_suffixes_combo_when_file_given(tmp_path: Path) -> None:
    target = tmp_path / "artifacts" / "demo.log"
    _ = observe_cli(
        [
            "logdemo",
            "--preset",
            "short",
            "--theme",
            "classic",
            "--dump-format",
            "text",
            "--dump-path",
            str(target),
        ]
    )
    expected = target.parent / "demo-short-classic.log"
    assert expected.exists()


def test_cli_logdemo_dump_path_uses_directory_when_provided(tmp_path: Path) -> None:
    directory = tmp_path / "exports"
    _ = observe_cli(
        [
            "logdemo",
            "--preset",
            "short",
            "--theme",
            "classic",
            "--dump-format",
            "json",
            "--dump-path",
            str(directory),
        ]
    )
    expected = directory / "logdemo-short-classic.json"
    assert expected.exists()


def test_cli_logdemo_emits_dump_payload_when_not_writing_to_disk() -> None:
    observation = observe_cli(
        [
            "logdemo",
            "--preset",
            "short",
            "--theme",
            "classic",
            "--dump-format",
            "json",
        ]
    )
    assert "--- dump (json) preset=short theme=classic ---" in observation.stdout


def test_cli_use_dotenv_flag_loads_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    reset_config_module()
    with runner.isolated_filesystem():
        Path(".env").write_text("LOG_SERVICE=from-dotenv\n", encoding="utf-8")
        runner.invoke(cli_mod.cli, ["--use-dotenv"])
    reset_config_module()
    assert (os.environ.get("LOG_SERVICE") or "").strip() == "from-dotenv"


def test_cli_env_toggle_triggers_dotenv_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    reset_config_module()
    with runner.isolated_filesystem():
        Path(".env").write_text("LOG_SERVICE=from-env-toggle\n", encoding="utf-8")
        runner.invoke(
            cli_mod.cli,
            [],
            env={cli_mod.config_module.DOTENV_ENV_VAR: "1"},
        )
    reset_config_module()
    assert (os.environ.get("LOG_SERVICE") or "").strip() == "from-env-toggle"
