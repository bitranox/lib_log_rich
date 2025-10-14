from __future__ import annotations

import importlib
import os
import runpy
import sys
from pathlib import Path

import pytest

from lib_log_rich import __main__ as module_main
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def test_module_main_returns_success_for_hello(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = module_main.main(["hello"])
    capsys.readouterr()
    assert exit_code == 0


def test_module_main_prints_greeting_for_hello(capsys: pytest.CaptureFixture[str]) -> None:
    module_main.main(["hello"])
    captured = capsys.readouterr()
    assert "Hello World" in captured.out


def test_module_main_reports_failure_for_fail_command(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = module_main.main(["fail"])
    capsys.readouterr()
    assert exit_code != 0


def test_module_main_use_dotenv_flag_loads_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    importlib.reload(module_main.config_module)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("LOG_SERVICE=dot-main\n", encoding="utf-8")
    monkeypatch.chdir(project)
    module_main.main(["--use-dotenv", "info"])
    importlib.reload(module_main.config_module)
    assert (os.environ.get("LOG_SERVICE") or "").strip() == "dot-main"


def test_module_main_no_use_dotenv_flag_leaves_environment_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    importlib.reload(module_main.config_module)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("LOG_SERVICE=ignored\n", encoding="utf-8")
    monkeypatch.chdir(project)
    module_main.main(["--no-use-dotenv", "info"])
    importlib.reload(module_main.config_module)
    assert os.environ.get("LOG_SERVICE") is None


def test_module_guard_raises_system_exit(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["lib_log_rich", "hello"], raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("lib_log_rich.__main__", run_name="__main__")
    capsys.readouterr()
    assert exit_info.value.code == 0
