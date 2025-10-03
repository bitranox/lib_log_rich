from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from lib_log_rich.application.use_cases.process_event import create_process_log_event
from lib_log_rich.application.use_cases.dump import create_capture_dump
from lib_log_rich.application.use_cases.shutdown import create_shutdown
from lib_log_rich.domain import ContextBinder, DumpFilter, LogContext, LogEvent, LogLevel, RingBuffer
from lib_log_rich.domain.dump import DumpFormat


Payload = dict[str, Any]
CallRecord = tuple[str, Payload]


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    def record(self, name: str, **payload: Any) -> None:
        self.calls.append((name, dict(payload)))


class _FakeConsole:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        self.recorder.record("console", event_id=event.event_id, colorize=colorize)


class _FakeBackend:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def emit(self, event: LogEvent) -> None:
        self.recorder.record("backend", event_id=event.event_id)


class _FakeGraylog:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def emit(self, event: LogEvent) -> None:
        self.recorder.record("graylog", event_id=event.event_id)

    async def flush(self) -> None:
        self.recorder.record("graylog_flush")


class _FakeScrubber:
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def scrub(self, event: LogEvent) -> LogEvent:
        self.recorder.record("scrub", event_id=event.event_id)
        return event


class _FakeRateLimiter:
    def __init__(self, allowed: bool = True, recorder: _Recorder | None = None) -> None:
        self.allowed = allowed
        self.recorder = recorder

    def allow(self, event: LogEvent) -> bool:
        if self.recorder:
            self.recorder.record("rate", event_id=event.event_id)
        return self.allowed


class _FakeQueue:
    def __init__(self, recorder: _Recorder, *, accept: bool = True) -> None:
        self.recorder = recorder
        self._accept = accept

    def start(self) -> None:
        self.recorder.record("queue_start")

    def stop(self, *, drain: bool = True, timeout: float | None = 5.0) -> None:
        self.recorder.record("queue_stop", drain=drain, timeout=timeout)

    def put(self, event: LogEvent) -> bool:
        self.recorder.record("queue_put", event_id=event.event_id)
        return self._accept


class _FakeClock:
    def now(self) -> datetime:
        return datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc)


class _FakeId:
    def __init__(self) -> None:
        self.counter = 0

    def __call__(self) -> str:
        self.counter += 1
        return f"evt-{self.counter:03d}"


@pytest.fixture
def binder() -> ContextBinder:
    return ContextBinder()


@pytest.fixture
def ring_buffer() -> RingBuffer:
    return RingBuffer(max_events=10)


def test_process_log_event_fans_out_when_allowed(binder: ContextBinder, ring_buffer: RingBuffer) -> None:
    recorder = _Recorder()
    console = _FakeConsole(recorder)
    backend = _FakeBackend(recorder)
    graylog = _FakeGraylog(recorder)
    scrubber = _FakeScrubber(recorder)
    limiter = _FakeRateLimiter(recorder=recorder)
    clock = _FakeClock()
    ids = _FakeId()
    diagnostics: list[CallRecord] = []

    def diagnostic(name: str, payload: Payload) -> None:
        diagnostics.append((name, payload))

    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="test", job_id="job-1").to_dict(include_none=True)]})

    process_callable = create_process_log_event(
        context_binder=binder,
        ring_buffer=ring_buffer,
        console=console,
        console_level=LogLevel.DEBUG,
        structured_backends=[backend],
        backend_level=LogLevel.INFO,
        graylog=graylog,
        graylog_level=LogLevel.INFO,
        scrubber=scrubber,
        rate_limiter=limiter,
        clock=clock,
        id_provider=ids,
        queue=None,
        diagnostic=diagnostic,
    )

    result: dict[str, Any] = process_callable(
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        extra={"foo": "bar"},
    )

    assert result["ok"] is True
    assert result["event_id"] == "evt-001"
    assert len(ring_buffer.snapshot()) == 1
    assert recorder.calls[0][0] == "scrub"
    assert any(name == "console" for name, _ in recorder.calls)
    assert any(name == "backend" for name, _ in recorder.calls)
    assert any(name == "graylog" for name, _ in recorder.calls)
    assert any(name == "emitted" for name, _ in diagnostics)


