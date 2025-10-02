"""Runtime state container and access helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Mapping

from lib_log_rich.adapters.queue import QueueAdapter
from lib_log_rich.domain import ContextBinder, LogLevel


@dataclass(slots=True)
class LoggingRuntime:
    """Aggregate of live collaborators assembled by the composition root."""

    binder: ContextBinder
    process: Callable[..., dict[str, Any]]
    capture_dump: Callable[..., str]
    shutdown_async: Callable[[], asyncio.Future | Any]
    queue: QueueAdapter | None
    service: str
    environment: str
    console_level: LogLevel
    backend_level: LogLevel
    graylog_level: LogLevel
    theme: str | None
    console_styles: Mapping[LogLevel | str, str] | None


_STATE: LoggingRuntime | None = None
_STATE_LOCK = RLock()


def set_runtime(runtime: LoggingRuntime) -> None:
    """Install ``runtime`` as the active singleton."""

    with _STATE_LOCK:
        global _STATE
        _STATE = runtime


def clear_runtime() -> None:
    """Remove the active runtime if present."""

    with _STATE_LOCK:
        global _STATE
        _STATE = None


def current_runtime() -> LoggingRuntime:
    """Return the active runtime or raise when uninitialised."""

    with _STATE_LOCK:
        if _STATE is None:
            raise RuntimeError("lib_log_rich.init() must be called before using the logging API")
        return _STATE


def is_initialised() -> bool:
    """Return ``True`` when :func:`lib_log_rich.init` has been called."""

    with _STATE_LOCK:
        return _STATE is not None


__all__ = [
    "LoggingRuntime",
    "clear_runtime",
    "current_runtime",
    "is_initialised",
    "set_runtime",
]
