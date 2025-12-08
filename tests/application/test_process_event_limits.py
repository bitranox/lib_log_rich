from __future__ import annotations

import json
from typing import Callable

from lib_log_rich.application import ProcessPipelineDependencies
from lib_log_rich.application.ports import (
    ClockPort,
    ConsolePort,
    IdProvider,
    QueuePort,
    RateLimiterPort,
    ScrubberPort,
    StructuredBackendPort,
    SystemIdentityPort,
)
from lib_log_rich.application.use_cases._types import DiagnosticPayload, ProcessResult
from lib_log_rich.application.use_cases.process_event import create_process_log_event
from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer, SeverityMonitor, SystemIdentity
from lib_log_rich.runtime import PayloadLimits
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


class DummyConsole(ConsolePort):
    def __init__(self) -> None:
        self.events: list[LogEvent] = []

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        self.events.append(event)


class DummyBackend(StructuredBackendPort):
    def __init__(self) -> None:
        self.events: list[LogEvent] = []

    def emit(self, event: LogEvent) -> None:
        self.events.append(event)


class DummyQueue(QueuePort):
    def __init__(self) -> None:
        self.events: list[LogEvent] = []

    def put(self, event: LogEvent) -> bool:  # pragma: no cover - queue-disabled tests keep this unused
        self.events.append(event)
        return True


class RejectingQueue(DummyQueue):
    def start(self) -> None:  # pragma: no cover - queue protocol stub
        return None

    def stop(self, *, drain: bool = True, timeout: float | None = 5.0) -> None:  # pragma: no cover - queue protocol stub
        return None

    def put(self, event: LogEvent) -> bool:
        super().put(event)
        return False


class DummyClock(ClockPort):
    def now(self):  # type: ignore[override]
        from datetime import datetime, timezone

        return datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc)


class DummyId(IdProvider):
    def __init__(self) -> None:
        self.counter = 0

    def __call__(self) -> str:
        self.counter += 1
        return f"event-{self.counter}"


class DummyScrubber(ScrubberPort):
    def scrub(self, event: LogEvent) -> LogEvent:  # pragma: no cover - simple passthrough
        return event


class AllowAllLimiter(RateLimiterPort):
    def allow(self, event: LogEvent) -> bool:  # pragma: no cover - simple passthrough
        return True


class DummyIdentity(SystemIdentityPort):
    def __init__(self, *, pid: int = 111, user: str | None = "svc", host: str | None = "host") -> None:
        self._identity = SystemIdentity(user_name=user, hostname=host, process_id=pid)

    def resolve_identity(self) -> SystemIdentity:
        return self._identity


