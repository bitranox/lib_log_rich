from __future__ import annotations

import asyncio
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Awaitable, Mapping, cast

import pytest
from rich.console import Console

from lib_log_rich.adapters import (
    DumpAdapter,
    QueueAdapter,
    RegexScrubber,
    RichConsoleAdapter,
    SlidingWindowRateLimiter,
)
from lib_log_rich.application import ProcessPipelineDependencies
from lib_log_rich.application.ports import ConsolePort, DumpPort, GraylogPort, QueuePort, StructuredBackendPort, SystemIdentityPort
from lib_log_rich.application.use_cases._payload_sanitizer import PayloadSanitizer
from lib_log_rich.application.use_cases._types import DiagnosticPayload, ProcessResult
from lib_log_rich.application.use_cases.dump import create_capture_dump
from lib_log_rich.application.use_cases.process_event import create_process_log_event
from lib_log_rich.application.use_cases.shutdown import create_shutdown
from lib_log_rich.domain import ContextBinder, LogContext, LogEvent, LogLevel, RingBuffer, SeverityMonitor, SystemIdentity
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.dump_filter import DumpFilter, build_dump_filter
from lib_log_rich.domain.enums import QueuePolicy
from lib_log_rich.runtime import PayloadLimits
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

CHAIN_LIMIT = 8


class StaticClock:
    """Deterministic clock advancing by ``step`` each call."""

    def __init__(self, start: datetime, step: timedelta | None = None) -> None:
        self._current = start
        self._step = step or timedelta()

    def now(self) -> datetime:
        current = self._current
        self._current = current + self._step
        return current


class SequentialId:
    """ID provider yielding zero-padded event identifiers."""

    def __init__(self) -> None:
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f"evt-{self._counter:06d}"


class StaticIdentity(SystemIdentityPort):
    """Return a configurable identity for context refresh assertions."""

    def __init__(self, *, user_name: str | None = "svc", hostname: str | None = "node", process_id: int = 4242) -> None:
        self._identity = SystemIdentity(user_name=user_name, hostname=hostname, process_id=process_id)

    def resolve_identity(self) -> SystemIdentity:
        return self._identity


class RecordingBackend(StructuredBackendPort):
    """Structured backend capturing emitted events for assertions."""

    def __init__(self) -> None:
        self.emitted: list[LogEvent] = []

    def emit(self, event: LogEvent) -> None:
        self.emitted.append(event)


@contextmanager
def default_context(binder: ContextBinder) -> Iterator[LogContext]:
    """Bind a minimal context without hostname/user to trigger refresh."""
    with binder.bind(service="svc", environment="test", job_id="job-001", extra={"token": "top-secret"}) as ctx:
        yield ctx


def test_process_pipeline_dependencies_defaults() -> None:
    binder = ContextBinder()
    ring = RingBuffer(max_events=8)
    monitor = SeverityMonitor()
    console_adapter = RichConsoleAdapter(console=Console(file=StringIO(), record=True), no_color=True)
    sanitizer = RegexScrubber(patterns={})
    limiter = SlidingWindowRateLimiter(max_events=3, interval=timedelta(seconds=30))
    clock = StaticClock(datetime(2025, 10, 14, 9, 0, tzinfo=timezone.utc))
    ids = SequentialId()
    identity = StaticIdentity()

    dependencies = ProcessPipelineDependencies(
        context_binder=binder,
        ring_buffer=ring,
        severity_monitor=monitor,
        console=console_adapter,
        console_level=LogLevel.INFO,
        structured_backends=(),
        backend_level=LogLevel.INFO,
        graylog=None,
        graylog_level=LogLevel.ERROR,
        scrubber=sanitizer,
        rate_limiter=limiter,
        clock=clock,
        id_provider=ids,
        limits=PayloadLimits(),
        identity=identity,
    )

    assert dependencies.queue is None
    assert dependencies.diagnostic is None
    assert dependencies.colorize_console is True

    processed: list[tuple[str, DiagnosticPayload]] = []

    def recorder(name: str, payload: DiagnosticPayload) -> None:
        processed.append((name, payload))

    enriched = dependencies.__class__(**dependencies.__dict__ | {"diagnostic": recorder})

    process = create_process_log_event(enriched)
    with default_context(binder):
        result = process(logger_name="tests.defaults", level=LogLevel.INFO, message="hello", extra=None)

    assert result.ok is True
    event_id = result.event_id
    assert isinstance(event_id, str)
    assert event_id.startswith("evt-")
    assert ring.snapshot()[-1].logger_name == "tests.defaults"
    assert processed[-1][0] == "emitted"


