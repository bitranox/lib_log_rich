"""Support types for process_event helpers.

These typing constructs make the implicit coupling between the process use
case and queue wiring explicit, so Pyright and reviewers can rely on a shared
contract when attaching fan-out workers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
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


class PayloadLimitsProtocol(Protocol):
    """Structural contract for payload limit configuration."""

    truncate_message: bool
    message_max_chars: int
    extra_max_keys: int
    extra_max_value_chars: int
    extra_max_depth: int
    extra_max_total_bytes: int | None
    context_max_keys: int
    context_max_value_chars: int
    stacktrace_max_frames: int


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Result of processing a log event through the pipeline.

    Replaces the previous dict[str, object] to provide type safety and
    clear documentation of the result contract.

    Attributes:
        ok: Whether the event was successfully processed.
        event_id: Identifier of the processed event (when available).
        reason: Failure reason when ok is False.
        queued: Whether the event was queued for async processing.
        failed_adapters: List of adapter names that failed during fan-out.
    """

    ok: bool
    event_id: str | None = None
    reason: str | None = None
    queued: bool = False
    failed_adapters: list[str] = field(default_factory=lambda: [])


DiagnosticPayload = Mapping[str, object]
DiagnosticCallback = Callable[[str, DiagnosticPayload], None]


@runtime_checkable
class FanOutCallable(Protocol):
    """Protocol for functions that dispatch events to adapters."""

    def __call__(self, event: LogEvent, /) -> list[str]:
        """Dispatch event, returning names of failed adapters."""
        ...


@runtime_checkable
class ProcessCallable(Protocol):
    """Protocol for the main event processing callable."""

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
    ) -> ProcessResult:
        """Process a log event through the pipeline."""
        ...


class ProcessFactory(Protocol):
    """Protocol for factories that build process pipelines."""

    def __call__(self, dependencies: ProcessPipelineDependencies) -> ProcessCallable:
        """Create a process callable from dependencies."""
        ...


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
    diagnostic: DiagnosticCallback | None = None
    colorize_console: bool = True
    queue: QueuePort | None = None


__all__ = [
    "DiagnosticCallback",
    "DiagnosticPayload",
    "FanOutCallable",
    "PayloadLimitsProtocol",
    "ProcessCallable",
    "ProcessPipelineDependencies",
    "ProcessFactory",
    "ProcessResult",
]
