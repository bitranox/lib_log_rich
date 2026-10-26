r"""Opt-in configuration helpers for project-local ``.env`` files.

Purpose
-------
Give host applications a deliberate hook to load ``.env`` files before calling
:func:`lib_log_rich.init`, keeping the documented precedence order (CLI →
process environment → ``.env`` → defaults) intact while avoiding surprises for
consumers that do not expect implicit file loading.

Contents
--------
* :func:`enable_dotenv` - entry point used by the CLI and host applications.
* :func:`should_use_dotenv` / :func:`interpret_dotenv_toggle` - helpers that
  translate CLI/env toggles into booleans.
* :class:`DotenvDecodeError` - raised when the discovered ``.env`` is not UTF-8.
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

import io
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from dotenv import load_dotenv as _load_dotenv

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "DEFAULT_MARKERS",
    "DOTENV_ENV_VAR",
    "DotenvDecodeError",
    "_reset_dotenv_state_for_testing",
    "enable_dotenv",
    "interpret_dotenv_toggle",
    "should_use_dotenv",
]

# Environment toggle mirroring the CLI flag for opting into .env loading.
DOTENV_ENV_VAR: str = "LOG_USE_DOTENV"
# Stop upward .env search when common project markers (pyproject/git) are encountered.
DEFAULT_MARKERS: tuple[str, ...] = ("pyproject.toml", ".git")
_TRUTHY_VALUES: set[str] = {"1", "true", "yes", "on"}
_FALSEY_VALUES: set[str] = {"0", "false", "no", "off"}
_DOTENV_FILENAME = ".env"
_DOTENV_ENCODING = "utf-8"


class DotenvDecodeError(ValueError):
    """The discovered ``.env`` file is not valid UTF-8.

    The message names the file and the byte offset of the first undecodable byte, never the
    file's content: a ``.env`` usually holds secrets, so neither the message nor a chained
    :class:`UnicodeDecodeError` (which carries the raw bytes) may reach a log or a traceback.

    Attributes:
        path: Absolute path of the refused file.
        offset: Zero-based byte offset of the first undecodable byte.

    Example:
        >>> error = DotenvDecodeError(Path("app") / ".env", 7)
        >>> str(error) == f"Cannot load {Path('app') / '.env'}: not valid UTF-8 at byte offset 7"
        True
        >>> isinstance(error, ValueError)
        True

    """

    def __init__(self, path: Path, offset: int) -> None:
        self.path = path
        self.offset = offset
        super().__init__(f"Cannot load {path}: not valid {_DOTENV_ENCODING.upper()} at byte offset {offset}")


class _Probe(Enum):
    """What a ``stat`` of one search candidate could establish."""

    FILE = "file"
    OTHER = "other"
    MISSING = "missing"
    # The parent directory denied the lookup, so the entry may or may not exist.
    HIDDEN = "hidden"


def _probe(path: Path) -> _Probe:
    """Classify ``path`` without letting a denied lookup pass for an absent one.

    ``Path.exists()`` and ``Path.is_file()`` are no use here: before Python 3.14 they raise
    ``PermissionError`` for an entry in an unenterable directory, and from 3.14 on they return
    ``False``, which reads as "missing" and would let the search walk past a project marker it
    could not see. ``stat`` raises on every version, so the denial is visible.

    Example:
        >>> _probe(Path(__file__)) is _Probe.FILE
        True
        >>> _probe(Path(__file__).parent) is _Probe.OTHER
        True
        >>> _probe(Path(__file__).parent / "no-such-entry") is _Probe.MISSING
        True

    """
    try:
        mode = path.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return _Probe.MISSING
    except OSError:
        return _Probe.HIDDEN
    return _Probe.FILE if stat.S_ISREG(mode) else _Probe.OTHER


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


def _current_directory() -> Path | None:
    """Return the cwd, or ``None`` when the OS cannot name it.

    macOS refuses ``getcwd`` with ``EACCES`` for a cwd the process cannot read (a service account
    whose cwd is ``/root``), where Linux still answers, and every POSIX system raises ``ENOENT``
    once the cwd was removed after the chdir. Either way there is no directory to start a search
    from, which is not the caller naming a missing ``search_from``, so it is no error.

    Example:
        >>> _current_directory() == Path.cwd()
        True

    """
    try:
        return Path.cwd()
    except OSError:
        return None


def _normalise_search_root(search_from: Path | str | None) -> Path | None:
    """Return an absolute directory used as the search starting point.

    Ensures ``enable_dotenv`` behaves predictably regardless of whether callers
    provide files, directories, or ``None``.

    Returns:
        The directory to search from, or ``None`` when ``search_from`` is ``None`` and the OS
        refuses to name the cwd.

    Example:
        >>> _normalise_search_root(Path('.')) == Path('.').resolve()
        True

    """
    if search_from is None:
        current = _current_directory()
        if current is None:
            return None
        base = current
    else:
        base = Path(search_from)
    base = base.expanduser()
    probe = _probe(base)
    if probe is _Probe.MISSING:
        raise FileNotFoundError(f"Cannot enable .env loading from missing directory: {base}")
    if probe is _Probe.FILE:
        base = base.parent
    # A HIDDEN root is kept as it is: the search stops there and finds nothing.
    return base.resolve()


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
        # A directory whose markers cannot be seen may be the project root, so it is a boundary too.
        if any(_probe(current / marker) is not _Probe.MISSING for marker in marker_set):
            break
        if current.parent == current:
            break
        current = current.parent
    return tuple(path.resolve() for path in allowed)


def _find_dotenv_path(start: Path, markers: Sequence[str]) -> Path | None:
    r"""Locate the first ``.env`` file walking towards the filesystem root.

    The working directory is never changed: ``os.chdir`` is process-wide, so it would move every
    other thread for the duration, and changing back fails when the original cwd cannot be
    entered. A directory whose entries cannot be looked up ends the search with no result.

    Example:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmpdir:
        ...     base = Path(tmpdir)
        ...     _ = (base / '.env').write_text('FOO=1\n')
        ...     _find_dotenv_path(base, markers=()).resolve() == (base / '.env').resolve()
        True

    """
    for directory in _collect_allowed_directories(start, markers):
        candidate = directory / _DOTENV_FILENAME
        probe = _probe(candidate)
        if probe is _Probe.FILE:
            return candidate.resolve()
        if probe is _Probe.HIDDEN:
            return None
    return None


def _read_dotenv_text(path: Path) -> str:
    r"""Return the decoded content of ``path`` or raise :class:`DotenvDecodeError`.

    Decoding here, before python-dotenv sees the file, refuses a bad file as a whole: no entry
    from it reaches ``os.environ``, and the offset is the file's own byte offset.

    Example:
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as tmpdir:
        ...     env_file = Path(tmpdir) / ".env"
        ...     _ = env_file.write_bytes(b"A=1\nB=\xff\n")
        ...     try:
        ...         _read_dotenv_text(env_file)
        ...     except DotenvDecodeError as error:
        ...         print(error.offset)
        6

    """
    raw = path.read_bytes()
    try:
        return raw.decode(_DOTENV_ENCODING)
    except UnicodeDecodeError as error:
        # from None: the chained error would carry the raw bytes, which are secrets.
        raise DotenvDecodeError(path, error.start) from None


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

    Raises:
        FileNotFoundError: ``search_from`` does not exist.
        DotenvDecodeError: The discovered ``.env`` is not valid UTF-8; nothing from it is loaded.
        OSError: The discovered ``.env`` exists but cannot be read.

    """
    global _dotenv_state  # noqa: PLW0603 - lock-protected module-level cache of the dotenv load state

    start = _normalise_search_root(search_from)
    with _STATE_LOCK:
        if _dotenv_state.loaded and _dotenv_state.override == dotenv_override:
            return _dotenv_state.path

    candidate = None if start is None else _find_dotenv_path(start, markers)

    with _STATE_LOCK:
        if _dotenv_state.loaded and _dotenv_state.override == dotenv_override:
            return _dotenv_state.path
        if candidate is None:
            _dotenv_state = _DotenvState(loaded=True, override=dotenv_override, path=None)
            return None
        _load_dotenv(stream=io.StringIO(_read_dotenv_text(candidate)), override=dotenv_override)
        _dotenv_state = _DotenvState(loaded=True, override=dotenv_override, path=candidate)
        return candidate


def _reset_dotenv_state_for_testing() -> None:
    """Reset cached state - intended for unit tests and doctests only."""
    global _dotenv_state  # noqa: PLW0603 - lock-protected module-level cache of the dotenv load state
    with _STATE_LOCK:
        _dotenv_state = _DotenvState()