def build_process(
    *,
    binder: ContextBinder,
    console: ConsolePort | None = None,
    backends: Sequence[StructuredBackendPort] | None = None,
    graylog: Any | None = None,
    scrubber: RegexScrubber | None = None,
    rate_limiter: SlidingWindowRateLimiter | None = None,
    clock: StaticClock | None = None,
    ids: SequentialId | None = None,
    queue: QueueAdapter | None = None,
    limits: PayloadLimits | None = None,
    diagnostic: Callable[[str, DiagnosticPayload], None] | None = None,
    monitor: SeverityMonitor | None = None,
    ring_buffer: RingBuffer | None = None,
    identity: SystemIdentityPort | None = None,
    colorize_console: bool = False,
) -> tuple[Callable[..., ProcessResult], RingBuffer, SeverityMonitor]:
    ring = ring_buffer or RingBuffer(max_events=32)
    severity_monitor = monitor or SeverityMonitor()
    console_adapter = console or RichConsoleAdapter(console=Console(file=StringIO(), record=True), no_color=True)
    structured_backends = tuple(backends or ())
    scrub = scrubber or RegexScrubber(patterns={})
    limiter = rate_limiter or SlidingWindowRateLimiter(max_events=10, interval=timedelta(seconds=60))
    clock_port = clock or StaticClock(datetime(2025, 10, 13, 12, 0, tzinfo=timezone.utc))
    id_provider = ids or SequentialId()
    limit_config = limits or PayloadLimits()
    identity_port = identity or StaticIdentity()

    dependencies = ProcessPipelineDependencies(
        context_binder=binder,
        ring_buffer=ring,
        severity_monitor=severity_monitor,
        console=console_adapter,
        console_level=LogLevel.DEBUG,
        structured_backends=structured_backends,
        backend_level=LogLevel.DEBUG,
        graylog=graylog,
        graylog_level=LogLevel.DEBUG,
        scrubber=scrub,
        rate_limiter=limiter,
        clock=clock_port,
        id_provider=id_provider,
        limits=limit_config,
        identity=identity_port,
        diagnostic=diagnostic,
        colorize_console=colorize_console,
        queue=queue,
    )
    process = create_process_log_event(dependencies)
    return process, ring, severity_monitor


def test_process_event_scrubs_payload_before_emitting() -> None:
    """Scrubbing redacts secrets and truncates payloads before fan-out."""
    binder = ContextBinder()
    backend = RecordingBackend()
    console_buffer = Console(file=StringIO(), record=True, force_terminal=False)
    console = RichConsoleAdapter(console=console_buffer, no_color=True)
    diagnostics: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=12,
        extra_max_keys=2,
        extra_max_value_chars=48,
        extra_max_depth=2,
        extra_max_total_bytes=512,
        context_max_keys=2,
        context_max_value_chars=48,
        stacktrace_max_frames=3,
    )
    process, ring, _ = build_process(
        binder=binder,
        console=console,
        backends=(backend,),
        scrubber=RegexScrubber(patterns={"token": "secret"}),
        limits=limits,
        diagnostic=diagnostic,
    )

    with default_context(binder):
        result = process(
            logger_name="tests.scrub",
            level=LogLevel.INFO,
            message="secret message content",
            extra={"token": "secret-123"},
        )

    event = ring.snapshot()[0]
    assert result.ok is True
    assert result.event_id == "evt-000001"
    assert backend.emitted[0].extra["token"] == "***"
    assert event.message.endswith("…[truncated]")
    assert event.context.hostname == "node"
    assert diagnostics[-1][0] == "emitted"


def test_process_event_formats_message_arguments() -> None:
    binder = ContextBinder()
    process, ring, _ = build_process(binder=binder)

    with default_context(binder):
        process(
            logger_name="tests.format",
            level=LogLevel.INFO,
            message="formatted %s %d",
            args=("value", 7),
        )

    event = ring.snapshot()[0]
    assert event.message == "formatted value 7"


def test_process_event_reports_formatting_failure() -> None:
    binder = ContextBinder()
    diagnostics: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    process, ring, _ = build_process(binder=binder, diagnostic=diagnostic)

    with default_context(binder):
        process(
            logger_name="tests.format",
            level=LogLevel.INFO,
            message="value %s %s",
            args=("only-one",),
        )

    event = ring.snapshot()[0]
    assert "formatting failed" in event.message
    assert any(name == "message_format_failed" for name, _ in diagnostics)