def test_process_log_event_drops_when_rate_limited(binder: ContextBinder, ring_buffer: RingBuffer) -> None:
    recorder = _Recorder()
    console = _FakeConsole(recorder)
    limiter = _FakeRateLimiter(allowed=False)
    diagnostics: list[CallRecord] = []

    def diagnostic(name: str, payload: Payload) -> None:
        diagnostics.append((name, payload))

    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="test", job_id="job-1").to_dict(include_none=True)]})

    process_callable = create_process_log_event(
        context_binder=binder,
        ring_buffer=ring_buffer,
        console=console,
        console_level=LogLevel.DEBUG,
        structured_backends=[],
        backend_level=LogLevel.INFO,
        graylog=None,
        graylog_level=LogLevel.ERROR,
        scrubber=_FakeScrubber(recorder),
        rate_limiter=limiter,
        clock=_FakeClock(),
        id_provider=_FakeId(),
        queue=None,
        diagnostic=diagnostic,
    )

    result = process_callable(logger_name="tests", level=LogLevel.INFO, message="hello")
    assert result == {"ok": False, "reason": "rate_limited"}
    assert recorder.calls == [("scrub", {"event_id": "evt-001"})]
    assert ring_buffer.snapshot() == []
    assert any(name == "rate_limited" for name, _ in diagnostics)


def test_process_log_event_reports_adapter_failure(
    binder: ContextBinder,
    ring_buffer: RingBuffer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    diagnostics: list[CallRecord] = []

    class _BoomConsole:
        def emit(self, event: LogEvent, *, colorize: bool) -> None:  # noqa: D401, ARG002
            raise RuntimeError("console boom")

    caplog.set_level(logging.ERROR)
    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="test", job_id="job-1").to_dict(include_none=True)]})

    process_callable = create_process_log_event(
        context_binder=binder,
        ring_buffer=ring_buffer,
        console=_BoomConsole(),
        console_level=LogLevel.DEBUG,
        structured_backends=[],
        backend_level=LogLevel.INFO,
        graylog=None,
        graylog_level=LogLevel.ERROR,
        scrubber=_FakeScrubber(_Recorder()),
        rate_limiter=_FakeRateLimiter(),
        clock=_FakeClock(),
        id_provider=_FakeId(),
        queue=None,
        diagnostic=lambda name, payload: diagnostics.append((name, payload)),
    )

    result = process_callable(logger_name="tests", level=LogLevel.INFO, message="boom")

    assert result["ok"] is False
    assert result["reason"] == "adapter_error"
    assert result["failed_adapters"] == ["_BoomConsole"]
    assert ring_buffer.snapshot(), "Event should remain recorded despite adapter failure"
    assert any("console boom" in record.message for record in caplog.records)
    assert any(name == "adapter_error" for name, _ in diagnostics)


def test_process_log_event_reports_queue_full(binder: ContextBinder, ring_buffer: RingBuffer) -> None:
    recorder = _Recorder()
    queue = _FakeQueue(recorder, accept=False)
    diagnostics: list[CallRecord] = []

    def diagnostic(name: str, payload: Payload) -> None:
        diagnostics.append((name, payload))

    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="test", job_id="job-1").to_dict(include_none=True)]})

    process_callable = create_process_log_event(
        context_binder=binder,
        ring_buffer=ring_buffer,
        console=_FakeConsole(recorder),
        console_level=LogLevel.DEBUG,
        structured_backends=[_FakeBackend(recorder)],
        backend_level=LogLevel.INFO,
        graylog=_FakeGraylog(recorder),
        graylog_level=LogLevel.INFO,
        scrubber=_FakeScrubber(recorder),
        rate_limiter=_FakeRateLimiter(),
        clock=_FakeClock(),
        id_provider=_FakeId(),
        queue=queue,
        diagnostic=diagnostic,
    )

    result = process_callable(logger_name="tests", level=LogLevel.INFO, message="overflow")
    assert result == {"ok": False, "reason": "queue_full"}
    assert any(name == "queue_full" for name, _ in diagnostics)
    assert any(name == "queue_put" for name, _ in recorder.calls)


