from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.application.ports.dump import DumpPort
from lib_log_rich.application.ports.graylog import GraylogPort
from lib_log_rich.application.ports.queue import QueuePort
from lib_log_rich.application.ports.rate_limiter import RateLimiterPort
from lib_log_rich.application.ports.scrubber import ScrubberPort
from lib_log_rich.application.ports.structures import StructuredBackendPort
from lib_log_rich.application.ports.time import ClockPort, IdProvider, UnitOfWork
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def record(self, name: str, **payload) -> None:
        self.calls.append((name, payload))


class _FakeConsole(ConsolePort):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        self.recorder.record("emit", event=event, colorize=colorize)


class _FakeStructured(StructuredBackendPort):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def emit(self, event: LogEvent) -> None:
        self.recorder.record("emit", event=event)


class _FakeGraylog(GraylogPort):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def emit(self, event: LogEvent) -> None:
        self.recorder.record("emit", event=event)

    async def flush(self) -> None:
        self.recorder.record("flush")


class _FakeDump(DumpPort):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def dump(self, events: Sequence[LogEvent], *, dump_format: DumpFormat, path: Path | None = None) -> str:
        payload = "|".join(event.event_id for event in events)
        self.recorder.record("dump", dump_format=dump_format, path=path)
        return payload


class _FakeQueue(QueuePort):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def start(self) -> None:
        self.recorder.record("start")

    def stop(self, *, drain: bool = True) -> None:
        self.recorder.record("stop", drain=drain)

    def put(self, event: LogEvent) -> None:
        self.recorder.record("put", event=event)


class _FakeRateLimiter(RateLimiterPort):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def allow(self, event: LogEvent) -> bool:
        self.recorder.record("allow", event=event)
        return True


class _FakeScrubber(ScrubberPort):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def scrub(self, event: LogEvent) -> LogEvent:
        self.recorder.record("scrub", event=event)
        return event


class _FakeClock(ClockPort):
    def now(self) -> datetime:
        return datetime(2025, 9, 23, tzinfo=timezone.utc)


class _FakeId(IdProvider):
    def __call__(self) -> str:
        return "evt-1"


class _FakeUnitOfWork(UnitOfWork):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def run(self, fn: Callable[[], None]) -> None:
        self.recorder.record("run")
        fn()


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


@pytest.fixture
def example_event(bound_context) -> LogEvent:
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=bound_context,
    )


@pytest.mark.parametrize(
    "factory, protocol",
    [
        (lambda rec: _FakeConsole(rec), ConsolePort),
        (lambda rec: _FakeStructured(rec), StructuredBackendPort),
        (lambda rec: _FakeGraylog(rec), GraylogPort),
        (lambda rec: _FakeDump(rec), DumpPort),
        (lambda rec: _FakeQueue(rec), QueuePort),
        (lambda rec: _FakeRateLimiter(rec), RateLimiterPort),
        (lambda rec: _FakeScrubber(rec), ScrubberPort),
    ],
)
def test_ports_accept_event_instances(
    factory: Callable[[_Recorder], object],
    protocol: type,
    recorder: _Recorder,
    example_event: LogEvent,
) -> None:
    port = factory(recorder)
    assert isinstance(port, protocol)

    if protocol is ConsolePort:
        port.emit(example_event, colorize=True)
    elif protocol is StructuredBackendPort:
        port.emit(example_event)
    elif protocol is GraylogPort:
        port.emit(example_event)
        asyncio.run(port.flush())
    elif protocol is DumpPort:
        payload = port.dump([example_event], dump_format=DumpFormat.TEXT)
        assert payload == "evt-1"
    elif protocol is QueuePort:
        port.start()
        port.put(example_event)
        port.stop()
    elif protocol is RateLimiterPort:
        assert port.allow(example_event)
    elif protocol is ScrubberPort:
        assert port.scrub(example_event) is example_event


def test_clock_and_id_contracts(recorder: _Recorder) -> None:
    clock: ClockPort = _FakeClock()
    ident: IdProvider = _FakeId()
    uow: UnitOfWork = _FakeUnitOfWork(recorder)

    now = clock.now()
    assert now.tzinfo is timezone.utc

    assert ident() == "evt-1"

    called: list[str] = []

    def _fn() -> None:
        called.append("ran")

    uow.run(_fn)
    assert called == ["ran"]
    assert recorder.calls == [("run", {})]