def test_process_event_accepts_exc_info_argument() -> None:
    binder = ContextBinder()
    process, ring, _ = build_process(binder=binder)

    exc: tuple[object, object, object] | None = None
    with default_context(binder):
        try:
            raise ValueError("boom")
        except ValueError:
            exc = sys.exc_info()
        assert exc is not None
        process(
            logger_name="tests.exc",
            level=LogLevel.ERROR,
            message="boom",
            exc_info=exc,
        )

    event = ring.snapshot()[0]
    assert event.exc_info is not None
    assert "ValueError" in event.exc_info


def test_process_event_records_stack_info() -> None:
    binder = ContextBinder()
    diagnostics: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=64,
        extra_max_keys=5,
        extra_max_value_chars=64,
        extra_max_depth=2,
        extra_max_total_bytes=512,
        context_max_keys=2,
        context_max_value_chars=32,
        stacktrace_max_frames=1,
    )
    process, ring, _ = build_process(binder=binder, limits=limits, diagnostic=diagnostic)

    stack_payload = "\n".join(f"frame {idx}" for idx in range(6))
    with default_context(binder):
        process(
            logger_name="tests.stack",
            level=LogLevel.WARNING,
            message="stack",
            stack_info=stack_payload,
        )

    event = ring.snapshot()[0]
    assert event.stack_info is not None
    assert "truncated" in event.stack_info
    assert any(name == "stack_info_truncated" for name, _ in diagnostics)


def test_process_event_rejects_message_when_truncation_disabled() -> None:
    """Oversized messages raise when truncation is disabled."""
    binder = ContextBinder()
    process, _, _ = build_process(
        binder=binder,
        limits=PayloadLimits(
            truncate_message=False,
            message_max_chars=5,
            extra_max_keys=4,
            extra_max_value_chars=32,
            extra_max_depth=2,
            extra_max_total_bytes=256,
            context_max_keys=2,
            context_max_value_chars=32,
            stacktrace_max_frames=4,
        ),
    )

    with pytest.raises(ValueError):
        with binder.bind(service="svc", environment="test", job_id="job-trunc"):
            process(logger_name="tests.message", level=LogLevel.INFO, message="toolong")


def test_process_event_sanitizes_nested_payload() -> None:
    """Nested extras, exc_info, and context are clamped and diagnosed."""
    binder = ContextBinder()
    diagnostics: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=12,
        extra_max_keys=3,
        extra_max_value_chars=10,
        extra_max_depth=1,
        extra_max_total_bytes=60,
        context_max_keys=1,
        context_max_value_chars=8,
        stacktrace_max_frames=2,
    )

    process, ring, _ = build_process(
        binder=binder,
        backends=(),
        scrubber=RegexScrubber(patterns={}),
        limits=limits,
        diagnostic=diagnostic,
    )

    extra_payload = {
        "a": "1234567890123",
        "b": {"nested": {"deep": "value"}},
        "overflow": "x" * 30,
        "dropme": "zzz",
        "exc_info": "\n".join(f"frame {idx} with lengthy detail payload" for idx in range(10)),
    }

    with binder.bind(
        service="svc",
        environment="test",
        job_id="job-sanitize",
        extra={"session": "abcdef12345"},
    ):
        result = process(logger_name="tests.sanitize", level=LogLevel.ERROR, message="hello payload", extra=extra_payload)

    event = ring.snapshot()[0]
    assert result.ok is True
    assert result.event_id == "evt-000001"
    assert set(event.extra) == {"a", "b"}
    assert event.extra["a"].startswith("…")
    assert event.extra["b"]["nested"].startswith("…")
    assert event.exc_info is not None and event.exc_info.startswith("…")
    assert any(name == "extra_total_trimmed" for name, _ in diagnostics)
    assert any(name == "extra_keys_dropped" for name, _ in diagnostics)
    assert any(name == "context_extra_value_truncated" for name, _ in diagnostics)


