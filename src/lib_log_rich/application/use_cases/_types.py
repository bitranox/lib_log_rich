"""Support types for process_event helpers.

These typing constructs make the implicit coupling between the process use
case and queue wiring explicit, so Pyright and reviewers can rely on a shared
contract when attaching fan-out workers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from lib_log_rich.application.ports import (
    ClockPort,
    ConsolePort,
    GraylogPort,
    IdProvider,
    QueuePort,
    RateLimiterPort,
    ScrubberPort,
    StructuredBackendPort,
    SystemIdentityPort,
)
from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer

from ._payload_sanitizer import PayloadLimitsProtocol


ProcessResult = dict[str, object]


@runtime_checkable
class FanOutCallable(Protocol):
    def __call__(self, event: LogEvent, /) -> list[str]: ...


@runtime_checkable
class ProcessCallable(Protocol):
    fan_out: FanOutCallable

    def __call__(
        self,
        *,
        logger_name: str,
        level: LogLevel,
        message: str,
        extra: Mapping[str, object] | None = None,
    ) -> ProcessResult: ...


class ProcessFactory(Protocol):
    def __call__(
        self,
        *,
        context_binder: ContextBinder,
        ring_buffer: RingBuffer,
        console: ConsolePort,
        console_level: LogLevel,
        structured_backends: Sequence[StructuredBackendPort],
        backend_level: LogLevel,
        graylog: GraylogPort | None,
        graylog_level: LogLevel,
        scrubber: ScrubberPort,
        rate_limiter: RateLimiterPort,
        clock: ClockPort,
        id_provider: IdProvider,
        queue: QueuePort | None,
        limits: PayloadLimitsProtocol,
        colorize_console: bool = True,
        diagnostic: Callable[[str, dict[str, object]], None] | None = None,
        identity: SystemIdentityPort,
    ) -> ProcessCallable: ...


__all__ = [
    "FanOutCallable",
    "ProcessCallable",
    "ProcessFactory",
    "ProcessResult",
]
