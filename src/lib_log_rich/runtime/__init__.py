"""Public runtime façade for lib_log_rich.

The heavy lifting lives in :mod:`lib_log_rich.runtime._api`; this module keeps the
import surface tidy by re-exporting the supported entry points and adapters.
"""

from __future__ import annotations

from lib_log_rich.adapters import RegexScrubber, RichConsoleAdapter
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

from ._api import (
    RuntimeSnapshot,
    bind,
    dump,
    get,
    hello_world,
    init,
    inspect_runtime,
    i_should_fail,
    shutdown,
    shutdown_async,
    summary_info,
)
from ._composition import LoggerProxy
from ._settings import DiagnosticHook, build_runtime_settings
from ._state import LoggingRuntime, clear_runtime, current_runtime, is_initialised, set_runtime

__all__ = [
    "CONSOLE_STYLE_THEMES",
    "DiagnosticHook",
    "LoggingRuntime",
    "LoggerProxy",
    "RegexScrubber",
    "RichConsoleAdapter",
    "RuntimeSnapshot",
    "bind",
    "build_runtime_settings",
    "clear_runtime",
    "current_runtime",
    "dump",
    "get",
    "hello_world",
    "init",
    "inspect_runtime",
    "i_should_fail",
    "is_initialised",
    "set_runtime",
    "shutdown",
    "shutdown_async",
    "summary_info",
]