def test_process_log_event_uses_queue_when_available(binder: ContextBinder, ring_buffer: RingBuffer) -> None:
    recorder = _Recorder()
    queue = _FakeQueue(recorder)
    diagnostics_queue: list[CallRecord] = []

    def diagnostic_queue(name: str, payload: Payload) -> None:
        diagnostics_queue.append((name, payload))

    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="test", job_id="job-1").to_dict(include_none=True)]})

    process_callable = create_process_log_event(
        context_binder=binder,
        ring_buffer=ring_buffer,
        console=_FakeConsole(recorder),
        console_level=LogLevel.DEBUG,
        structured_backends=[_FakeBackend(recorder)],
        backend_level=LogLevel.INFO,
        graylog=_FakeGraylog(recorder),
        graylog_level=LogLevel.INFO,
        scrubber=_FakeScrubber(recorder),
        rate_limiter=_FakeRateLimiter(),
        clock=_FakeClock(),
        id_provider=_FakeId(),
        queue=queue,
        diagnostic=diagnostic_queue,
    )

    result = process_callable(logger_name="tests", level=LogLevel.WARNING, message="queued")
    assert result["ok"] is True
    assert any(name == "queue_put" for name, _ in recorder.calls)
    assert not any(name in {"console", "backend", "graylog"} for name, _ in recorder.calls)
    assert any(name == "queued" for name, _ in diagnostics_queue)


def test_process_log_event_reports_adapter_errors(binder: ContextBinder, ring_buffer: RingBuffer) -> None:
    recorder = _Recorder()
    diagnostics_backend: list[CallRecord] = []

    class RaisingBackend:
        def emit(self, event: LogEvent) -> None:  # noqa: D401, ANN001
            raise RuntimeError("backend failure")

    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="env", job_id="job-1").to_dict(include_none=True)]})

    def diagnostic_backend(name: str, payload: Payload) -> None:
        diagnostics_backend.append((name, payload))

    process_callable = create_process_log_event(
        context_binder=binder,
        ring_buffer=ring_buffer,
        console=_FakeConsole(recorder),
        console_level=LogLevel.DEBUG,
        structured_backends=[RaisingBackend()],
        backend_level=LogLevel.INFO,
        graylog=None,
        graylog_level=LogLevel.ERROR,
        scrubber=_FakeScrubber(recorder),
        rate_limiter=_FakeRateLimiter(),
        clock=_FakeClock(),
        id_provider=_FakeId(),
        queue=None,
        diagnostic=diagnostic_backend,
    )

    result = process_callable(logger_name="tests", level=LogLevel.ERROR, message="boom")

    assert result == {
        "ok": False,
        "reason": "adapter_error",
        "event_id": "evt-001",
        "failed_adapters": ["RaisingBackend"],
    }
    assert ring_buffer.snapshot(), "Event should remain recorded after adapter failure"
    assert any(name == "adapter_error" for name, _ in diagnostics_backend)


def test_capture_dump_uses_dump_port(ring_buffer: RingBuffer) -> None:
    recorder = _Recorder()

    class _DumpAdapter:
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
            recorder.record(
                "dump",
                dump_format=dump_format,
                path=path,
                count=len(events),
                min_level=min_level,
                format_preset=format_preset,
                format_template=format_template,
                theme=theme,
                console_styles=console_styles,
                filters=filters,
                colorize=colorize,
            )
            return "payload"

    ring_buffer.append(
        LogEvent(
            event_id="evt-1",
            timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
            logger_name="tests",
            level=LogLevel.INFO,
            message="hello",
            context=LogContext(service="svc", environment="test", job_id="job"),
        )
    )

    capture_callable = create_capture_dump(ring_buffer=ring_buffer, dump_port=_DumpAdapter())
    payload: str = capture_callable(
        dump_format=DumpFormat.JSON,
        min_level=LogLevel.INFO,
        format_template="template",
        colorize=True,
    )
    assert payload == "payload"
    assert recorder.calls == [
        (
            "dump",
            {
                "dump_format": DumpFormat.JSON,
                "path": None,
                "count": 1,
                "min_level": LogLevel.INFO,
                "format_preset": None,
                "format_template": "template",
                "theme": None,
                "console_styles": None,
                "filters": None,
                "colorize": True,
            },
        )
    ]


def test_shutdown_flushes_adapters_and_stops_queue() -> None:
    recorder = _Recorder()
    queue = _FakeQueue(recorder)
    graylog = _FakeGraylog(recorder)

    shutdown = create_shutdown(queue=queue, graylog=graylog, ring_buffer=None)
    asyncio.run(shutdown())  # type: ignore[arg-type]

    assert ("queue_stop", {"drain": True, "timeout": 5.0}) in recorder.calls
    assert ("graylog_flush", {}) in recorder.calls
