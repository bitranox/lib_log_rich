from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from click.testing import CliRunner

from lib_log_rich import cli as cli_module
from lib_log_rich import config as log_config
from tests.os_markers import OS_AGNOSTIC, POSIX_ONLY

CONFIG = cast("Any", log_config)

# root bypasses directory permissions, so a mode-000 directory is no wall for it.
_RUNS_AS_ROOT = sys.platform != "win32" and os.geteuid() == 0
SKIP_AS_ROOT = pytest.mark.skipif(_RUNS_AS_ROOT, reason="root sees through directory permissions")

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
    result = cast("Any", log_config)._normalise_search_root(candidate)
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


def _project_root(tmp_path: Path) -> Path:
    """Return a directory holding a ``pyproject.toml`` marker, so no search climbs above it."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text("")
    return project


def test_find_dotenv_returns_none_when_no_candidate(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    assert CONFIG._find_dotenv_path(project, markers=("pyproject.toml",)) is None


def test_find_dotenv_rejects_non_file_candidate(tmp_path: Path) -> None:
    project = _project_root(tmp_path)
    (project / ".env").mkdir()
    assert CONFIG._find_dotenv_path(project, markers=("pyproject.toml",)) is None


def test_find_dotenv_rejects_candidate_outside_allowed(tmp_path: Path) -> None:
    """A ``.env`` above the marker directory is never picked up."""
    (tmp_path / ".env").write_text("FOO=1\n")
    project = _project_root(tmp_path)
    assert CONFIG._find_dotenv_path(project, markers=("pyproject.toml",)) is None


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
    """A second call with ``dotenv_override=True`` loads again and replaces the existing value."""
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_SERVICE=svc\n")
    monkeypatch.setenv("LOG_SERVICE", "real-service")
    first = CONFIG.enable_dotenv(search_from=tmp_path)
    assert first == env_file.resolve()
    assert os.environ["LOG_SERVICE"] == "real-service"

    second = CONFIG.enable_dotenv(search_from=tmp_path, dotenv_override=True)
    assert second == env_file.resolve()
    assert os.environ["LOG_SERVICE"] == "svc"


def test_load_dotenv_delegates_to_enable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LOG_SERVICE=svc\n")
    result = CONFIG.load_dotenv(search_from=tmp_path)
    assert result == env_file.resolve()


@pytest.fixture(name="unenterable_cwd")
def unenterable_cwd_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Make the cwd a directory the process cannot look into, like ``/root`` for a service user.

    A ``.env`` and a ``pyproject.toml`` sit one level up, so a search that walks past the
    unreadable directory would find both.
    """
    (tmp_path / ".env").write_text("LOG_SERVICE=from-parent\n")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    monkeypatch.chdir(blocked)
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    blocked.chmod(0o000)
    try:
        yield blocked
    finally:
        blocked.chmod(0o755)
        os.environ.pop("LOG_SERVICE", None)


@POSIX_ONLY
@SKIP_AS_ROOT
@pytest.mark.usefixtures("unenterable_cwd")
def test_enable_dotenv_from_unenterable_cwd_finds_nothing() -> None:
    """An uninspectable cwd ends the search instead of crashing or walking past it."""
    assert CONFIG.enable_dotenv(markers=()) is None
    assert os.environ.get("LOG_SERVICE") is None


@POSIX_ONLY
@SKIP_AS_ROOT
@pytest.mark.usefixtures("unenterable_cwd")
def test_enable_dotenv_with_search_root_ignores_unenterable_cwd(tmp_path: Path) -> None:
    """An explicit ``search_from`` works even when the process cwd cannot be entered."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("LOG_SERVICE=from-project\n")
    assert CONFIG.enable_dotenv(search_from=project) == (project / ".env").resolve()
    assert os.environ.get("LOG_SERVICE") == "from-project"


def _write_undecodable_env(directory: Path) -> Path:
    env_file = directory / ".env"
    # "TOKEN=hunter2\n" is 14 bytes and "LOG_SERVICE=caf" 15 more, so the bad byte sits at offset 29.
    env_file.write_bytes(b"TOKEN=hunter2\nLOG_SERVICE=caf\xe9\n")
    return env_file


def test_enable_dotenv_refuses_undecodable_env_naming_file_and_offset(tmp_path: Path) -> None:
    """A ``.env`` that is not UTF-8 is refused with a message naming the file and the byte offset."""
    env_file = _write_undecodable_env(tmp_path)
    with pytest.raises(CONFIG.DotenvDecodeError) as info:
        CONFIG.enable_dotenv(search_from=tmp_path)
    message = str(info.value)
    assert str(env_file.resolve()) in message
    assert "byte offset 29" in message


def test_enable_dotenv_refusal_never_quotes_env_content(tmp_path: Path) -> None:
    """The refusal leaves the file's values out, both in its message and in a chained cause."""
    _write_undecodable_env(tmp_path)
    with pytest.raises(ValueError, match=r"\.env") as info:
        CONFIG.enable_dotenv(search_from=tmp_path)
    assert "hunter2" not in str(info.value)
    assert info.value.__suppress_context__ is True


def test_enable_dotenv_refusal_loads_no_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused ``.env`` sets none of its entries, not even the ones before the bad byte."""
    monkeypatch.delenv("TOKEN", raising=False)
    _write_undecodable_env(tmp_path)
    with pytest.raises(ValueError, match=r"\.env"):
        CONFIG.enable_dotenv(search_from=tmp_path)
    assert os.environ.get("TOKEN") is None


def test_enable_dotenv_finds_nothing_when_the_os_refuses_to_name_the_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS refuses ``getcwd`` for a cwd it cannot read; that ends the search like any hidden directory."""

    def refuse(cls: type[Path]) -> Path:
        raise PermissionError(13, "Permission denied")

    # A seam that misses finds this file rather than whatever .env sits above the test run.
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / ".env").write_text("LOG_SERVICE=must-not-load\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    # Path.cwd is the OS edge: Linux answers for an unreadable cwd, so only this reproduces macOS here.
    # os.getcwd would not do: Python 3.10's Path.cwd reaches it through an accessor bound at import.
    monkeypatch.setattr(Path, "cwd", classmethod(refuse))
    assert CONFIG.enable_dotenv() is None


@POSIX_ONLY
def test_enable_dotenv_finds_nothing_when_the_cwd_was_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A cwd removed after the chdir cannot be named either; the search finds nothing instead of raising.

    POSIX only: Windows refuses to remove a directory that is some process's cwd.
    """
    # A search that still started here would find this file rather than whatever sits above the run.
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / ".env").write_text("LOG_SERVICE=must-not-load\n")
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    monkeypatch.delenv("LOG_SERVICE", raising=False)
    assert CONFIG.enable_dotenv() is None
    assert os.environ.get("LOG_SERVICE") is None
