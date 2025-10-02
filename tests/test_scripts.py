from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Callable

import pytest

pytest.importorskip("click")
from click.testing import CliRunner

import scripts.build as build
import scripts.dev as dev
import scripts.install as install
import scripts.test as test_script
from scripts import _utils
from scripts._utils import RunResult
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


@dataclass(frozen=True)
class ScriptObservation:
    """Capture the exit code and recorded commands for a script run."""

    exit_code: int
    commands: list[tuple[object, dict]]


def _make_run_recorder(record: list[tuple[object, dict]]) -> Callable[..., RunResult]:
    def _run(cmd, *, check=True, capture=True, cwd=None, env=None, dry_run=False):
        record.append(
            (
                cmd,
                {
                    "check": check,
                    "capture": capture,
                    "cwd": cwd,
                    "env": env,
                    "dry_run": dry_run,
                },
            )
        )
        return RunResult(0, "", "")

    return _run


def observe_build(monkeypatch: pytest.MonkeyPatch) -> ScriptObservation:
    recorded: list[tuple[object, dict]] = []
    monkeypatch.setattr(build, "run", _make_run_recorder(recorded))
    monkeypatch.setattr(build, "cmd_exists", lambda name: True)
    runner = CliRunner()
    result = runner.invoke(build.main, [])
    return ScriptObservation(result.exit_code, recorded)


def observe_dev(monkeypatch: pytest.MonkeyPatch) -> ScriptObservation:
    recorded: list[tuple[object, dict]] = []
    monkeypatch.setattr(dev, "run", _make_run_recorder(recorded))
    runner = CliRunner()
    result = runner.invoke(dev.main, [])
    return ScriptObservation(result.exit_code, recorded)


def observe_install(monkeypatch: pytest.MonkeyPatch) -> ScriptObservation:
    recorded: list[tuple[object, dict]] = []
    monkeypatch.setattr(install, "run", _make_run_recorder(recorded))
    runner = CliRunner()
    result = runner.invoke(install.main, [])
    return ScriptObservation(result.exit_code, recorded)


def observe_test(monkeypatch: pytest.MonkeyPatch) -> ScriptObservation:
    recorded: list[tuple[object, dict]] = []
    monkeypatch.setattr(test_script, "bootstrap_dev", lambda: None)
    monkeypatch.setattr(test_script, "sync_packaging", lambda: None)
    monkeypatch.setattr(test_script, "cmd_exists", lambda name: False)
    monkeypatch.setattr(test_script, "run", _make_run_recorder(recorded))
    runner = CliRunner()
    result = runner.invoke(test_script.main, [])
    return ScriptObservation(result.exit_code, recorded)


def test_project_metadata_reports_name() -> None:
    metadata = _utils.get_project_metadata()
    assert metadata.name == "lib_log_rich"


def test_project_metadata_reports_slug() -> None:
    metadata = _utils.get_project_metadata()
    assert metadata.slug == "lib-log-rich"


def test_project_metadata_reports_import_package() -> None:
    metadata = _utils.get_project_metadata()
    assert metadata.import_package == "lib_log_rich"


def test_project_metadata_reports_coverage_source() -> None:
    metadata = _utils.get_project_metadata()
    assert metadata.coverage_source == "src/lib_log_rich"


def test_project_metadata_tarball_url_points_to_github() -> None:
    metadata = _utils.get_project_metadata()
    assert metadata.github_tarball_url("1.2.3").endswith("/bitranox/lib_log_rich/archive/refs/tags/v1.2.3.tar.gz")


def test_build_script_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_build(monkeypatch)
    assert observation.exit_code == 0


def test_build_script_invokes_python_build(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_build(monkeypatch)
    commands = [" ".join(cmd) if isinstance(cmd, list) else str(cmd) for cmd, _ in observation.commands]
    assert any("python -m build" in cmd for cmd in commands)


def test_build_script_invokes_brew_formula(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_build(monkeypatch)
    commands = [" ".join(cmd) if isinstance(cmd, list) else str(cmd) for cmd, _ in observation.commands]
    assert any("brew install --build-from-source packaging/brew/Formula/lib-log-rich.rb" in cmd for cmd in commands)


def test_dev_script_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_dev(monkeypatch)
    assert observation.exit_code == 0


def test_dev_script_installs_dev_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_dev(monkeypatch)
    assert observation.commands[0][0] == [sys.executable, "-m", "pip", "install", "-e", ".[dev]"]


def test_install_script_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_install(monkeypatch)
    assert observation.exit_code == 0


def test_install_script_installs_editable_package(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_install(monkeypatch)
    assert observation.commands[0][0] == [sys.executable, "-m", "pip", "install", "-e", "."]


def test_test_script_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_test(monkeypatch)
    assert observation.exit_code == 0


def test_test_script_invokes_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_test(monkeypatch)
    pytest_commands = [cmd for cmd, _ in observation.commands if isinstance(cmd, list) and cmd[:3] == ["python", "-m", "pytest"]]
    assert pytest_commands != []


def test_test_script_sets_cov_target(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = observe_test(monkeypatch)
    pytest_commands = [" ".join(cmd) for cmd, _ in observation.commands if isinstance(cmd, list) and cmd[:3] == ["python", "-m", "pytest"]]
    assert any(f"--cov={test_script.COVERAGE_TARGET}" in cmd for cmd in pytest_commands)


def test_module_main_exit_code_is_zero(capsys: pytest.CaptureFixture[str]) -> None:
    from lib_log_rich.__main__ import main

    exit_code = main([])
    capsys.readouterr()
    assert exit_code == 0


def test_module_main_prints_metadata_banner(capsys: pytest.CaptureFixture[str]) -> None:
    from lib_log_rich.__main__ import main

    main([])
    captured = capsys.readouterr()
    assert "Info for lib_log_rich" in captured.out


def test_module_main_hello_exit_code_is_zero(capsys: pytest.CaptureFixture[str]) -> None:
    from lib_log_rich.__main__ import main

    exit_code = main(["--hello"])
    capsys.readouterr()
    assert exit_code == 0


def test_module_main_hello_prints_greeting(capsys: pytest.CaptureFixture[str]) -> None:
    from lib_log_rich.__main__ import main

    main(["--hello"])
    captured = capsys.readouterr()
    assert captured.out.startswith("Hello World\n")


def test_module_main_hello_prints_metadata(capsys: pytest.CaptureFixture[str]) -> None:
    from lib_log_rich.__main__ import main

    main(["--hello"])
    captured = capsys.readouterr()
    assert "Info for lib_log_rich" in captured.out


def test_module_main_version_exit_code_is_zero(capsys: pytest.CaptureFixture[str]) -> None:
    from lib_log_rich.__main__ import main

    exit_code = main(["--version"])
    capsys.readouterr()
    assert exit_code == 0


def test_module_main_version_prints_version_line(capsys: pytest.CaptureFixture[str]) -> None:
    from lib_log_rich.__main__ import main
    from lib_log_rich import __init__conf__

    main(["--version"])
    captured = capsys.readouterr()
    assert captured.out.strip() == f"{__init__conf__.shell_command} version {__init__conf__.version}"
