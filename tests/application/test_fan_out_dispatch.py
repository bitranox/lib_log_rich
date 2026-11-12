from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from lib_log_rich.application.ports import ConsolePort, GraylogPort, QueuePort, StructuredBackendPort
from lib_log_rich.application.use_cases._fan_out import build_fan_out_handlers
from lib_log_rich.application.use_cases._queue_dispatch import build_queue_dispatcher
from lib_log_rich.domain import DiagnosticEvent, DropReason, LogEvent, LogLevel
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from lib_log_rich.application.use_cases._types import DiagnosticPayload

pytestmark = [OS_AGNOSTIC]


class MemoryConsole(ConsolePort):
    def __init__(self) -> None:
        self.events: list[LogEvent] = []
        self.colorized: list[bool] = []

    def emit(self, event: LogEvent, *, colorize: bool) -> None:
        self.events.append(event)
        self.colorized.append(colorize)

    def flush(self) -> None:
        pass


class MemoryBackend(StructuredBackendPort):
    def __init__(self) -> None:
        self.events: list[LogEvent] = []

    def emit(self, event: LogEvent) -> None:
        self.events.append(event)


class MemoryGraylog(GraylogPort):
    def __init__(self) -> None:
        self.events: list[LogEvent] = []

    def emit(self, event: LogEvent) -> None:
        self.events.append(event)

    async def flush(self) -> None:  # pragma: no cover - noop flush for protocol parity
        return None


class RejectingQueue(QueuePort):
    def __init__(self, *, accept: bool) -> None:
        self.accept = accept
        self.events: list[LogEvent] = []

    def start(self) -> None:  # pragma: no cover - queue protocol stub
        return None

    def stop(self, *, drain: bool = True, timeout: float | None = 5.0) -> None:  # pragma: no cover - queue protocol stub
        return None

    def put(self, event: LogEvent) -> bool:
        self.events.append(event)
        return self.accept

    def wait_until_idle(self, timeout: float | None = None) -> bool:  # pragma: no cover - queue protocol stub
        return True


