"""Runtime state container and access helpers."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Awaitable, Callable, Mapping

from lib_log_rich.adapters.queue import QueueAdapter
from lib_log_rich.domain import ContextBinder, LogLevel


@dataclass(slots=True)
class LoggingRuntime:
    """Aggregate of live collaborators assembled by the composition root."""

    binder: ContextBinder
    process: Callable[..., dict[str, Any]]
    capture_dump: Callable[..., str]
    shutdown_async: Callable[[], Awaitable[None] | None]
    queue: QueueAdapter | None
    service: str
    environment: str
    console_level: LogLevel
    backend_level: LogLevel
    graylog_level: LogLevel
    theme: str | None
    console_styles: Mapping[str, str] | None


_runtime_state: LoggingRuntime | None = None
_runtime_lock = RLock()


def set_runtime(runtime: LoggingRuntime) -> None:
    """Install ``runtime`` as the active singleton."""

    with _runtime_lock:
        global _runtime_state
        _runtime_state = runtime


def clear_runtime() -> None:
    """Remove the active runtime if present."""

    with _runtime_lock:
        global _runtime_state
        _runtime_state = None


def current_runtime() -> LoggingRuntime:
    """Return the active runtime or raise when uninitialised."""

    with _runtime_lock:
        if _runtime_state is None:
            raise RuntimeError("lib_log_rich.init() must be called before using the logging API")
        return _runtime_state


def is_initialised() -> bool:
    """Return ``True`` when :func:`lib_log_rich.init` has been called."""

    with _runtime_lock:
        return _runtime_state is not None


__all__ = [
    "LoggingRuntime",
    "clear_runtime",
    "current_runtime",
    "is_initialised",
    "set_runtime",
]
