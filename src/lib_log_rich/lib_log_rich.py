"""Aggregated façade bridging runtime orchestration and demo helpers."""

from __future__ import annotations

from .demo import logdemo
from .domain.palettes import CONSOLE_STYLE_THEMES
from .runtime import (
    LoggerProxy,
    RuntimeConfig,
    SeveritySnapshot,
    bind,
    dump,
    get_minimum_log_level,
    getLogger,
    hello_world,
    i_should_fail,
    init,
    max_level_seen,
    reset_severity_metrics,
    severity_snapshot,
    shutdown,
    shutdown_async,
    summary_info,
)

__all__ = [
    "LoggerProxy",
    "RuntimeConfig",
    "SeveritySnapshot",
    "CONSOLE_STYLE_THEMES",
    "bind",
    "dump",
    "getLogger",
    "get_minimum_log_level",
    "hello_world",
    "i_should_fail",
    "init",
    "logdemo",
    "max_level_seen",
    "reset_severity_metrics",
    "severity_snapshot",
    "shutdown",
    "shutdown_async",
    "summary_info",
]
