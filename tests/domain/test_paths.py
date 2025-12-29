"""Tests for cross-platform path handling utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib_log_rich.domain.paths import (
    normalize_path,
    path_to_posix,
)
from tests.os_markers import OS_AGNOSTIC, POSIX_ONLY, WINDOWS_ONLY

pytestmark = [OS_AGNOSTIC]


class TestNormalizePath:
    """Verify path normalization across platforms."""

    def test_rejects_empty_string(self) -> None:
        """Empty string input raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            normalize_path("")

    def test_normalizes_posix_path_to_path_object(self) -> None:
        """POSIX-style input becomes a Path object."""
        result = normalize_path("/home/user/file.txt")
        assert isinstance(result, Path)

    def test_normalizes_path_object_input(self) -> None:
        """Path object input remains a Path object."""
        result = normalize_path(Path("/home/user"))
        assert isinstance(result, Path)

    def test_handles_relative_path(self) -> None:
        """Relative paths are preserved as relative."""
        result = normalize_path("relative/path/file.txt")
        assert isinstance(result, Path)
        assert not result.is_absolute()

    def test_handles_path_with_spaces(self) -> None:
        """Paths containing spaces are handled correctly."""
        result = normalize_path("/path/with spaces/file.txt")
        assert isinstance(result, Path)


class TestNormalizePathUncHandling:
    """Verify UNC path normalization behavior."""

    def test_accepts_posix_unc_path(self) -> None:
        """Forward-slash UNC path is accepted."""
        result = normalize_path("//server/share/file.txt")
        assert isinstance(result, Path)

    def test_accepts_windows_unc_path(self) -> None:
        """Backslash UNC path is accepted."""
        result = normalize_path("\\\\server\\share\\file.txt")
        assert isinstance(result, Path)


class TestPathToPosix:
    """Verify POSIX serialization for cross-platform storage."""

    def test_serializes_posix_path(self) -> None:
        """POSIX path serializes with forward slashes."""
        result = path_to_posix(Path("/home/user/file.txt"))
        assert result == "/home/user/file.txt"
        assert "/" in result

    def test_serializes_relative_path(self) -> None:
        """Relative path serializes correctly."""
        result = path_to_posix(Path("relative/path"))
        assert result == "relative/path"

    def test_serializes_string_input(self) -> None:
        """String input is converted and serialized."""
        result = path_to_posix("some/path/file.txt")
        assert result == "some/path/file.txt"

    def test_output_contains_no_backslashes(self) -> None:
        """Output never contains backslash separators."""
        result = path_to_posix(Path("some/path/file.txt"))
        assert "\\" not in result


@POSIX_ONLY
class TestPosixSpecificBehavior:
    """Tests specific to POSIX systems."""

    def test_unc_path_uses_forward_slashes(self) -> None:
        """On POSIX, UNC paths use forward slashes."""
        result = normalize_path("\\\\server\\share\\file.txt")
        path_str = str(result)
        assert "\\" not in path_str or path_str.startswith("//")


@WINDOWS_ONLY
class TestWindowsSpecificBehavior:
    """Tests specific to Windows systems."""

    def test_unc_path_uses_backslashes(self) -> None:
        """On Windows, UNC paths use backslashes."""
        result = normalize_path("//server/share/file.txt")
        path_str = str(result)
        assert path_str.startswith("\\\\")

    def test_path_to_posix_converts_backslashes(self) -> None:
        """On Windows, backslashes are converted to forward slashes."""
        result = path_to_posix(Path("C:\\Users\\test\\file.txt"))
        assert "/" in result
        assert "\\" not in result
