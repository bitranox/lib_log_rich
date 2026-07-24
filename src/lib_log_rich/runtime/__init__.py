"""Public runtime façade for lib_log_rich.

The heavy lifting lives in :mod:`lib_log_rich.runtime._api`; this module keeps the
import surface tidy by re-exporting the supported entry points and adapters.
"""

from __future__ import annotations

from lib_log_rich.adapters import (
    AsyncQueueConsoleAdapter,
    ExportStyle,
    QueueConsoleAdapter,
    RegexScrubber,
    RichConsoleAdapter,
)
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

from ._api import (
    RuntimeSnapshot,
    SeveritySnapshot,
    bind,
    dump,
    flush,
    flush_async,
    getLogger,
    hello_world,
    i_should_fail,
    init,
    inspect_runtime,
    max_level_seen,
    reset_severity_metrics,
    severity_snapshot,
    shutdown,
    shutdown_async,
    summary_info,
)
from ._composition import LoggerProxy
from ._settings import (
    ConsoleAppearance,
    DiagnosticHook,
    DumpDefaults,
    FeatureFlags,
    GraylogSettings,
    PayloadLimits,
    RuntimeConfig,
    RuntimeSettings,
    build_runtime_settings,
)
from ._state import LoggingRuntime, clear_runtime, current_runtime, get_minimum_log_level, is_initialised
from ._stdlib_handler import StdlibLoggingHandler, attach_std_logging

__all__ = [
    "CONSOLE_STYLE_THEMES",
    "AsyncQueueConsoleAdapter",
    "ConsoleAppearance",
    "DiagnosticHook",
    "DumpDefaults",
    "ExportStyle",
    "FeatureFlags",
    "GraylogSettings",
    "LoggerProxy",
    "LoggingRuntime",
    "PayloadLimits",
    "QueueConsoleAdapter",
    "RegexScrubber",
    "RichConsoleAdapter",
    "RuntimeConfig",
    "RuntimeSettings",
    "RuntimeSnapshot",
    "SeveritySnapshot",
    "StdlibLoggingHandler",
    "attach_std_logging",
    "bind",
    "build_runtime_settings",
    "clear_runtime",
    "current_runtime",
    "dump",
    "flush",
    "flush_async",
    "getLogger",
    "get_minimum_log_level",
    "hello_world",
    "i_should_fail",
    "init",
    "inspect_runtime",
    "is_initialised",
    "max_level_seen",
    "reset_severity_metrics",
    "severity_snapshot",
    "shutdown",
    "shutdown_async",
    "summary_info",
]
