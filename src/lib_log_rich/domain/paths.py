"""Cross-platform path handling utilities.

Purpose
-------
Provide pure functions for normalizing and serializing filesystem paths
across different operating systems, ensuring consistent representation
when paths are stored, transmitted, or logged.

Contents
--------
* :func:`normalize_path` - Convert path strings to platform-native format.
* :func:`path_to_posix` - Serialize paths using POSIX format.

System Role
-----------
Lives in the domain layer as pure string/path transformation logic with
no I/O dependencies. Used by adapters when serializing path values in
log event payloads.

Examples
--------
>>> from lib_log_rich.domain.paths import normalize_path, path_to_posix
>>> from pathlib import Path
>>> path_to_posix(Path("/home/user/file.txt"))
'/home/user/file.txt'

"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Final

__all__ = ["normalize_path", "path_to_posix"]

_IS_WINDOWS: Final[bool] = sys.platform == "win32"
_UNC_POSIX_PREFIX: Final[str] = "//"
_UNC_WINDOWS_PREFIX: Final[str] = "\\\\"


def _is_unc_path(path_str: str) -> bool:
    """Check if a path string represents a UNC path.

    Args:
        path_str: Path string to check.

    Returns:
        True if path starts with UNC prefix (// or \\\\).

    Examples:
        >>> _is_unc_path("//server/share")
        True
        >>> _is_unc_path("\\\\\\\\server\\\\share")
        True
        >>> _is_unc_path("/home/user")
        False
        >>> _is_unc_path("C:\\\\Users")
        False
        >>> _is_unc_path("")
        False

    """
    return path_str.startswith(_UNC_POSIX_PREFIX) or path_str.startswith(_UNC_WINDOWS_PREFIX)


def _normalize_unc_for_platform(path_str: str) -> str:
    """Normalize UNC path separators for the current platform.

    Args:
        path_str: UNC path string to normalize.

    Returns:
        Path string with platform-appropriate separators.

    """
    if _IS_WINDOWS:
        return path_str.replace("/", "\\")
    return path_str.replace("\\", "/")


def normalize_path(path_input: str | Path) -> Path:
    """Normalize a path to the current platform's native format.

    Handle UNC paths (//server/share or \\\\server\\share) by converting
    them to the appropriate format for the current operating system.
    Regular paths are normalized using pathlib's standard handling.

    Args:
        path_input: Path string or Path object to normalize.

    Returns:
        Normalized Path object using platform-native separators.

    Raises:
        ValueError: If path_input is an empty string.

    Examples:
        >>> from pathlib import Path
        >>> result = normalize_path("/home/user/file.txt")
        >>> isinstance(result, Path)
        True
        >>> result = normalize_path(Path("/home/user"))
        >>> isinstance(result, Path)
        True
        >>> normalize_path("")
        Traceback (most recent call last):
            ...
        ValueError: path_input cannot be empty

    """
    path_str = os.fspath(path_input) if isinstance(path_input, Path) else path_input

    if not path_str:
        raise ValueError("path_input cannot be empty")

    if _is_unc_path(path_str):
        normalized_str = _normalize_unc_for_platform(path_str)
        return Path(normalized_str)

    return Path(path_str)


def path_to_posix(path: Path | str) -> str:
    """Serialize a path to POSIX format for cross-platform storage.

    Use Path.as_posix() to ensure consistent forward-slash representation
    when paths are serialized, stored, or transmitted between systems.

    Args:
        path: Path object or string to serialize.

    Returns:
        POSIX-style path string with forward slashes.

    Examples:
        >>> from pathlib import Path
        >>> path_to_posix(Path("/home/user/file.txt"))
        '/home/user/file.txt'
        >>> path_to_posix("relative/path")
        'relative/path'
        >>> path_to_posix(Path("some/path/file.txt"))
        'some/path/file.txt'

    """
    if isinstance(path, str):
        path = Path(path)

    return path.as_posix()
