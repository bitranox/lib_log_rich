"""Factory utilities supporting runtime composition.

These helpers are separated from ``_composition`` to keep responsibilities
focused: composition orchestrates wiring, while this module provides reusable
building blocks and lightweight facades.
"""

from __future__ import annotations

import getpass
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Optional

from lib_log_rich.adapters import (
    DumpAdapter,
    GraylogAdapter,
    JournaldAdapter,
    RegexScrubber,
    RichConsoleAdapter,
    SlidingWindowRateLimiter,
    WindowsEventLogAdapter,
)
from lib_log_rich.application.ports import ClockPort, ConsolePort, IdProvider, RateLimiterPort, StructuredBackendPort
from lib_log_rich.application.use_cases.dump import create_capture_dump
from lib_log_rich.domain import ContextBinder, DumpFilter, DumpFormat, LogContext, LogEvent, LogLevel, RingBuffer

from ._settings import ConsoleAppearance, DumpDefaults, FeatureFlags, GraylogSettings, RuntimeSettings


class SystemClock(ClockPort):
    """Concrete clock port returning timezone-aware UTC timestamps."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class UuidProvider(IdProvider):
    """Generate stable hexadecimal identifiers for log events."""

    def __call__(self) -> str:
        from uuid import uuid4

        return uuid4().hex


class AllowAllRateLimiter(RateLimiterPort):
    """Fallback rate limiter that never throttles events."""

    def allow(self, event: LogEvent) -> bool:  # noqa: ARG002 - interface parity
        return True


class LoggerProxy:
    """Lightweight facade for structured logging calls."""

    def __init__(self, name: str, process: Callable[..., dict[str, Any]]) -> None:
        self._name = name
        self._process = process

    def debug(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        return self._log(LogLevel.DEBUG, message, extra)

    def info(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        return self._log(LogLevel.INFO, message, extra)

    def warning(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        return self._log(LogLevel.WARNING, message, extra)

    def error(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        return self._log(LogLevel.ERROR, message, extra)

    def critical(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        return self._log(LogLevel.CRITICAL, message, extra)

    def _log(
        self,
        level: LogLevel,
        message: str,
        extra: Optional[MutableMapping[str, Any]],
    ) -> dict[str, Any]:
        payload: MutableMapping[str, Any] = extra if extra is not None else {}
        return self._process(logger_name=self._name, level=level, message=message, extra=payload)


def create_dump_renderer(
    *,
    ring_buffer: RingBuffer,
    dump_defaults: DumpDefaults,
    theme: str | None,
    console_styles: Mapping[str, str] | None,
) -> Callable[
    [DumpFormat, Path | None, LogLevel | None, str | None, str | None, str | None, str | None, Mapping[str, str] | None, DumpFilter | None, bool],
    str,
]:
    return create_capture_dump(
        ring_buffer=ring_buffer,
        dump_port=DumpAdapter(),
        default_template=dump_defaults.format_template,
        default_format_preset=dump_defaults.format_preset,
        default_theme=theme,
        default_console_styles=console_styles,
    )


def create_runtime_binder(service: str, environment: str) -> ContextBinder:
    identity = system_identity()
    binder = ContextBinder()
    base = LogContext(
        service=service,
        environment=environment,
        job_id="bootstrap",
        user_name=identity["user_name"],
        hostname=identity["hostname"],
        process_id=identity["process_id"],
        process_id_chain=(identity["process_id"],),
    )
    binder.deserialize({"version": 1, "stack": [base.to_dict(include_none=True)]})
    return binder


def system_identity() -> dict[str, Any]:
    try:
        user_name = getpass.getuser()
    except Exception:  # pragma: no cover - platform dependent
        user_name = os.getenv("USER") or os.getenv("USERNAME")
    hostname_value = socket.gethostname() or ""
    hostname = hostname_value.split(".", 1)[0] if hostname_value else None
    return {"user_name": user_name, "hostname": hostname, "process_id": os.getpid()}


def create_ring_buffer(enabled: bool, size: int) -> RingBuffer:
    capacity = size if enabled else 1024
    return RingBuffer(max_events=capacity)


def create_console(console: ConsoleAppearance) -> ConsolePort:
    return RichConsoleAdapter(
        force_color=console.force_color,
        no_color=console.no_color,
        styles=console.styles,
        format_preset=console.format_preset,
        format_template=console.format_template,
    )


def create_structured_backends(flags: FeatureFlags) -> list[StructuredBackendPort]:
    backends: list[StructuredBackendPort] = []
    if flags.journald:
        backends.append(JournaldAdapter())
    if flags.eventlog:
        backends.append(WindowsEventLogAdapter())
    return backends


def create_graylog_adapter(settings: GraylogSettings) -> GraylogAdapter | None:
    if not settings.enabled or settings.endpoint is None:
        return None
    host, port = settings.endpoint
    return GraylogAdapter(
        host=host,
        port=port,
        enabled=True,
        protocol=settings.protocol,
        use_tls=settings.tls,
    )


def compute_thresholds(settings: RuntimeSettings, graylog: GraylogAdapter | None) -> tuple[LogLevel, LogLevel, LogLevel]:
    console_level = coerce_level(settings.console_level)
    backend_level = coerce_level(settings.backend_level)
    graylog_level = coerce_level(settings.graylog_level)
    if graylog is None:
        graylog_level = LogLevel.CRITICAL
    return console_level, backend_level, graylog_level


def create_scrubber(patterns: dict[str, str]) -> RegexScrubber:
    """Instantiate the configured scrubber class kept on the runtime module."""
    from lib_log_rich import runtime as runtime_module  # local import for monkeypatchability

    scrubber_cls = getattr(runtime_module, "RegexScrubber", RegexScrubber)
    return scrubber_cls(patterns=patterns)


def create_rate_limiter(rate_limit: Optional[tuple[int, float]]) -> RateLimiterPort:
    if rate_limit is None:
        return AllowAllRateLimiter()
    max_events, interval_seconds = rate_limit
    return SlidingWindowRateLimiter(max_events=max_events, interval=timedelta(seconds=interval_seconds))


def coerce_level(level: str | LogLevel) -> LogLevel:
    if isinstance(level, LogLevel):
        return level
    return LogLevel.from_name(level)


__all__ = [
    "AllowAllRateLimiter",
    "LoggerProxy",
    "SystemClock",
    "UuidProvider",
    "compute_thresholds",
    "coerce_level",
    "create_console",
    "create_dump_renderer",
    "create_graylog_adapter",
    "create_rate_limiter",
    "create_ring_buffer",
    "create_runtime_binder",
    "create_scrubber",
    "create_structured_backends",
    "system_identity",
]