def _make_process(
    *,
    limits: PayloadLimits | None = None,
    collector: list[tuple[str, DiagnosticPayload]] | None = None,
    queue: QueuePort | None = None,
    monitor: SeverityMonitor | None = None,
) -> tuple[
    ContextBinder,
    RingBuffer,
    DummyConsole,
    list[tuple[str, DiagnosticPayload]],
    Callable[..., ProcessResult],
    SeverityMonitor,
]:
    binder = ContextBinder()
    ring = RingBuffer(max_events=50)
    console = DummyConsole()
    severity_monitor = monitor or SeverityMonitor()
    diagnostics: list[tuple[str, DiagnosticPayload]] = collector if collector is not None else []

    def diag(name: str, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    dependencies = ProcessPipelineDependencies(
        context_binder=binder,
        ring_buffer=ring,
        severity_monitor=severity_monitor,
        console=console,
        console_level=LogLevel.DEBUG,
        structured_backends=(),
        backend_level=LogLevel.INFO,
        graylog=None,
        graylog_level=LogLevel.ERROR,
        scrubber=DummyScrubber(),
        rate_limiter=AllowAllLimiter(),
        clock=DummyClock(),
        id_provider=DummyId(),
        limits=limits or PayloadLimits(),
        identity=DummyIdentity(),
        diagnostic=diag,
        colorize_console=True,
        queue=queue,
    )
    process = create_process_log_event(dependencies)
    return binder, ring, console, diagnostics, process, severity_monitor


def _capture_event(ring: RingBuffer) -> LogEvent:
    snapshot = ring.snapshot()
    assert snapshot, "ring buffer should contain at least one event"
    return snapshot[-1]


def test_message_truncated() -> None:
    binder, ring, _, diagnostics, process, _ = _make_process()
    long_message = "x" * 5000
    with binder.bind(service="svc", environment="prod", job_id="job"):
        process(logger_name="svc.worker", level=LogLevel.INFO, message=long_message, extra=None)
    event = _capture_event(ring)
    assert len(event.message) == 4096
    assert event.message.endswith("…[truncated]")
    assert any(name == "message_truncated" for name, _ in diagnostics)


def test_extra_keys_clamped() -> None:
    binder, ring, _, diagnostics, process, _ = _make_process()
    extra = {f"k{i}": i for i in range(30)}
    with binder.bind(service="svc", environment="prod", job_id="job"):
        process(logger_name="svc.worker", level=LogLevel.INFO, message="hello", extra=extra)
    event = _capture_event(ring)
    assert len(event.extra) == 25
    dropped = [payload.get("dropped_keys") for name, payload in diagnostics if name == "extra_keys_dropped"]
    assert dropped and dropped[0] == [f"k{i}" for i in range(25, 30)]


def test_extra_value_truncated() -> None:
    binder, ring, _, diagnostics, process, _ = _make_process()
    long_value = "y" * 600
    with binder.bind(service="svc", environment="prod", job_id="job"):
        process(logger_name="svc.worker", level=LogLevel.INFO, message="hello", extra={"field": long_value})
    event = _capture_event(ring)
    assert len(event.extra["field"]) == 512
    assert event.extra["field"].endswith("…[truncated]")
    assert any(name == "extra_value_truncated" for name, _ in diagnostics)


def test_extra_total_bytes_trimmed() -> None:
    limits = PayloadLimits(extra_max_total_bytes=2048)
    binder, ring, _, diagnostics, process, _ = _make_process(limits=limits)
    extra = {f"key{i}": "z" * 200 for i in range(20)}
    with binder.bind(service="svc", environment="prod", job_id="job"):
        process(logger_name="svc.worker", level=LogLevel.INFO, message="hello", extra=extra)
    event = _capture_event(ring)
    encoded = json.dumps(event.extra, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= 2048
    assert any(name == "extra_total_trimmed" for name, _ in diagnostics)


def test_nested_extra_depth_collapsed() -> None:
    binder, ring, _, _, process, _ = _make_process()
    deep_value = {"a": {"b": {"c": {"d": "deep"}}}}
    with binder.bind(service="svc", environment="prod", job_id="job"):
        process(logger_name="svc.worker", level=LogLevel.INFO, message="hello", extra={"deep": deep_value})
    event = _capture_event(ring)
    collapsed = event.extra["deep"]["a"]["b"]["c"]
    assert isinstance(collapsed, str)
    assert "d" in collapsed


def test_context_extra_clamped() -> None:
    binder, _, _, diagnostics, process, _ = _make_process()
    context_extra = {f"ctx{i}": f"value-{i}" for i in range(25)}
    with binder.bind(service="svc", environment="prod", job_id="job", extra=context_extra):
        process(logger_name="svc.worker", level=LogLevel.INFO, message="hello", extra=None)
        current = binder.current()
        assert current is not None
        assert len(current.extra) == 20
    assert any(name == "context_extra_keys_dropped" for name, _ in diagnostics)


def test_exc_info_compacted() -> None:
    binder, ring, _, diagnostics, process, _ = _make_process()
    traceback_lines = [f"frame {i}" for i in range(40)]
    exc_info = "\n".join(traceback_lines)
    with binder.bind(service="svc", environment="prod", job_id="job"):
        process(logger_name="svc.worker", level=LogLevel.ERROR, message="boom", extra={"exc_info": exc_info})
    event = _capture_event(ring)
    assert event.exc_info is not None
    assert "... truncated" in event.exc_info
    assert any(name == "exc_info_truncated" for name, _ in diagnostics)


def test_queue_rejection_records_drop_reason() -> None:
    diagnostics: list[tuple[str, DiagnosticPayload]] = []
    queue = RejectingQueue()
    binder, _, _, diagnostics, process, monitor = _make_process(collector=diagnostics, queue=queue)
    with binder.bind(service="svc", environment="prod", job_id="job"):
        result = process(logger_name="svc.worker", level=LogLevel.INFO, message="queued", extra=None)
    assert result.ok is False
    assert result.reason == "queue_full"
    assert monitor.drops_by_reason()["queue_full"] == 1


def test_duplicate_numeric_keys_preserve_latest_value() -> None:
    binder, ring, _, _, process, _ = _make_process()
    with binder.bind(service="svc", environment="prod", job_id="job"):
        process(logger_name="svc.worker", level=LogLevel.INFO, message="hello", extra={1: "secret", "1": "safe"})
    event = _capture_event(ring)
    assert event.extra["1"] == "safe"
