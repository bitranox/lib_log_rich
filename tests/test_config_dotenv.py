from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from lib_log_rich import cli as cli_module
from lib_log_rich import config as log_config


@pytest.fixture(autouse=True)
def _reset_dotenv_state() -> None:
    """Reset shared dotenv state around each test."""

    log_config._reset_dotenv_state_for_testing()
    yield
    log_config._reset_dotenv_state_for_testing()


def test_enable_dotenv_populates_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading the nearest .env injects values without overriding call arguments."""

    nested = tmp_path / "nested"
    nested.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_SERVICE=dotenv-service\n")
    monkeypatch.chdir(nested)
    monkeypatch.delenv("LOG_SERVICE", raising=False)

    loaded = log_config.enable_dotenv()

    assert loaded == env_file.resolve()
    assert os.environ["LOG_SERVICE"] == "dotenv-service"

    os.environ.pop("LOG_SERVICE", None)


def test_enable_dotenv_respects_existing_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing environment variables keep precedence over .env entries."""

    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / ".env").write_text("LOG_SERVICE=dotenv-service\n")
    monkeypatch.chdir(nested)
    monkeypatch.setenv("LOG_SERVICE", "real-service")

    result = log_config.enable_dotenv()

    assert result is not None
    assert os.environ["LOG_SERVICE"] == "real-service"

    os.environ.pop("LOG_SERVICE", None)


def test_cli_dotenv_toggle_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI flag wins over environment toggle when deciding whether to load .env."""

    runner = CliRunner()

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def record_enable(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(log_config, "enable_dotenv", record_enable)
    monkeypatch.delenv(log_config.DOTENV_ENV_VAR, raising=False)

    result = runner.invoke(cli_module.cli, ["--use-dotenv", "info"])
    assert result.exit_code == 0
    assert len(calls) == 1

    calls.clear()
    env = {log_config.DOTENV_ENV_VAR: "1"}
    result = runner.invoke(cli_module.cli, ["info"], env=env)
    assert result.exit_code == 0
    assert len(calls) == 1

    calls.clear()
    env = {log_config.DOTENV_ENV_VAR: "1"}
    result = runner.invoke(cli_module.cli, ["--no-use-dotenv", "info"], env=env)
    assert result.exit_code == 0
    assert calls == []
