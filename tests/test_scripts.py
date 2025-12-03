from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

import pytest
from click.testing import CliRunner

from scripts import build as build_module
from scripts import bump as bump_module
from scripts import clean as clean_module
from scripts import cli
from scripts import dev as dev_module
from scripts import install as install_module
from scripts import push as push_module
from scripts import release as release_module
from scripts import run_cli as run_cli_module
from scripts import test as test_module
from scripts import version_current as version_module
from scripts._utils import get_project_metadata
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMMIT_MESSAGE", raising=False)


def test_get_project_metadata_fields() -> None:
    metadata = get_project_metadata()
    assert metadata.name == "lib_log_rich"
    assert metadata.slug == "lib-log-rich"
    assert metadata.import_package == "lib_log_rich"
    assert metadata.coverage_source == "src/lib_log_rich"
    assert metadata.github_tarball_url("1.2.3").endswith("/bitranox/lib_log_rich/archive/refs/tags/v1.2.3.tar.gz")


def _record_call(monkeypatch: pytest.MonkeyPatch, module: Any, attribute: str, return_value: Any = None) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        calls["args"] = args
        calls["kwargs"] = kwargs
        return return_value

    monkeypatch.setattr(module, attribute, _wrapper)
    return calls


def test_install_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls = _record_call(monkeypatch, install_module, "install")
    result = runner.invoke(cli.main, ["install", "--dry-run"], catch_exceptions=False)
    assert result.exit_code == 0
    assert calls["kwargs"] == {"dry_run": True}


def test_dev_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls = _record_call(monkeypatch, dev_module, "install_dev")
    result = runner.invoke(cli.main, ["dev", "--dry-run"], catch_exceptions=False)
    assert result.exit_code == 0
    assert calls["kwargs"] == {"dry_run": True}


def test_clean_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    patterns: list[str] = []

    def _capture(values: Iterable[str]) -> None:
        patterns.extend(values)

    monkeypatch.setattr(clean_module, "clean", _capture)
    result = runner.invoke(cli.main, ["clean", "--pattern", "foo", "--pattern", "bar"], catch_exceptions=False)
    assert result.exit_code == 0
    assert patterns[: len(clean_module.DEFAULT_PATTERNS)] == list(clean_module.DEFAULT_PATTERNS)
    assert patterns[-2:] == ["foo", "bar"]


def test_run_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls = _record_call(monkeypatch, run_cli_module, "run_cli", return_value=0)
    result = runner.invoke(cli.main, ["run", "--", "hello"], catch_exceptions=False)
    assert result.exit_code == 0
    assert calls["args"] == (("hello",),)
    assert calls["kwargs"] == {}


def test_test_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls = _record_call(monkeypatch, test_module, "run_tests")
    result = runner.invoke(
        cli.main,
        [
            "test",
            "--coverage",
            "auto",
            "--verbose",
            "--strict-format",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert calls["kwargs"] == {
        "coverage": "auto",
        "verbose": True,
        "strict_format": True,
    }


def test_build_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls = _record_call(monkeypatch, build_module, "build_artifacts")
    result = runner.invoke(
        cli.main,
        ["build"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert calls["kwargs"] == {}


def test_release_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls = _record_call(monkeypatch, release_module, "release")
    result = runner.invoke(
        cli.main,
        ["release", "--remote", "origin"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert calls["kwargs"] == {"remote": "origin"}


def test_push_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls = _record_call(monkeypatch, push_module, "push")
    result = runner.invoke(
        cli.main,
        ["push", "--remote", "upstream", "--message", "feat: deploy"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert calls["kwargs"] == {"remote": "upstream", "message": "feat: deploy"}


def test_version_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """[project]
version = "1.2.3"
""",
        encoding="utf-8",
    )

    def fake_print_current_version(pyproject_path: Path) -> str:
        return "1.2.3"

    monkeypatch.setattr(version_module, "print_current_version", fake_print_current_version)
    result = runner.invoke(cli.main, ["version-current", "--pyproject", str(pyproject)], catch_exceptions=False)
    assert result.exit_code == 0
    assert "1.2.3" in result.output


def test_bump_command(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls = _record_call(monkeypatch, bump_module, "bump")
    result = runner.invoke(
        cli.main,
        ["bump", "--version", "2.0.0", "--pyproject", "pyproject.toml", "--changelog", "CHANGELOG.md"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    assert calls["kwargs"] == {
        "version": "2.0.0",
        "part": "patch",
        "pyproject": Path("pyproject.toml"),
        "changelog": Path("CHANGELOG.md"),
    }


def test_bump_shortcuts(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    calls: dict[str, int] = {"major": 0, "minor": 0, "patch": 0}

    def _stub(name: str) -> Callable[[], None]:
        def _inner() -> None:
            calls[name] += 1

        return _inner

    monkeypatch.setattr("scripts.cli.bump_major", _stub("major"))
    monkeypatch.setattr("scripts.cli.bump_minor", _stub("minor"))
    monkeypatch.setattr("scripts.cli.bump_patch", _stub("patch"))

    for cmd in ("bump-major", "bump-minor", "bump-patch"):
        result = runner.invoke(cli.main, [cmd], catch_exceptions=False)
        assert result.exit_code == 0

    assert calls == {"major": 1, "minor": 1, "patch": 1}


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
