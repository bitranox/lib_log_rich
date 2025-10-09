"""CLI behaviour coverage matching the rich-click adapter."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, cast

import lib_cli_exit_tools
import pytest
import click
from click.testing import CliRunner

from lib_log_rich import __init__conf__
from lib_log_rich import cli as cli_mod
from lib_log_rich import __main__ as module_main
from lib_log_rich.lib_log_rich import summary_info
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

ANSI_RE = re.compile(r"\[[0-9;]*m")


CollectFilters = Callable[..., Dict[str, Any]]

_collect_field_filters: CollectFilters = getattr(cli_mod, "_collect_field_filters")
_resolve_dump_path: Callable[[Path, str, str], Path] = getattr(cli_mod, "_resolve_dump_path")
_extract_dotenv_flag: Callable[[List[str] | None], bool | None] = getattr(module_main, "_extract_dotenv_flag")
_maybe_enable_dotenv: Callable[[List[str] | None], None] = getattr(module_main, "_maybe_enable_dotenv")
_module_main: Callable[[List[str] | None], int] = getattr(module_main, "_module_main")


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


def observe_main_invocation(monkeypatch: pytest.MonkeyPatch, argv: List[str] | None = None) -> tuple[int, dict[str, bool]]:
    """Invoke ``main`` and capture the traceback flags afterwards."""

    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback", True, raising=False)
    monkeypatch.setattr(lib_cli_exit_tools.config, "traceback_force_color", True, raising=False)

    recorded: dict[str, bool] = {}

    def fake_run_cli(command: click.Command, argv_override: List[str] | None = None, *, prog_name: str | None = None, **_: object) -> int:
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


def test_cli_regex_invalid_pattern_reports_friendly_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_logdemo(**_: object) -> None:  # pragma: no cover - safety net
        raise AssertionError("logdemo should not run for invalid regex")

    monkeypatch.setattr(cli_mod, "_logdemo", fail_logdemo)
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["logdemo", "--extra-regex", "field=[", "--theme", "classic"])

    assert result.exit_code == 2
    assert result.exception is not None
    assert not isinstance(result.exception, re.error)
    assert "Invalid regular expression" in result.output


def test_collect_field_filters_handles_multiple() -> None:
    filters = _collect_field_filters(
        option_prefix="context",
        exact=["job=alpha", "job=beta"],
        contains=["service=api"],
        regex=["user=^svc"],
    )
    assert filters["job"] == ["alpha", "beta"]

    service_spec = filters["service"]
    service_payload = cast(Dict[str, Any], service_spec[0] if isinstance(service_spec, list) else service_spec)
    assert service_payload == {"contains": "api"}

    user_spec = filters["user"]
    user_payload = cast(Dict[str, Any], user_spec[0] if isinstance(user_spec, list) else user_spec)
    pattern = cast(re.Pattern[str], user_payload["pattern"])
    assert pattern.pattern == "^svc"


def test_collect_field_filters_appends_to_existing_list() -> None:
    filters = _collect_field_filters(option_prefix="extra", exact=["key=one", "key=two", "key=three"])
    assert isinstance(filters["key"], list)
    assert filters["key"] == ["one", "two", "three"]


def test_resolve_dump_path_adds_theme_suffix(tmp_path: Path) -> None:
    base = tmp_path / "artifacts" / "demo.log"
    result = _resolve_dump_path(base, "classic", "text")
    expected = base.parent / "demo-classic.log"
    assert result == expected
    assert result.parent.exists()


def test_resolve_dump_path_handles_directory(tmp_path: Path) -> None:
    base = tmp_path / "artifacts"
    base.mkdir()
    result = _resolve_dump_path(base, "classic", "json")
    assert result == base / "logdemo-classic.json"


def test_resolve_dump_path_creates_directory(tmp_path: Path) -> None:
    base = tmp_path / "dumps"
    result = _resolve_dump_path(base, "classic", "text")
    assert result == base / "logdemo-classic.log"
    assert base.exists()


def test_extract_dotenv_flag_prefers_last() -> None:
    argv = ["--use-dotenv", "info", "--no-use-dotenv", "logdemo", "--use-dotenv"]
    assert _extract_dotenv_flag(argv) is True


def test_maybe_enable_dotenv_honours_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: List[Path] = []

    def fake_enable_dotenv(*, search_from: Path | None = None) -> None:
        calls.append(search_from or tmp_path)

    monkeypatch.setattr(cli_mod.config_module, "enable_dotenv", fake_enable_dotenv)
    monkeypatch.setenv(cli_mod.config_module.DOTENV_ENV_VAR, "1")
    _maybe_enable_dotenv(["--use-dotenv"])
    assert calls


def test_module_main_restores_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    original_traceback = lib_cli_exit_tools.config.traceback
    original_force_color = lib_cli_exit_tools.config.traceback_force_color

    def fake_main(*, argv: List[str] | None = None, restore_traceback: bool = False) -> None:
        raise RuntimeError("boom")

    seen: dict[str, object] = {}

    def fake_print_exception_message(**kwargs: object) -> None:
        seen["kwargs"] = kwargs

    monkeypatch.setattr(cli_mod, "main", fake_main)
    monkeypatch.setattr(lib_cli_exit_tools, "print_exception_message", fake_print_exception_message)

    def fake_exit_code(exc: BaseException) -> int:
        return 23

    monkeypatch.setattr(lib_cli_exit_tools, "get_system_exit_code", fake_exit_code)
    lib_cli_exit_tools.config.traceback = original_traceback
    lib_cli_exit_tools.config.traceback_force_color = original_force_color

    exit_code = _module_main(["fail"])
    assert exit_code == 23
    assert seen
    assert lib_cli_exit_tools.config.traceback is original_traceback
    assert lib_cli_exit_tools.config.traceback_force_color is original_force_color

    lib_cli_exit_tools.config.traceback = original_traceback
    lib_cli_exit_tools.config.traceback_force_color = original_force_color


def test_cli_root_options_inherit_into_logdemo(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_logdemo(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        current = click.get_current_context()
        captured["ctx_obj"] = dict(current.obj or {})
        return {"events": [object()], "dump": None, "service": "svc", "environment": "env"}

    monkeypatch.setattr(cli_mod, "_logdemo", fake_logdemo)
    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        [
            "--console-format-preset",
            "short_loc",
            "--console-format-template",
            "{message}",
            "--queue-stop-timeout",
            "2.5",
            "logdemo",
            "--theme",
            "classic",
        ],
    )
    assert result.exit_code == 0
    assert captured["console_format_preset"] == "short_loc"
    assert captured["console_format_template"] == "{message}"
    ctx_obj = cast(dict[str, object], captured["ctx_obj"])
    assert ctx_obj["queue_stop_timeout"] == 2.5


def test_cli_logdemo_reports_backends_and_dump_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dump_file = tmp_path / "artifacts" / "demo.json"
    dump_payload = '{"events": []}'

    def fake_logdemo(**kwargs: object) -> dict[str, object]:
        assert kwargs["enable_graylog"] is True
        assert kwargs["enable_journald"] is True
        assert kwargs["enable_eventlog"] is True
        assert kwargs["graylog_protocol"] == "udp"
        return {"events": [1, 2], "dump": dump_payload}

    monkeypatch.setattr(cli_mod, "_logdemo", fake_logdemo)
    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        [
            "logdemo",
            "--theme",
            "classic",
            "--dump-format",
            "json",
            "--dump-path",
            str(dump_file),
            "--enable-graylog",
            "--graylog-endpoint",
            "gray.example:12201",
            "--graylog-protocol",
            "udp",
            "--enable-journald",
            "--enable-eventlog",
        ],
    )
    assert result.exit_code == 0
    text = result.output
    assert "graylog -> gray.example:12201 via UDP" in text
    assert "journald -> systemd.journal.send" in text
    assert "eventlog -> Windows Event Log" in text
    assert (tmp_path / "artifacts").exists()


def test_cli_logdemo_prints_dump_payload_when_no_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_logdemo(**kwargs: object) -> dict[str, object]:
        return {"events": [1], "dump": "payload"}

    monkeypatch.setattr(cli_mod, "_logdemo", fake_logdemo)
    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        [
            "logdemo",
            "--theme",
            "classic",
            "--dump-format",
            "json",
        ],
    )
    assert result.exit_code == 0
    assert "--- dump (json) theme=classic ---" in result.output
    assert "payload" in result.output