def test_fan_out_emits_console_backend_and_graylog(event_factory: Callable[[dict[str, Any] | None], LogEvent]) -> None:
    console = MemoryConsole()
    backend = MemoryBackend()
    graylog = MemoryGraylog()
    diagnostics: list[tuple[DiagnosticEvent, DiagnosticPayload]] = []
    logger = logging.getLogger("tests.fan_out")
    logger.addHandler(logging.NullHandler())

    def record(name: DiagnosticEvent, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    _, finalize = build_fan_out_handlers(
        console=console,
        console_level=LogLevel.DEBUG,
        structured_backends=[backend],
        backend_level=LogLevel.INFO,
        graylog=graylog,
        graylog_level=LogLevel.WARNING,
        emit=record,
        colorize_console=True,
        logger=logger,
    )

    event = event_factory({"level": LogLevel.ERROR})
    result = finalize(event)
    assert result.ok is True
    assert result.event_id == event.event_id
    assert len(console.events) == 1
    assert len(backend.events) == 1
    assert len(graylog.events) == 1
    assert diagnostics[-1][0] is DiagnosticEvent.EMITTED


def test_fan_out_respects_dynamic_console_level(event_factory: Callable[[dict[str, Any] | None], LogEvent]) -> None:
    console = MemoryConsole()
    diagnostics: list[tuple[DiagnosticEvent, DiagnosticPayload]] = []
    logger = logging.getLogger("tests.fan_out.dynamic")
    logger.addHandler(logging.NullHandler())

    def record(name: DiagnosticEvent, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    fan_out_error, _ = build_fan_out_handlers(
        console=console,
        console_level=LogLevel.ERROR,
        structured_backends=[],
        backend_level=LogLevel.INFO,
        graylog=None,
        graylog_level=LogLevel.WARNING,
        emit=record,
        colorize_console=False,
        logger=logger,
    )

    warning_event = event_factory({"level": LogLevel.WARNING})
    fan_out_error(warning_event)
    assert console.events == []

    fan_out_debug, _ = build_fan_out_handlers(
        console=console,
        console_level=LogLevel.DEBUG,
        structured_backends=[],
        backend_level=LogLevel.INFO,
        graylog=None,
        graylog_level=LogLevel.WARNING,
        emit=record,
        colorize_console=False,
        logger=logger,
    )

    fan_out_debug(event_factory({"level": LogLevel.WARNING}))
    assert len(console.events) == 1


def test_queue_dispatch_reports_queue_full(event_factory: Callable[[dict[str, Any] | None], LogEvent]) -> None:
    queue = RejectingQueue(accept=False)
    diagnostics: list[tuple[DiagnosticEvent, DiagnosticPayload]] = []

    def record(name: DiagnosticEvent, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    dispatch = build_queue_dispatcher(queue, record)
    event = event_factory(None)
    result = dispatch(event)
    assert result is not None
    assert result.ok is False
    assert result.reason is DropReason.QUEUE_FULL
    assert diagnostics[0][0] is DiagnosticEvent.QUEUE_FULL
    assert diagnostics[0][1]["event_id"] == event.event_id


def test_queue_dispatch_reports_success(event_factory: Callable[[dict[str, Any] | None], LogEvent]) -> None:
    queue = RejectingQueue(accept=True)
    diagnostics: list[tuple[DiagnosticEvent, DiagnosticPayload]] = []

    def record(name: DiagnosticEvent, payload: DiagnosticPayload) -> None:
        diagnostics.append((name, payload))

    dispatch = build_queue_dispatcher(queue, record)
    event = event_factory(None)
    result = dispatch(event)
    assert result is not None
    assert result.ok is True
    assert result.event_id == event.event_id
    assert result.queued is True
    assert diagnostics[0][0] is DiagnosticEvent.QUEUED
    assert diagnostics[0][1]["event_id"] == event.event_id


class _RaisingGraylog(GraylogPort):
    def __init__(self, error: Exception) -> None:
        self._error = error

    def emit(self, event: LogEvent) -> None:
        raise self._error

    async def flush(self) -> None:  # pragma: no cover - noop flush for protocol parity
        return None


def _fan_out_with_failing_graylog(error: Exception, logger: logging.Logger) -> Callable[[LogEvent], list[str]]:
    fan_out, _ = build_fan_out_handlers(
        console=MemoryConsole(),
        console_level=LogLevel.CRITICAL,
        structured_backends=[],
        backend_level=LogLevel.CRITICAL,
        graylog=_RaisingGraylog(error),
        graylog_level=LogLevel.DEBUG,
        emit=lambda _name, _payload: None,
        colorize_console=False,
        logger=logger,
    )
    return fan_out


def test_fan_out_logs_a_transport_failure_as_one_line_without_traceback(
    event_factory: Callable[[dict[str, Any] | None], LogEvent], caplog: pytest.LogCaptureFixture
) -> None:
    # An unreachable sink is an operational fault, not a bug: a traceback per
    # event buried the console the user was reading.
    logger = logging.getLogger("tests.fan_out.transport")
    fan_out = _fan_out_with_failing_graylog(TimeoutError("timed out"), logger)

    with caplog.at_level(logging.ERROR, logger=logger.name):
        failed = fan_out(event_factory({"level": LogLevel.ERROR}))

    assert failed == ["_RaisingGraylog"]
    (record,) = caplog.records
    assert "timed out" in record.getMessage()
    assert not record.exc_info


def test_fan_out_keeps_the_traceback_for_a_programming_error(
    event_factory: Callable[[dict[str, Any] | None], LogEvent], caplog: pytest.LogCaptureFixture
) -> None:
    logger = logging.getLogger("tests.fan_out.bug")
    fan_out = _fan_out_with_failing_graylog(ValueError("bad payload"), logger)

    with caplog.at_level(logging.ERROR, logger=logger.name):
        fan_out(event_factory({"level": LogLevel.ERROR}))

    (record,) = caplog.records
    assert record.exc_info
    assert record.exc_info[0] is ValueError