def test_process_event_handles_duplicate_keys_and_serialization() -> None:
    """Duplicate-like keys and unserialisable values are normalised safely."""
    binder = ContextBinder()
    diagnostics: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=20,
        extra_max_keys=6,
        extra_max_value_chars=15,
        extra_max_depth=1,
        extra_max_total_bytes=200,
        context_max_keys=2,
        context_max_value_chars=15,
        stacktrace_max_frames=5,
    )

    class AliasKey:
        def __init__(self, value: str) -> None:
            self._value = value

        def __hash__(self) -> int:
            return hash((AliasKey, self._value))

        def __eq__(self, other: object) -> bool:
            return isinstance(other, AliasKey) and other._value == self._value

        def __str__(self) -> str:
            return self._value

    alias = AliasKey("dup")

    process, ring, _ = build_process(
        binder=binder,
        backends=(),
        scrubber=RegexScrubber(patterns={}),
        limits=limits,
        diagnostic=diagnostic,
    )

    extra_payload = {
        alias: "first alias entry",
        "dup": "second alias entry requiring truncation",
        1: "alpha",
        "1": "beta extended payload",
        "set": set(range(12)),
        "exc_info": "frame-one-with-extended-details",
    }

    with binder.bind(
        service="svc",
        environment="test",
        job_id="job-dup",
        extra={1: "context value"},
    ):
        result = process(logger_name="tests.duplicate", level=LogLevel.INFO, message="long message payload for truncation", extra=extra_payload)

    event = ring.snapshot()[0]
    assert result.ok is True
    assert result.event_id == "evt-000001"
    assert event.message.endswith("…[truncated]")
    dup_value = event.extra["dup"]
    assert isinstance(dup_value, str)
    assert dup_value.endswith("…[truncated]")
    extra_one = event.extra["1"]
    assert isinstance(extra_one, str)
    assert extra_one.endswith("…[truncated]")
    assert isinstance(event.extra["set"], str)
    assert isinstance(event.exc_info, str)
    assert event.exc_info.endswith("…[truncated]")
    assert "1" in event.context.extra
    assert any(name == "extra_value_truncated" and payload.get("key") == "1" for name, payload in diagnostics)
    assert any(name == "extra_value_truncated" and payload.get("key") == "dup" for name, payload in diagnostics)


def test_process_event_requires_context_binding() -> None:
    """Calling the process pipeline without a context fails fast."""
    binder = ContextBinder()
    process, _, _ = build_process(binder=binder)

    with pytest.raises(RuntimeError):
        process(logger_name="tests.noctx", level=LogLevel.INFO, message="missing context")


def test_process_event_refreshes_process_chain() -> None:
    """Identity refresh trims the PID chain to the configured limit."""
    binder = ContextBinder()
    base_chain = tuple(range(1000, 1000 + CHAIN_LIMIT))
    identity = StaticIdentity(user_name="svc", hostname="updated", process_id=base_chain[-1] + 1)
    process, ring, _ = build_process(binder=binder, identity=identity)

    with binder.bind(
        service="svc",
        environment="test",
        job_id="job-chain",
        process_id=base_chain[-1],
        process_id_chain=base_chain,
    ):
        process(logger_name="tests.chain", level=LogLevel.INFO, message="chain update")

    chain = ring.snapshot()[0].context.process_id_chain or ()
    assert len(chain) == CHAIN_LIMIT
    assert chain[-1] == identity.resolve_identity().process_id


def test_process_event_appends_process_chain_without_trimming() -> None:
    """Process chain expands when below the truncation threshold."""
    binder = ContextBinder()
    base_chain = (2000, 2001)
    identity = StaticIdentity(user_name="svc", hostname="updated", process_id=2002)
    process, ring, _ = build_process(binder=binder, identity=identity)

    with binder.bind(
        service="svc",
        environment="test",
        job_id="job-chain-lite",
        process_id=base_chain[-1],
        process_id_chain=base_chain,
    ):
        process(logger_name="tests.chain-lite", level=LogLevel.INFO, message="chain extend")

    chain = ring.snapshot()[0].context.process_id_chain or ()
    assert chain == (*base_chain, identity.resolve_identity().process_id)


def test_process_event_diagnostic_callback_errors_are_swallowed() -> None:
    """Diagnostic callbacks raising errors do not break the pipeline."""
    binder = ContextBinder()

    def noisy(_name: str, _payload: DiagnosticPayload) -> None:
        raise RuntimeError("diagnostic boom")

    process, _, _ = build_process(binder=binder, diagnostic=noisy)

    with default_context(binder):
        result = process(logger_name="tests.diag", level=LogLevel.INFO, message="still works")

    assert result.ok is True


