"""Support types for process_event helpers.

These typing constructs make the implicit coupling between the process use
case and queue wiring explicit, so Pyright and reviewers can rely on a shared
contract when attaching fan-out workers.
"""

from __future__ import annotations

from dataclasses import dataclass
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
from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer, SeverityMonitor

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
        message: object,
        args: tuple[object, ...] = (),
        exc_info: object | None = None,
        stack_info: object | None = None,
        stacklevel: int = 1,
        extra: Mapping[str, object] | None = None,
    ) -> ProcessResult: ...


class ProcessFactory(Protocol):
    def __call__(self, dependencies: "ProcessPipelineDependencies") -> ProcessCallable: ...


@dataclass(frozen=True)
class ProcessPipelineDependencies:
    """Bundle the collaborators required to build the process pipeline."""

    context_binder: ContextBinder
    ring_buffer: RingBuffer
    severity_monitor: SeverityMonitor
    console: ConsolePort
    console_level: LogLevel
    structured_backends: Sequence[StructuredBackendPort]
    backend_level: LogLevel
    graylog: GraylogPort | None
    graylog_level: LogLevel
    scrubber: ScrubberPort
    rate_limiter: RateLimiterPort
    clock: ClockPort
    id_provider: IdProvider
    limits: PayloadLimitsProtocol
    identity: SystemIdentityPort
    diagnostic: Callable[[str, dict[str, object]], None] | None = None
    colorize_console: bool = True
    queue: QueuePort | None = None


__all__ = [
    "FanOutCallable",
    "ProcessCallable",
    "ProcessPipelineDependencies",
    "ProcessFactory",
    "ProcessResult",
]
