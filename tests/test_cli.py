"""CLI behaviour coverage matching the rich-click adapter."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import click
import lib_cli_exit_tools
import pytest
from click.testing import CliRunner

from lib_log_rich import __init__conf__
from lib_log_rich import cli as cli_mod
from lib_log_rich.domain import DumpFormat
from lib_log_rich.domain.enums import ConsoleTheme, FormatPreset, GraylogProtocol
from lib_log_rich.lib_log_rich import summary_info
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [OS_AGNOSTIC]

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


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


def observe_main_invocation(monkeypatch: pytest.MonkeyPatch, argv: list[str] | None = None) -> tuple[int, dict[str, bool]]:
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
        cast("Any", cli_mod)._parse_key_value("=value", "--context")


def test_collect_field_filters_merges_variants() -> None:
    filters = cast("Any", cli_mod)._collect_field_filters(
        option_prefix="--context",
        exact=["service=svc"],
        contains=["service=prod"],
        icontains=["service=PROD"],
        regex=["service=^svc"],
    )
    service_filters = filters["service"]
    assert isinstance(service_filters, list)
    assert len(cast("list[Any]", service_filters)) == 4


def test_resolve_dump_path_uses_existing_directory(tmp_path: Path) -> None:
    base = tmp_path / "dumps"
    base.mkdir()
    resolved = cast("Any", cli_mod)._resolve_dump_path(base, FormatPreset.SHORT, "classic", DumpFormat.TEXT)
    assert resolved == base / "logdemo-short-classic.log"


def test_print_backend_status_adds_tls_only_for_tcp(capsys: pytest.CaptureFixture[str]) -> None:
    """The scheme names the protocol in upper case and marks TLS only on TCP, which is the only TLS transport."""
    status = cast("Any", cli_mod)._print_backend_status
    common: dict[str, Any] = {"enable_journald": False, "enable_eventlog": False, "endpoint_tuple": ("gl.local", 12201), "graylog_tls": True}
    status(enable_graylog=True, graylog_protocol=GraylogProtocol.TCP, **common)
    status(enable_graylog=True, graylog_protocol=GraylogProtocol.UDP, **common)
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["  graylog -> gl.local:12201 via TCP+TLS", "  graylog -> gl.local:12201 via UDP"]


def test_cli_logdemo_rejects_unknown_graylog_protocol() -> None:
    observation = observe_cli(["logdemo", "--theme", "classic", "--graylog-protocol", "http"])
    assert observation.exit_code == 2
    assert "Invalid value for '--graylog-protocol'" in strip_ansi(observation.stdout)


def test_cli_logdemo_accepts_preset_in_any_case() -> None:
    """``--preset`` matches case-insensitively and reports the preset by its value."""
    observation = observe_cli(["logdemo", "--preset", "SHORT_LOC", "--theme", "classic"])
    assert observation.exit_code == 0
    assert "=== Preset: short_loc, Theme: classic ===" in strip_ansi(observation.stdout)


def test_cli_logdemo_accepts_theme_in_any_case() -> None:
    observation = observe_cli(["logdemo", "--preset", "short", "--theme", "NEON"])
    assert observation.exit_code == 0
    assert "=== Preset: short, Theme: neon ===" in strip_ansi(observation.stdout)


def test_cli_logdemo_refuses_a_console_only_dump_preset_listing_the_dump_presets() -> None:
    # A text dump has no template for the icon preset; offered here, it failed at the dump.
    observation = observe_cli(["logdemo", "--dump-format", "text", "--dump-format-preset", "short_loc_icon"])
    assert observation.exit_code == 2
    output = strip_ansi(observation.stdout)
    assert "Invalid value for '--dump-format-preset': 'short_loc_icon'" in output
    assert all(f"'{preset.value}'" in output for preset in FormatPreset if preset.renders_in_text_dump)


def test_cli_logdemo_refuses_an_unknown_theme_listing_the_valid_names() -> None:
    observation = observe_cli(["logdemo", "--theme", "nope"])
    assert observation.exit_code == 2
    output = strip_ansi(observation.stdout)
    # rich-click boxes and wraps the message at the runner's width, so check its parts.
    assert "Invalid value for '--theme': 'nope'" in output
    assert all(f"'{theme.value}'" in output for theme in ConsoleTheme)


def test_cli_logdemo_runs_every_theme_by_default() -> None:
    observation = observe_cli(["logdemo", "--preset", "short"])
    headers = [line for line in strip_ansi(observation.stdout).splitlines() if line.startswith("=== Preset:")]
    assert headers == [f"=== Preset: short, Theme: {theme.value} ===" for theme in ConsoleTheme]


def test_cli_logdemo_runs_every_preset_by_default() -> None:
    observation = observe_cli(["logdemo", "--theme", "classic"])
    headers = [line for line in strip_ansi(observation.stdout).splitlines() if line.startswith("=== Preset:")]
    assert headers == [f"=== Preset: {preset.value}, Theme: classic ===" for preset in FormatPreset]


def test_cli_logdemo_regex_filter_keeps_matching_events() -> None:
    observation = observe_cli(
        ["logdemo", "--preset", "short", "--theme", "classic", "--dump-format", "json", "--extra-regex", "theme=^class"],
    )
    output = strip_ansi(observation.stdout)
    payload = output.split("--- dump (json) preset=short theme=classic ---\n", 1)[1]
    events = json.loads(payload)
    assert [event["extra"]["theme"] for event in events] == ["classic"] * 5


def test_cli_logdemo_regex_filter_drops_non_matching_events() -> None:
    observation = observe_cli(
        ["logdemo", "--preset", "short", "--theme", "classic", "--dump-format", "json", "--extra-regex", "theme=^nomatch$"],
    )
    assert "--- dump (json) preset=short theme=classic ---\n[]" in strip_ansi(observation.stdout)


def test_cli_logdemo_html_txt_dump_gets_html_extension(tmp_path: Path) -> None:
    directory = tmp_path / "exports"
    observe_cli(
        ["logdemo", "--preset", "short", "--theme", "classic", "--dump-format", "html_txt", "--dump-path", str(directory)],
    )
    assert (directory / "logdemo-short-classic.html").exists()


def test_cli_root_stores_typed_options_for_subcommands() -> None:
    """The root group hands its options down as a typed object."""
    seen: list[object] = []

    @cli_mod.cli.command("probe-root-options")
    @click.pass_context
    def probe(ctx: click.Context) -> None:
        seen.append(ctx.obj)

    try:
        observation = observe_cli(["--console-format-template", "{message}", "probe-root-options"])
    finally:
        cli_mod.cli.commands.pop("probe-root-options", None)
    assert observation.exit_code == 0
    root_options = cast("Any", cli_mod)._RootOptions
    assert seen == [root_options(traceback=True, console_format_template="{message}")]


_ROOT_VALUE_OPTIONS: list[tuple[str, str]] = [
    ("console-format-template", "{message}"),
]


@pytest.mark.parametrize(("flag", "value"), [("--console-format-preset", "full"), ("--queue-stop-timeout", "1")])
def test_cli_root_refuses_the_options_no_subcommand_read(flag: str, value: str) -> None:
    """The root group no longer offers options it stored but no subcommand used."""
    observation = observe_cli([flag, value, "info"])
    assert observation.exit_code == 2
    assert f"No such option '{flag}'" in strip_ansi(observation.stdout)


@pytest.mark.parametrize(("kebab_name", "value"), _ROOT_VALUE_OPTIONS)
def test_cli_root_refuses_underscore_option_spelling(kebab_name: str, value: str) -> None:
    """Only the kebab-case spelling is registered; the underscore spelling is an unknown option."""
    underscore_flag = "--" + kebab_name.replace("-", "_")
    observation = observe_cli([underscore_flag, value, "info"])
    assert observation.exit_code == 2
    assert f"No such option '{underscore_flag}'" in strip_ansi(observation.stdout)


@pytest.mark.parametrize(("kebab_name", "value"), _ROOT_VALUE_OPTIONS)
def test_cli_root_accepts_kebab_option_spelling(kebab_name: str, value: str) -> None:
    observation = observe_cli([f"--{kebab_name}", value, "info"])
    assert observation.exit_code == 0


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


@pytest.mark.usefixtures("fresh_dotenv")
def test_cli_use_dotenv_flag_loads_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOG_SERVICE=from-dotenv\n", encoding="utf-8")
    runner.invoke(cli_mod.cli, ["--use-dotenv"])
    assert (os.environ.get("LOG_SERVICE") or "").strip() == "from-dotenv"


@pytest.mark.usefixtures("fresh_dotenv")
def test_cli_env_toggle_triggers_dotenv_loading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = CliRunner()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOG_SERVICE=from-env-toggle\n", encoding="utf-8")
    runner.invoke(
        cli_mod.cli,
        [],
        env={cli_mod.config_module.DOTENV_ENV_VAR: "1"},
    )
    assert (os.environ.get("LOG_SERVICE") or "").strip() == "from-env-toggle"