def test_process_event_sanitizes_without_diagnostic_callback() -> None:
    """Sanitization still runs when no diagnostic hook is configured."""
    binder = ContextBinder()
    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=8,
        extra_max_keys=1,
        extra_max_value_chars=5,
        extra_max_depth=1,
        extra_max_total_bytes=20,
        context_max_keys=1,
        context_max_value_chars=5,
        stacktrace_max_frames=1,
    )
    process, ring, _ = build_process(binder=binder, limits=limits)

    with binder.bind(service="svc", environment="test", job_id="job-nodiag"):
        result = process(logger_name="tests.nodiag", level=LogLevel.INFO, message="truncate me please", extra={"field": "excess"})

    event = ring.snapshot()[0]
    assert result.ok is True
    assert event.message.startswith("truncate") is False


def test_process_event_rate_limiter_drop_records_diagnostic() -> None:
    """Rate limiting short-circuits fan-out and emits a diagnostic."""
    binder = ContextBinder()
    drops: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        drops.append((name, payload))

    limiter = SlidingWindowRateLimiter(max_events=1, interval=timedelta(seconds=60))
    process, ring, monitor = build_process(
        binder=binder,
        rate_limiter=limiter,
        diagnostic=diagnostic,
    )

    class BadExtra:
        def __iter__(self) -> Iterator[int]:
            raise RuntimeError("should not iterate")

    with default_context(binder):
        process(logger_name="tests.limiter", level=LogLevel.INFO, message="ok", extra={"key": "value"})
        second = process(
            logger_name="tests.limiter",
            level=LogLevel.INFO,
            message="second",
            extra=BadExtra(),
        )

    assert second.ok is False
    assert second.reason == "rate_limited"
    assert any(name == "extra_invalid" for name, _ in drops)
    assert monitor.dropped_total() == 1
    assert ring.snapshot()[0].logger_name == "tests.limiter"


def test_process_event_surfaces_adapter_failure() -> None:
    """Adapter errors surface as diagnostics and drop counts."""
    binder = ContextBinder()
    diagnostics: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    class FailingConsole:
        def emit(self, event: LogEvent, *, colorize: bool) -> None:  # noqa: D401, ARG002
            raise RuntimeError("boom")

    process, _, monitor = build_process(
        binder=binder,
        console=FailingConsole(),
        diagnostic=diagnostic,
    )

    with default_context(binder):
        result = process(logger_name="tests.failure", level=LogLevel.INFO, message="adapter boom")

    assert result.reason == "adapter_error"
    assert any(name == "adapter_error" for name, _ in diagnostics)
    assert monitor.dropped_total() == 1


@OS_AGNOSTIC
def test_process_event_enqueue_short_circuits_fan_out() -> None:
    """Queue dispatch marks events as queued without hitting direct adapters."""
    binder = ContextBinder()
    backend = RecordingBackend()
    console_buffer = Console(file=StringIO(), record=True, force_terminal=False)
    queue = QueueAdapter(worker=lambda event: None, drop_policy=QueuePolicy.DROP, maxsize=8)
    queue.start()

    try:
        process, ring, monitor = build_process(
            binder=binder,
            console=RichConsoleAdapter(console=console_buffer, no_color=True),
            backends=(backend,),
            queue=queue,
            colorize_console=False,
        )

        with default_context(binder):
            result = process(logger_name="tests.queue", level=LogLevel.WARNING, message="queued")

        queue.wait_until_idle(timeout=1.0)
        assert result.ok is True
        assert result.event_id == "evt-000001"
        assert result.queued is True
        assert not backend.emitted
        assert not console_buffer.export_text().strip()
        assert monitor.dropped_total() == 0
        assert ring.snapshot()  # event still buffered until worker flushes
    finally:
        queue.stop(drain=True)


@OS_AGNOSTIC
def test_capture_dump_uses_real_adapter_and_flushes_buffer(tmp_path: Path) -> None:
    """Dump use case streams filtered events through the real adapter."""
    checkpoint = tmp_path / "ring_buffer.jsonl"
    ring = RingBuffer(max_events=4, checkpoint_path=checkpoint)
    context = LogContext(service="svc", environment="test", job_id="job", extra={"tenant": "alpha"})
    ring.append(
        LogEvent(
            event_id="evt-1",
            timestamp=datetime(2025, 10, 13, 12, 0, tzinfo=timezone.utc),
            logger_name="tests.dump",
            level=LogLevel.INFO,
            message="keep",
            context=context,
        )
    )
    ring.append(
        LogEvent(
            event_id="evt-2",
            timestamp=datetime(2025, 10, 13, 12, 1, tzinfo=timezone.utc),
            logger_name="tests.dump",
            level=LogLevel.ERROR,
            message="retain",
            context=context,
        )
    )
    adapter = DumpAdapter()
    capture = create_capture_dump(ring_buffer=ring, dump_port=adapter)

    payload = capture(
        dump_format=DumpFormat.TEXT,
        min_level=LogLevel.ERROR,
        format_template=None,
        text_template="{message}",
        dump_filter=None,
        colorize=False,
    )

    assert payload.strip() == "retain"
    written = checkpoint.read_text(encoding="utf-8")
    assert "evt-2" in written and "evt-1" in written


