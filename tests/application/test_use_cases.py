from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from lib_log_rich.application.use_cases.process_event import create_process_log_event
from lib_log_rich.application.use_cases.dump import create_capture_dump
from lib_log_rich.application.use_cases.shutdown import create_shutdown
from lib_log_rich.domain import ContextBinder, LogContext, LogEvent, LogLevel, RingBuffer
from lib_log_rich.domain.dump import DumpFormat


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def record(self, name: str, **payload) -> None:
        self.calls.append((name, payload))


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
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def start(self) -> None:
        self.recorder.record("queue_start")

    def stop(self, *, drain: bool = True) -> None:
        self.recorder.record("queue_stop", drain=drain)

    def put(self, event: LogEvent) -> None:
        self.recorder.record("queue_put", event_id=event.event_id)


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
    diagnostics: list[tuple[str, dict]] = []

    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="test", job_id="job-1").to_dict(include_none=True)]})

    process = create_process_log_event(
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
        diagnostic=lambda n, p: diagnostics.append((n, p)),
    )

    result = process(logger_name="tests", level=LogLevel.INFO, message="hello", extra={"foo": "bar"})

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
    diagnostics: list[tuple[str, dict]] = []
    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="test", job_id="job-1").to_dict(include_none=True)]})

    process = create_process_log_event(
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
        diagnostic=lambda n, p: diagnostics.append((n, p)),
    )

    result = process(logger_name="tests", level=LogLevel.INFO, message="hello")
    assert result == {"ok": False, "reason": "rate_limited"}
    assert recorder.calls == [("scrub", {"event_id": "evt-001"})]
    assert ring_buffer.snapshot() == []
    assert any(name == "rate_limited" for name, _ in diagnostics)


def test_process_log_event_uses_queue_when_available(binder: ContextBinder, ring_buffer: RingBuffer) -> None:
    recorder = _Recorder()
    queue = _FakeQueue(recorder)
    diagnostics: list[tuple[str, dict]] = []
    binder.deserialize({"version": 1, "stack": [LogContext(service="svc", environment="test", job_id="job-1").to_dict(include_none=True)]})

    process = create_process_log_event(
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
        diagnostic=lambda n, p: diagnostics.append((n, p)),
    )

    result = process(logger_name="tests", level=LogLevel.WARNING, message="queued")
    assert result["ok"] is True
    assert any(name == "queue_put" for name, _ in recorder.calls)
    assert not any(name in {"console", "backend", "graylog"} for name, _ in recorder.calls)
    assert any(name == "queued" for name, _ in diagnostics)


def test_capture_dump_uses_dump_port(ring_buffer: RingBuffer) -> None:
    recorder = _Recorder()

    class _DumpAdapter:
        def dump(
            self,
            events,
            *,
            dump_format: DumpFormat,
            path=None,
            min_level=None,
            format_preset=None,
            format_template=None,
            text_template=None,
            theme=None,
            console_styles=None,
            colorize=False,
        ) -> str:  # type: ignore[override]
            recorder.record(
                "dump",
                dump_format=dump_format,
                path=path,
                count=len(events),
                min_level=min_level,
                format_preset=format_preset,
                format_template=format_template,
                text_template=text_template,
                theme=theme,
                console_styles=console_styles,
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

    capture = create_capture_dump(ring_buffer=ring_buffer, dump_port=_DumpAdapter())
    result = capture(
        dump_format=DumpFormat.JSON,
        min_level=LogLevel.INFO,
        format_template="template",
        colorize=True,
    )
    assert result == "payload"
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
                "text_template": "template",
                "theme": None,
                "console_styles": None,
                "colorize": True,
            },
        )
    ]


def test_shutdown_flushes_adapters_and_stops_queue() -> None:
    recorder = _Recorder()
    queue = _FakeQueue(recorder)
    graylog = _FakeGraylog(recorder)

    shutdown = create_shutdown(queue=queue, graylog=graylog, ring_buffer=None)
    asyncio.run(shutdown())

    assert ("queue_stop", {"drain": True}) in recorder.calls
    assert ("graylog_flush", {}) in recorder.calls
