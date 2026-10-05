"""Module entry stories ensuring ``python -m lib_log_rich`` mirrors the CLI."""

from __future__ import annotations

import importlib
import os
import runpy
import sys
from typing import TYPE_CHECKING

import pytest

from lib_log_rich import cli as cli_module
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from types import ModuleType

pytestmark = [OS_AGNOSTIC]


@pytest.fixture
def module_main() -> Iterator[ModuleType]:
    """Import ``lib_log_rich.__main__`` and drop it afterwards.

    Left in ``sys.modules``, every later ``runpy`` of the module warns that it is already loaded.
    """
    previous = sys.modules.pop("lib_log_rich.__main__", None)
    module = importlib.import_module("lib_log_rich.__main__")
    try:
        yield module
    finally:
        sys.modules.pop("lib_log_rich.__main__", None)
        if previous is not None:
            sys.modules["lib_log_rich.__main__"] = previous


def _run_module(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["lib_log_rich", *argv], raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("lib_log_rich.__main__", run_name="__main__")
    code = exit_info.value.code
    assert isinstance(code, int)
    return code


def test_main_delegate_returns_success_for_hello(module_main: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = module_main.main(["hello"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Hello World" in captured.out


def test_main_delegate_reports_failure_for_fail_command(module_main: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = module_main.main(["fail"])
    capsys.readouterr()
    assert exit_code != 0


@pytest.mark.parametrize("argv", [["hello"], ["fail"], ["--bad-flag"], ["no-such-command"], ["--help"]])
def test_module_entry_exits_with_the_code_cli_main_gives(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    expected = cli_module.main(argv)
    capsys.readouterr()
    assert _run_module(monkeypatch, argv) == expected


@pytest.mark.parametrize("argv", [["--bad-flag"], ["no-such-command"]], ids=["bad-flag", "unknown-command"])
def test_a_usage_error_exits_two_with_clicks_usage_message(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    assert cli_module.main(argv) == 2
    assert "Usage:" in capsys.readouterr().err
    assert _run_module(monkeypatch, argv) == 2


def test_module_entry_runs_cli_main_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_main(argv: object = None) -> int:
        seen.append(argv)
        return 7

    monkeypatch.setattr(cli_module, "main", fake_main)
    assert _run_module(monkeypatch, ["hello"]) == 7
    assert seen == [None]


def test_use_dotenv_flag_loads_values_through_module_entry(module_main: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    importlib.reload(cli_module.config_module)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("LOG_SERVICE=dot-main\n", encoding="utf-8")
    monkeypatch.chdir(project)
    module_main.main(["--use-dotenv", "info"])
    importlib.reload(cli_module.config_module)
    assert (os.environ.get("LOG_SERVICE") or "").strip() == "dot-main"


def test_no_use_dotenv_flag_leaves_environment_clean(module_main: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    importlib.reload(cli_module.config_module)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("LOG_SERVICE=ignored\n", encoding="utf-8")
    monkeypatch.chdir(project)
    module_main.main(["--no-use-dotenv", "info"])
    importlib.reload(cli_module.config_module)
    assert os.environ.get("LOG_SERVICE") is None


def test_importing_the_module_runs_nothing(module_main: ModuleType) -> None:
    assert module_main.cli is cli_module