@OS_AGNOSTIC
def test_capture_dump_with_default_template_and_filter(tmp_path: Path) -> None:
    """Default template and dump filters shape the rendered payload."""
    checkpoint = tmp_path / "defaults.jsonl"
    ring = RingBuffer(max_events=4, checkpoint_path=checkpoint)
    context = LogContext(service="svc", environment="test", job_id="job")
    ring.append(
        LogEvent(
            event_id="evt-3",
            timestamp=datetime(2025, 10, 13, 12, 2, tzinfo=timezone.utc),
            logger_name="tests.dump",
            level=LogLevel.WARNING,
            message="match",
            context=context,
            extra={"category": "include"},
        )
    )
    ring.append(
        LogEvent(
            event_id="evt-4",
            timestamp=datetime(2025, 10, 13, 12, 3, tzinfo=timezone.utc),
            logger_name="tests.dump",
            level=LogLevel.WARNING,
            message="skip",
            context=context,
            extra={"category": "exclude"},
        )
    )

    capture = create_capture_dump(
        ring_buffer=ring,
        dump_port=DumpAdapter(),
        default_template="{logger_name}:{message}",
        default_format_preset="full",
        default_theme="night",
        default_console_styles={"WARNING": "yellow"},
    )

    dump_filter = build_dump_filter(extra={"category": "include"})
    payload = capture(
        dump_format=DumpFormat.TEXT,
        min_level=None,
        format_preset=None,
        format_template=None,
        text_template=None,
        theme="day",
        console_styles={"WARNING": "orange"},
        dump_filter=dump_filter,
        colorize=True,
    )

    assert payload.strip() == "tests.dump:match"
    written = checkpoint.read_text(encoding="utf-8")
    assert "evt-3" in written and "evt-4" in written


