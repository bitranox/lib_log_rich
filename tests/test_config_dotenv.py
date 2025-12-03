from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, cast

import pytest
from click.testing import CliRunner

from lib_log_rich import cli as cli_module
from lib_log_rich import config as log_config
from tests.os_markers import OS_AGNOSTIC

CONFIG = cast(Any, log_config)

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

    loaded = CONFIG.enable_dotenv()
    service_value = os.environ.get("LOG_SERVICE")
    os.environ.pop("LOG_SERVICE", None)
    return DotenvObservation(loaded, service_value)


def observe_cli_dotenv(monkeypatch: pytest.MonkeyPatch, *, args: list[str], env: dict[str, str] | None = None) -> CliDotenvObservation:
    """Run the CLI with dotenv toggles and capture exit code and call count."""
    runner = CliRunner()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def record_enable(*call_args: object, **call_kwargs: object) -> None:
        calls.append((call_args, call_kwargs))

    monkeypatch.setattr(CONFIG, "enable_dotenv", record_enable)
    monkeypatch.delenv(CONFIG.DOTENV_ENV_VAR, raising=False)

    result = runner.invoke(cli_module.cli, args, env=env)
    return CliDotenvObservation(result.exit_code, len(calls))


def test_enable_dotenv_returns_loaded_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading the nearest .env returns the resolved path."""
    observation = observe_enable_dotenv(tmp_path, monkeypatch)
    assert observation.loaded_path == (tmp_path / ".env").resolve()


def test_interpret_dotenv_toggle_handles_blank_values() -> None:
    assert log_config.interpret_dotenv_toggle("   ") is None
    assert log_config.interpret_dotenv_toggle(None) is None


def test_normalise_search_root_converts_file_to_parent(tmp_path: Path) -> None:
    candidate = tmp_path / "example" / "config.env"
    candidate.parent.mkdir()
    candidate.write_text("LOG_SERVICE=svc\n")
    result = cast(Any, log_config)._normalise_search_root(candidate)
    assert result == candidate.parent.resolve()


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
        env={CONFIG.DOTENV_ENV_VAR: "1"},
    )
    assert observation.enable_calls == 1


def test_cli_env_toggle_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment toggle still allows command success."""
    observation = observe_cli_dotenv(
        monkeypatch,
        args=["info"],
        env={CONFIG.DOTENV_ENV_VAR: "1"},
    )
    assert observation.exit_code == 0


