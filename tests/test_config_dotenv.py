from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import pytest
from click.testing import CliRunner

from lib_log_rich import cli as cli_module
from lib_log_rich import config as log_config
from tests.os_markers import OS_AGNOSTIC

ResetCallable = Callable[[], None]


def _reset_helper() -> ResetCallable:
    func = getattr(log_config, "_reset_dotenv_state_for_testing", None)
    if func is None:
        raise AttributeError("_reset_dotenv_state_for_testing missing")
    return func


pytestmark = [OS_AGNOSTIC]


@dataclass
class DotenvObservation:
    """Details captured after invoking ``enable_dotenv``."""

    loaded_path: Path | None
    service_value: str | None


@dataclass
class CliDotenvObservation:
    """Observation of CLI dotenv toggling."""

    exit_code: int
    enable_calls: int


@pytest.fixture(name="_reset_dotenv_state", autouse=True)
def reset_dotenv_state_fixture() -> Iterator[None]:
    """Reset shared dotenv state around each test."""

    reset = _reset_helper()
    reset()
    try:
        yield
    finally:
        reset()


def observe_enable_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, existing: str | None = None) -> DotenvObservation:
    """Invoke ``enable_dotenv`` in a temporary tree and capture results."""

    nested = tmp_path / "nested"
    nested.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_SERVICE=dotenv-service\n")
    monkeypatch.chdir(nested)

    if existing is None:
        monkeypatch.delenv("LOG_SERVICE", raising=False)
    else:
        monkeypatch.setenv("LOG_SERVICE", existing)

    loaded = log_config.enable_dotenv()
    service_value = os.environ.get("LOG_SERVICE")
    os.environ.pop("LOG_SERVICE", None)
    return DotenvObservation(loaded, service_value)


def observe_cli_dotenv(monkeypatch: pytest.MonkeyPatch, *, args: list[str], env: dict[str, str] | None = None) -> CliDotenvObservation:
    """Run the CLI with dotenv toggles and capture exit code and call count."""

    runner = CliRunner()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def record_enable(*call_args: object, **call_kwargs: object) -> None:
        calls.append((call_args, call_kwargs))

    monkeypatch.setattr(log_config, "enable_dotenv", record_enable)
    monkeypatch.delenv(log_config.DOTENV_ENV_VAR, raising=False)

    result = runner.invoke(cli_module.cli, args, env=env)
    return CliDotenvObservation(result.exit_code, len(calls))


def test_enable_dotenv_returns_loaded_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading the nearest .env returns the resolved path."""

    observation = observe_enable_dotenv(tmp_path, monkeypatch)
    assert observation.loaded_path == (tmp_path / ".env").resolve()


def test_enable_dotenv_populates_service_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading the nearest .env populates ``LOG_SERVICE`` from file."""

    observation = observe_enable_dotenv(tmp_path, monkeypatch)
    assert observation.service_value == "dotenv-service"


def test_enable_dotenv_preserves_existing_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing environment variables win over .env entries."""

    observation = observe_enable_dotenv(tmp_path, monkeypatch, existing="real-service")
    assert observation.service_value == "real-service"


def test_enable_dotenv_returns_non_null_when_existing_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The function still reports a loaded file even with existing values."""

    observation = observe_enable_dotenv(tmp_path, monkeypatch, existing="real-service")
    assert observation.loaded_path is not None


def test_cli_flag_use_dotenv_triggers_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``--use-dotenv`` flag invokes the loader."""

    observation = observe_cli_dotenv(monkeypatch, args=["--use-dotenv", "info"])
    assert observation.enable_calls == 1


def test_cli_flag_use_dotenv_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``--use-dotenv`` flag still exits cleanly."""

    observation = observe_cli_dotenv(monkeypatch, args=["--use-dotenv", "info"])
    assert observation.exit_code == 0


def test_cli_env_toggle_invokes_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting the environment toggle enables dotenv by default."""

    observation = observe_cli_dotenv(
        monkeypatch,
        args=["info"],
        env={log_config.DOTENV_ENV_VAR: "1"},
    )
    assert observation.enable_calls == 1


def test_cli_env_toggle_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment toggle still allows command success."""

    observation = observe_cli_dotenv(
        monkeypatch,
        args=["info"],
        env={log_config.DOTENV_ENV_VAR: "1"},
    )
    assert observation.exit_code == 0


def test_cli_no_use_dotenv_skips_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--no-use-dotenv`` overrides the environment toggle."""

    observation = observe_cli_dotenv(
        monkeypatch,
        args=["--no-use-dotenv", "info"],
        env={log_config.DOTENV_ENV_VAR: "1"},
    )
    assert observation.enable_calls == 0


def test_cli_no_use_dotenv_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-out flag exits without error."""

    observation = observe_cli_dotenv(
        monkeypatch,
        args=["--no-use-dotenv", "info"],
        env={log_config.DOTENV_ENV_VAR: "1"},
    )
    assert observation.exit_code == 0
