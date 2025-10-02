"""Aggregated façade bridging runtime orchestration and demo helpers."""

from __future__ import annotations

from .demo import logdemo
from .domain.palettes import CONSOLE_STYLE_THEMES
from .runtime import (
    LoggerProxy,
    bind,
    dump,
    get,
    hello_world,
    i_should_fail,
    init,
    shutdown,
    shutdown_async,
    summary_info,
)

__all__ = [
    "LoggerProxy",
    "CONSOLE_STYLE_THEMES",
    "bind",
    "dump",
    "get",
    "hello_world",
    "i_should_fail",
    "init",
    "logdemo",
    "shutdown",
    "shutdown_async",
    "summary_info",
]
