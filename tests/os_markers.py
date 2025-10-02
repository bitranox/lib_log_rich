"""Shared pytest markers describing the operating-system expectations for tests."""

from __future__ import annotations

import os
import sys

import pytest

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_POSIX = os.name == "posix"
IS_LINUX = sys.platform.startswith("linux")

OS_AGNOSTIC = pytest.mark.os_agnostic
WINDOWS_ONLY = pytest.mark.skipif(not IS_WINDOWS, reason="Windows-specific behavior")
MACOS_ONLY = pytest.mark.skipif(not IS_MACOS, reason="macOS-specific behavior")
POSIX_ONLY = pytest.mark.skipif(not IS_POSIX, reason="POSIX-specific behavior")
LINUX_ONLY = pytest.mark.skipif(not IS_LINUX, reason="Linux-specific behavior")