def test_cli_no_use_dotenv_skips_loader(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--no-use-dotenv`` overrides the environment toggle."""
    observation = observe_cli_dotenv(
        monkeypatch,
        args=["--no-use-dotenv", "info"],
        env={CONFIG.DOTENV_ENV_VAR: "1"},
    )
    assert observation.enable_calls == 0


def test_cli_no_use_dotenv_exits_successfully(monkeypatch: pytest.MonkeyPatch) -> None:
    """The opt-out flag exits without error."""
    observation = observe_cli_dotenv(
        monkeypatch,
        args=["--no-use-dotenv", "info"],
        env={CONFIG.DOTENV_ENV_VAR: "1"},
    )
    assert observation.exit_code == 0


def test_normalise_search_root_requires_existing(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        CONFIG._normalise_search_root(missing)


def test_collect_allowed_directories_stops_at_marker(tmp_path: Path) -> None:
    root = tmp_path
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    chain = CONFIG._collect_allowed_directories(nested, markers=("pyproject.toml",))
    assert chain[0] == nested.resolve()
    assert root.resolve() in chain
    assert (root / "a").resolve() in chain
    # traversal stops once marker directory encountered
    assert len(chain) == 3


def test_find_dotenv_returns_none_when_no_directories(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def empty_collect(start: Path, markers: tuple[str, ...]) -> tuple[Path, ...]:
        return ()

    monkeypatch.setattr(CONFIG, "_collect_allowed_directories", empty_collect)
    assert CONFIG._find_dotenv_path(tmp_path, markers=()) is None


def test_find_dotenv_returns_none_when_no_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def no_candidate(*, usecwd: bool) -> str:
        return ""

    monkeypatch.setattr(CONFIG, "find_dotenv", no_candidate)
    assert CONFIG._find_dotenv_path(tmp_path, markers=()) is None


def test_find_dotenv_rejects_non_file_candidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def non_file(*, usecwd: bool) -> str:
        return os.fspath(tmp_path)

    monkeypatch.setattr(CONFIG, "find_dotenv", non_file)
    assert CONFIG._find_dotenv_path(tmp_path, markers=()) is None


def test_find_dotenv_rejects_candidate_outside_allowed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    env_file = env_dir / ".env"
    env_file.write_text("FOO=1\n")
    other_dir = tmp_path / "other"
    other_dir.mkdir()

    def fake_collect(start: Path, markers: tuple[str, ...]) -> tuple[Path, ...]:
        return (other_dir.resolve(),)

    monkeypatch.setattr(CONFIG, "_collect_allowed_directories", fake_collect)

    def fake_find_dotenv(*, usecwd: bool) -> str:
        return os.fspath(env_file)

    monkeypatch.setattr(CONFIG, "find_dotenv", fake_find_dotenv)
    assert CONFIG._find_dotenv_path(env_dir, markers=()) is None


def test_enable_dotenv_uses_cached_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_SERVICE=svc\n")
    first = CONFIG.enable_dotenv(search_from=tmp_path)
    assert first == env_file.resolve()

    def fail(*_args: object, **_kwargs: object) -> None:  # pragma: no cover - should not run
        raise AssertionError("finder should not be called")

    monkeypatch.setattr(CONFIG, "_find_dotenv_path", fail)
    second = CONFIG.enable_dotenv(search_from=tmp_path)
    assert second == first


def test_enable_dotenv_cached_after_candidate_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_SERVICE=svc\n")

    def fake_find(start: Path, markers: tuple[str, ...]) -> Path:
        with CONFIG._STATE_LOCK:
            CONFIG._dotenv_state = CONFIG._DotenvState(loaded=True, override=False, path=env_file.resolve())
        return env_file.resolve()

    monkeypatch.setattr(CONFIG, "_find_dotenv_path", fake_find)
    result = CONFIG.enable_dotenv(search_from=tmp_path)
    assert result == env_file.resolve()


def test_enable_dotenv_records_missing_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def none_path(start: Path, markers: tuple[str, ...]) -> None:
        return None

    monkeypatch.setattr(CONFIG, "_find_dotenv_path", none_path)
    outcome = CONFIG.enable_dotenv(search_from=tmp_path)
    assert outcome is None

    def fail(*_args: object, **_kwargs: object) -> None:  # pragma: no cover
        raise AssertionError("finder should not be called")

    monkeypatch.setattr(CONFIG, "_find_dotenv_path", fail)
    assert CONFIG.enable_dotenv(search_from=tmp_path) is None


def test_enable_dotenv_reloads_when_override_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_SERVICE=svc\n")
    first = CONFIG.enable_dotenv(search_from=tmp_path)
    assert first == env_file.resolve()

    calls: list[bool] = []

    def record_load(*, dotenv_path: str, override: bool) -> None:
        calls.append(override)

    monkeypatch.setattr(CONFIG, "_load_dotenv", record_load)

    def return_env(start: Path, markers: tuple[str, ...]) -> Path:
        return env_file.resolve()

    monkeypatch.setattr(CONFIG, "_find_dotenv_path", return_env)
    second = CONFIG.enable_dotenv(search_from=tmp_path, dotenv_override=True)
    assert second == env_file.resolve()
    assert calls == [True]


def test_load_dotenv_delegates_to_enable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_SERVICE=svc\n")
    result = CONFIG.load_dotenv(search_from=tmp_path)
    assert result == env_file.resolve()