def test_payload_sanitizer_overwrites_duplicate_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate-like keys replace previous entries with truncated values."""
    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=32,
        extra_max_keys=6,
        extra_max_value_chars=8,
        extra_max_depth=1,
        extra_max_total_bytes=None,
        context_max_keys=2,
        context_max_value_chars=8,
        stacktrace_max_frames=2,
    )
    events: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        events.append((name, payload))

    sanitizer = PayloadSanitizer(limits, diagnostic)

    class AliasKey:
        def __init__(self, value: str) -> None:
            self._value = value

        def __hash__(self) -> int:
            return hash((AliasKey, self._value))

        def __eq__(self, other: object) -> bool:
            return isinstance(other, AliasKey) and other._value == self._value

        def __str__(self) -> str:
            return self._value

    alias = AliasKey("alias")
    raw_mapping: OrderedDict[Any, Any] = OrderedDict(
        [
            (alias, "short"),
            ("alias", "value requiring truncation"),
        ]
    )

    sanitized, exc_info, stack_info = sanitizer.sanitize_extra(
        cast(Mapping[str, Any], raw_mapping),
        event_id="evt",
        logger_name="tests",
    )
    assert exc_info is None
    assert stack_info is None
    alias_value = sanitized["alias"]
    assert isinstance(alias_value, str) and alias_value.startswith("…")
    assert any(name == "extra_value_truncated" and payload.get("key") == "alias" for name, payload in events)

    class FailJson:
        def __str__(self) -> str:
            return "json-fallback"

    from lib_log_rich.application.use_cases._payload_sanitizer import (
        get_shared_encoder,
        set_shared_encoder,
    )

    original_encoder = get_shared_encoder()

    class FailingEncoder:
        """Encoder that fails on FailJson objects."""

        def encode(self, obj: Any) -> str:
            if isinstance(obj, FailJson):
                raise TypeError("fail")
            if isinstance(obj, dict):
                obj_dict = cast(dict[Any, Any], obj)
                if any(isinstance(item, FailJson) for item in obj_dict.values()):
                    raise TypeError("fail")
            return original_encoder.encode(obj)

    try:
        set_shared_encoder(cast(Any, FailingEncoder()))
        wide_limits = PayloadLimits(
            truncate_message=True,
            message_max_chars=32,
            extra_max_keys=6,
            extra_max_value_chars=5,
            extra_max_depth=1,
            extra_max_total_bytes=None,
            context_max_keys=2,
            context_max_value_chars=32,
            stacktrace_max_frames=2,
        )
        fallback_sanitizer = PayloadSanitizer(wide_limits, None)
        sanitized_json, _, stack_info_json = fallback_sanitizer.sanitize_extra(
            {"payload": FailJson()},
            event_id="evt",
            logger_name="tests",
        )
        payload_value = sanitized_json["payload"]
        assert isinstance(payload_value, str)
        assert payload_value.startswith("…")
        assert stack_info_json is None
    finally:
        set_shared_encoder(original_encoder)


def test_payload_sanitizer_reports_depth_collapsed() -> None:
    """Nested mappings beyond depth limits emit depth-collapsed diagnostics."""
    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=32,
        extra_max_keys=5,
        extra_max_value_chars=6,
        extra_max_depth=1,
        extra_max_total_bytes=None,
        context_max_keys=2,
        context_max_value_chars=6,
        stacktrace_max_frames=2,
    )
    events: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        events.append((name, payload))

    sanitizer = PayloadSanitizer(limits, diagnostic)
    sanitized, _, stack_info_nested = sanitizer.sanitize_extra(
        {"outer": {"inner": {"value": "payload"}}},
        event_id="evt",
        logger_name="tests",
    )

    inner_value = sanitized["outer"]["inner"]
    assert isinstance(inner_value, str) and inner_value.startswith("…")
    assert any(name.endswith("depth_collapsed") for name, _ in events)
    assert stack_info_nested is None


def test_payload_sanitizer_compact_traceback_short() -> None:
    """Short tracebacks below size limits remain untouched."""
    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=32,
        extra_max_keys=3,
        extra_max_value_chars=12,
        extra_max_depth=1,
        extra_max_total_bytes=None,
        context_max_keys=2,
        context_max_value_chars=12,
        stacktrace_max_frames=5,
    )
    sanitizer = PayloadSanitizer(limits, None)
    sanitized_short, exc_info_short, stack_info_short = sanitizer.sanitize_extra(
        {"exc_info": "frame-one"},
        event_id="evt",
        logger_name="tests",
    )
    assert sanitized_short == {}
    assert exc_info_short == "frame-one"
    assert stack_info_short is None


def test_payload_sanitizer_compact_traceback_longer_sequences() -> None:
    """Long tracebacks compact frames and apply truncation."""
    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=32,
        extra_max_keys=3,
        extra_max_value_chars=18,
        extra_max_depth=1,
        extra_max_total_bytes=None,
        context_max_keys=2,
        context_max_value_chars=12,
        stacktrace_max_frames=1,
    )
    events: list[tuple[str, DiagnosticPayload]] = []

    def diagnostic(name: str, payload: DiagnosticPayload) -> None:
        events.append((name, payload))

    sanitizer = PayloadSanitizer(limits, diagnostic)
    trace = "\n".join(f"frame {idx} with detail" for idx in range(6))
    _, exc_info_long, stack_info_long = sanitizer.sanitize_extra(
        {"exc_info": trace},
        event_id="evt",
        logger_name="tests",
    )
    assert isinstance(exc_info_long, str)
    assert "truncated" in exc_info_long and exc_info_long.endswith("…[truncated]")
    assert any(name == "exc_info_truncated" for name, _ in events)
    assert stack_info_long is None


def test_payload_sanitizer_truncate_text_passthrough() -> None:
    """When text fits within the limit the original string is retained."""
    limits = PayloadLimits()
    sanitizer = PayloadSanitizer(limits, None)
    assert sanitizer.sanitize_message("ok", event_id="evt", logger_name="tests") == "ok"


def test_payload_sanitizer_compact_traceback_without_length_truncation() -> None:
    """Compacted tracebacks below the value limit skip the additional truncation."""
    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=32,
        extra_max_keys=3,
        extra_max_value_chars=120,
        extra_max_depth=1,
        extra_max_total_bytes=None,
        context_max_keys=2,
        context_max_value_chars=120,
        stacktrace_max_frames=1,
    )
    sanitizer = PayloadSanitizer(limits, None)
    trace = "\n".join(f"frame {idx} detail" for idx in range(3))
    _, compacted_no_trim, stack_info_trim = sanitizer.sanitize_extra(
        {"exc_info": trace},
        event_id="evt",
        logger_name="tests",
    )
    assert isinstance(compacted_no_trim, str)
    assert compacted_no_trim.endswith("detail")
    assert stack_info_trim is None


def test_payload_sanitizer_diagnose_without_callback() -> None:
    """Sanitizer still truncates when no diagnostic hook is installed."""
    limits = PayloadLimits(
        truncate_message=True,
        message_max_chars=8,
        extra_max_keys=2,
        extra_max_value_chars=8,
        extra_max_depth=1,
        extra_max_total_bytes=None,
        context_max_keys=1,
        context_max_value_chars=8,
        stacktrace_max_frames=1,
    )
    sanitizer = PayloadSanitizer(limits, None)
    assert sanitizer.sanitize_message("truncate please", event_id="evt", logger_name="tests").startswith("…")


@OS_AGNOSTIC
def test_capture_dump_preserves_explicit_preset(tmp_path: Path) -> None:
    """Explicit format presets bypass the default template fallback."""

    class RecordingDump(DumpPort):
        def __init__(self) -> None:
            self.last_args: dict[str, Any] | None = None

        def dump(
            self,
            events: Sequence[LogEvent],
            *,
            dump_format: DumpFormat,
            path: Path | None = None,
            min_level: LogLevel | None = None,
            format_preset: str | None = None,
            format_template: str | None = None,
            text_template: str | None = None,
            theme: str | None = None,
            console_styles: Mapping[str, str] | None = None,
            filters: DumpFilter | None = None,
            colorize: bool = False,
        ) -> str:
            self.last_args = {
                "preset": format_preset,
                "template": format_template if format_template is not None else text_template,
            }
            return f"{len(events)}"  # minimal payload

    ring = RingBuffer(max_events=2)
    context = LogContext(service="svc", environment="test", job_id="job")
    ring.append(
        LogEvent(
            event_id="evt-5",
            timestamp=datetime(2025, 10, 13, 12, 4, tzinfo=timezone.utc),
            logger_name="tests.dump",
            level=LogLevel.INFO,
            message="preset",
            context=context,
        )
    )

    recorder = RecordingDump()
    capture = create_capture_dump(
        ring_buffer=ring,
        dump_port=recorder,
        default_template="{message}",
    )

    payload = capture(
        dump_format=DumpFormat.TEXT,
        format_preset="short",
        format_template=None,
        text_template=None,
        dump_filter=None,
        colorize=False,
    )

    assert payload == "1"
    assert recorder.last_args == {"preset": "short", "template": "{message}"}


def test_shutdown_flushes_adapters_and_stops_queue() -> None:
    """Shutdown sequence stops queue, flushes Graylog, and persists ring buffer."""
    events: list[str] = []

    class RecordingQueue(QueuePort):
        def __init__(self) -> None:
            self.accepted: list[LogEvent] = []

        def start(self) -> None:
            events.append("queue_start")

        def stop(self, *, drain: bool = True, timeout: float | None = None) -> None:  # noqa: D401
            events.append(f"queue_stop:{drain}:{timeout}")

        def put(self, event: LogEvent) -> bool:
            self.accepted.append(event)
            return True

    class RecordingGraylog(GraylogPort):
        def __init__(self) -> None:
            self.emitted: list[LogEvent] = []

        def emit(self, event: LogEvent) -> None:
            self.emitted.append(event)

        async def flush(self) -> None:
            events.append("graylog_flush")

    class RecordingRing(RingBuffer):
        def __init__(self) -> None:
            super().__init__(max_events=4)
            self.flushed = False

        def flush(self) -> None:
            self.flushed = True
            events.append("ring_flush")

    ring = RecordingRing()
    queue = RecordingQueue()
    graylog = RecordingGraylog()
    shutdown = create_shutdown(queue=queue, graylog=graylog, ring_buffer=ring)

    async def _invoke_shutdown(callable_: Callable[[], Awaitable[None]]) -> None:
        await callable_()

    asyncio.run(_invoke_shutdown(shutdown))

    assert events == ["queue_stop:True:None", "graylog_flush", "ring_flush"]
    assert ring.flushed is True
