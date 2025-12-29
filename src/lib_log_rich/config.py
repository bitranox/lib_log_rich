r"""Opt-in configuration helpers for project-local ``.env`` files.

Purpose
-------
Give host applications a deliberate hook to load ``.env`` files before calling
:func:`lib_log_rich.init`, keeping the documented precedence order (CLI →
process environment → ``.env`` → defaults) intact while avoiding surprises for
consumers that do not expect implicit file loading.

Contents
--------
* :func:`enable_dotenv` / :func:`load_dotenv` – entry points used by the CLI and
  host applications.
* :func:`should_use_dotenv` / :func:`interpret_dotenv_toggle` – helpers that
  translate CLI/env toggles into booleans.
* Module-level constants (:data:`DOTENV_ENV_VAR`, :data:`DEFAULT_MARKERS`).

System Role
-----------
Lives in the configuration boundary described in ``concept_architecture.md``;
``init`` reads environment variables only after callers opt in through these
helpers.

Examples
--------
>>> import os, tempfile
>>> from pathlib import Path
>>> from lib_log_rich import config as log_config
>>> original = os.environ.get("LOG_SERVICE")
>>> try:
...     with tempfile.TemporaryDirectory() as tmpdir:
...         project = Path(tmpdir)
...         _ = (project / ".env").write_text("LOG_SERVICE=dotenv-example")
...         nested = project / "nested"
...         nested.mkdir()
...         _ = os.environ.pop("LOG_SERVICE", None)
...         log_config._reset_dotenv_state_for_testing()
...         _ = log_config.enable_dotenv(search_from=nested)
...         os.environ["LOG_SERVICE"]
... finally:
...     if original is not None:
...         os.environ["LOG_SERVICE"] = original
...     else:
...         _ = os.environ.pop("LOG_SERVICE", None)
...     log_config._reset_dotenv_state_for_testing()
'dotenv-example'
>>> log_config.should_use_dotenv(env_value="1")
True
>>> log_config.should_use_dotenv(explicit=False, env_value="1")
False

"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from dotenv import find_dotenv
from dotenv import load_dotenv as _load_dotenv

__all__ = [
    "enable_dotenv",
    "load_dotenv",
    "should_use_dotenv",
    "interpret_dotenv_toggle",
    "DOTENV_ENV_VAR",
    "DEFAULT_MARKERS",
    "_reset_dotenv_state_for_testing",
]

# Environment toggle mirroring the CLI flag for opting into .env loading.
DOTENV_ENV_VAR: str = "LOG_USE_DOTENV"
# Stop upward .env search when common project markers (pyproject/git) are encountered.
DEFAULT_MARKERS: tuple[str, ...] = ("pyproject.toml", ".git")
_TRUTHY_VALUES: set[str] = {"1", "true", "yes", "on"}
_FALSEY_VALUES: set[str] = {"0", "false", "no", "off"}


@dataclass(slots=True)
class _DotenvState:
    """Record cached loading results to keep enablement idempotent.

    Attributes:
        loaded: Indicates whether :func:`enable_dotenv` ran at least once.
        override: Stores the override flag used for the last load so repeated
            calls with the same semantics can be skipped.
        path: Resolved path of the loaded ``.env`` file (if any).

    """

    loaded: bool = False
    override: bool = False
    path: Path | None = None


_STATE_LOCK = Lock()
_dotenv_state = _DotenvState()


def interpret_dotenv_toggle(value: str | None) -> bool | None:
    """Return ``True``/``False``/``None`` for environment toggle meanings.

    Mirrors the behaviour described in ``DOTENV.md`` so operators can rely on
    boolean-like environment variables (`1`, `true`, etc.).

    Example:
        >>> interpret_dotenv_toggle('YES')
        True
        >>> interpret_dotenv_toggle('no')
        False
        >>> interpret_dotenv_toggle('maybe') is None
        True

    """
    if value is None:
        return None
    candidate = value.strip().lower()
    if not candidate:
        return None
    if candidate in _TRUTHY_VALUES:
        return True
    if candidate in _FALSEY_VALUES:
        return False
    return None


def should_use_dotenv(*, explicit: bool | None = None, env_value: str | None = None) -> bool:
    """Determine whether ``.env`` loading is enabled.

    Args:
        explicit: CLI flag or config switch that takes precedence.
        env_value: Raw environment string (e.g., ``"1"``) to interpret when
            ``explicit`` is not provided.

    Returns:
        ``True`` when a ``.env`` file should be loaded.

    Example:
        >>> should_use_dotenv(explicit=True, env_value='0')
        True
        >>> should_use_dotenv(explicit=None, env_value='off')
        False

    """
    if explicit is not None:
        return explicit
    return interpret_dotenv_toggle(env_value) is True


def _normalise_search_root(search_from: Path | str | None) -> Path:
    """Return an absolute directory used as the search starting point.

    Ensures ``enable_dotenv`` behaves predictably regardless of whether callers
    provide files, directories, or ``None``.

    Example:
        >>> tmp = Path('.')
        >>> _normalise_search_root(tmp).exists() in (True, False)
        True

    """
    base = Path(search_from) if search_from is not None else Path.cwd()
    base = base.expanduser()
    if base.is_file():
        base = base.parent
    if not base.exists():
        raise FileNotFoundError(f"Cannot enable .env loading from missing directory: {base}")
    return base.resolve()


@contextmanager
def _temporary_working_directory(path: Path) -> Iterator[None]:
    """Temporarily change working directory while searching for ``.env``.

    Note:
        Changes :func:`Path.cwd` for the duration of the context.

    Example:
        >>> cwd = Path.cwd()
        >>> with _temporary_working_directory(cwd):
        ...     Path.cwd() == cwd
        True
        >>> Path.cwd() == cwd
        True

    """
    original = Path.cwd()
    os.chdir(os.fspath(path))
    try:
        yield
    finally:
        os.chdir(os.fspath(original))


def _collect_allowed_directories(start: Path, markers: Sequence[str]) -> tuple[Path, ...]:
    """Collect parents to inspect, stopping at filesystem root or marker match.

    Mirrors Poetry/pipenv search semantics where configuration markers stop the
    upward traversal.

    Example:
        >>> directories = _collect_allowed_directories(Path.cwd(), ('pyproject.toml',))
        >>> isinstance(directories, tuple)
        True

    """
    allowed: list[Path] = []
    marker_set = {marker for marker in markers if marker}
    current = start
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        allowed.append(current)
        if marker_set and any((current / marker).exists() for marker in marker_set):
            break
        if current.parent == current:
            break
        current = current.parent
    return tuple(path.resolve() for path in allowed)


def _find_dotenv_path(start: Path, markers: Sequence[str]) -> Path | None:
    r"""Locate the first ``.env`` file walking towards the filesystem root.

    Example:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmpdir:
        ...     base = Path(tmpdir)
        ...     _ = (base / '.env').write_text('FOO=1\n')
        ...     _find_dotenv_path(base, markers=()).resolve() == (base / '.env').resolve()
        True

    """
    allowed_dirs = _collect_allowed_directories(start, markers)
    if not allowed_dirs:
        return None
    with _temporary_working_directory(allowed_dirs[0]):
        candidate_str = find_dotenv(usecwd=True)
    if not candidate_str:
        return None
    candidate = Path(candidate_str).resolve()
    if not candidate.is_file():
        return None
    if not any(candidate.parent == directory for directory in allowed_dirs):
        return None
    return candidate


def enable_dotenv(
    search_from: Path | str | None = None,
    *,
    markers: Sequence[str] = DEFAULT_MARKERS,
    dotenv_override: bool = False,
) -> Path | None:
    """Load the nearest ``.env`` file and cache the result.

    Provides a single entry point for opt-in ``.env`` loading while caching
    results so repeated calls remain cheap.

    Args:
        search_from: Directory to start the upward search from. ``None`` uses
            :func:`Path.cwd`.
        markers: Optional filenames that terminate the search once encountered.
        dotenv_override: When ``True`` the ``.env`` values replace existing
            environment entries.

    Returns:
        Absolute path to the loaded ``.env`` or ``None`` when not found.

    """
    global _dotenv_state

    start = _normalise_search_root(search_from)
    with _STATE_LOCK:
        if _dotenv_state.loaded and _dotenv_state.override == dotenv_override:
            return _dotenv_state.path

    candidate = _find_dotenv_path(start, markers)

    with _STATE_LOCK:
        if _dotenv_state.loaded and _dotenv_state.override == dotenv_override:
            return _dotenv_state.path
        if candidate is None:
            _dotenv_state = _DotenvState(loaded=True, override=dotenv_override, path=None)
            return None
        _load_dotenv(dotenv_path=os.fspath(candidate), override=dotenv_override)
        _dotenv_state = _DotenvState(loaded=True, override=dotenv_override, path=candidate)
        return candidate


def load_dotenv(
    search_from: Path | str | None = None,
    *,
    markers: Sequence[str] = DEFAULT_MARKERS,
    override: bool = False,
) -> Path | None:
    """Alias that mirrors :func:`dotenv.load_dotenv` semantics."""
    return enable_dotenv(search_from=search_from, markers=markers, dotenv_override=override)


def _reset_dotenv_state_for_testing() -> None:
    """Reset cached state – intended for unit tests and doctests only."""
    global _dotenv_state
    with _STATE_LOCK:
        _dotenv_state = _DotenvState()
