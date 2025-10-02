from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

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
from lib_log_rich.domain.dump_filter import DumpFilter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.context import LogContext


Payload = dict[str, object]
CallRecord = tuple[str, Payload]


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    def record(self, name: str, **payload: object) -> None:
        self.calls.append((name, dict(payload)))


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
        payload = "|".join(event.event_id for event in events)
        self.recorder.record(
            "dump",
            dump_format=dump_format,
            path=path,
            min_level=min_level,
            format_preset=format_preset,
            format_template=format_template,
            text_template=text_template,
            theme=theme,
            console_styles=console_styles,
            filters=filters,
            colorize=colorize,
        )
        return payload


class _FakeQueue(QueuePort):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def start(self) -> None:
        self.recorder.record("start")

    def stop(self, *, drain: bool = True, timeout: float | None = 5.0) -> None:
        self.recorder.record("stop", drain=drain, timeout=timeout)

    def put(self, event: LogEvent) -> bool:
        self.recorder.record("put", event=event)
        return True


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


class _FakeUnitOfWork(UnitOfWork[str]):
    def __init__(self, recorder: _Recorder) -> None:
        self.recorder = recorder

    def run(self, fn: Callable[[], str]) -> str:
        self.recorder.record("run")
        return fn()


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


@pytest.fixture
def example_event(bound_context: LogContext) -> LogEvent:
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=bound_context,
    )


Factory = Callable[[_Recorder], object]
PortType = type[ConsolePort] | type[StructuredBackendPort] | type[GraylogPort] | type[DumpPort] | type[QueuePort] | type[RateLimiterPort] | type[ScrubberPort]


def _make_console(rec: _Recorder) -> ConsolePort:
    return _FakeConsole(rec)


def _make_structured(rec: _Recorder) -> StructuredBackendPort:
    return _FakeStructured(rec)


def _make_graylog(rec: _Recorder) -> GraylogPort:
    return _FakeGraylog(rec)


def _make_dump(rec: _Recorder) -> DumpPort:
    return _FakeDump(rec)


def _make_queue(rec: _Recorder) -> QueuePort:
    return _FakeQueue(rec)


def _make_rate_limiter(rec: _Recorder) -> RateLimiterPort:
    return _FakeRateLimiter(rec)


def _make_scrubber(rec: _Recorder) -> ScrubberPort:
    return _FakeScrubber(rec)


@pytest.mark.parametrize(
    "factory, protocol",
    [
        (_make_console, ConsolePort),
        (_make_structured, StructuredBackendPort),
        (_make_graylog, GraylogPort),
        (_make_dump, DumpPort),
        (_make_queue, QueuePort),
        (_make_rate_limiter, RateLimiterPort),
        (_make_scrubber, ScrubberPort),
    ],
)
def test_ports_accept_event_instances(
    factory: Factory,
    protocol: PortType,
    recorder: _Recorder,
    example_event: LogEvent,
) -> None:
    port = factory(recorder)
    assert isinstance(port, protocol)

    if protocol is ConsolePort:
        console = cast(ConsolePort, port)
        console.emit(example_event, colorize=True)
    elif protocol is StructuredBackendPort:
        backend = cast(StructuredBackendPort, port)
        backend.emit(example_event)
    elif protocol is GraylogPort:
        graylog = cast(GraylogPort, port)
        graylog.emit(example_event)
        asyncio.run(graylog.flush())
    elif protocol is DumpPort:
        dump_port = cast(DumpPort, port)
        payload = dump_port.dump([example_event], dump_format=DumpFormat.TEXT)
        assert payload == "evt-1"
    elif protocol is QueuePort:
        queue = cast(QueuePort, port)
        queue.start()
        queue.put(example_event)
        queue.stop()
    elif protocol is RateLimiterPort:
        limiter = cast(RateLimiterPort, port)
        assert limiter.allow(example_event)
    elif protocol is ScrubberPort:
        scrubber = cast(ScrubberPort, port)
        assert scrubber.scrub(example_event) is example_event


def test_clock_and_id_contracts(recorder: _Recorder) -> None:
    clock: ClockPort = _FakeClock()
    ident: IdProvider = _FakeId()
    uow: UnitOfWork[str] = _FakeUnitOfWork(recorder)

    now = clock.now()
    assert now.tzinfo is timezone.utc

    assert ident() == "evt-1"

    called: list[str] = []

    def _fn() -> str:
        called.append("ran")
        return "ran"

    result = uow.run(_fn)
    assert called == ["ran"]
    assert result == "ran"
    assert recorder.calls == [("run", {})]
