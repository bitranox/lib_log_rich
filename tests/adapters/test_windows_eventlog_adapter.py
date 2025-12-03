from __future__ import annotations

from typing import Any, Callable, Dict, List, cast

import pytest

from lib_log_rich.adapters.structured.windows_eventlog import WindowsEventLogAdapter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC, WINDOWS_ONLY

pytestmark = [OS_AGNOSTIC]


EventFactory = Callable[[dict[str, object] | None], LogEvent]


@pytest.fixture
def sample_event(event_factory: EventFactory) -> LogEvent:
    return event_factory({"message": "hello", "level": LogLevel.WARNING})


def test_windows_eventlog_adapter_uses_default_mapping(sample_event: LogEvent) -> None:
    recorded: Dict[str, object] = {}

    def _reporter(*, app_name: str, event_id: int, event_type: int, strings: List[str]) -> None:
        recorded.update(
            {
                "app_name": app_name,
                "event_id": event_id,
                "event_type": event_type,
                "strings": list(strings),
            }
        )

    adapter = WindowsEventLogAdapter(reporter=_reporter)
    adapter.emit(sample_event)

    assert recorded["app_name"] == sample_event.context.service
    assert recorded["event_id"] == 2000
    assert recorded["event_type"] == WindowsEventLogAdapter.EVENT_TYPES[LogLevel.WARNING]
    strings_value = recorded.get("strings")
    assert isinstance(strings_value, list)
    strings = cast(List[str], strings_value)
    assert any(sample_event.message in value for value in strings)
    assert any(value.startswith("PROCESS_ID_CHAIN=") for value in strings)


def test_windows_eventlog_adapter_accepts_custom_event_ids(sample_event: LogEvent) -> None:
    recorded: Dict[str, object] = {}

    def _reporter(*, app_name: str, event_id: int, event_type: int, strings: List[str]) -> None:
        recorded["event_id"] = event_id

    adapter = WindowsEventLogAdapter(
        reporter=_reporter,
        event_ids={LogLevel.WARNING: 1234, LogLevel.INFO: 1111, LogLevel.ERROR: 2222, LogLevel.CRITICAL: 3333},
    )
    adapter.emit(sample_event)

    assert recorded["event_id"] == 1234


@WINDOWS_ONLY
def test_windows_eventlog_adapter_with_pywin32(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    evtlog = pytest.importorskip("win32evtlogutil")
    recorded: Dict[str, object] = {}

    def fake_report_event(
        app_name: str,
        event_id: int,
        eventCategory: int,
        eventType: int,
        strings: List[str],
    ) -> None:
        recorded.update(
            {
                "app_name": app_name,
                "event_id": event_id,
                "event_type": eventType,
                "strings": list(strings),
            }
        )

    monkeypatch.setattr(evtlog, "ReportEvent", fake_report_event)

    adapter = WindowsEventLogAdapter()
    adapter.emit(sample_event)

    assert recorded["app_name"] == sample_event.context.service
    assert recorded["strings"]


def test_windows_eventlog_adapter_process_chain_string(sample_event: LogEvent) -> None:
    class DictContext:
        """Mock context with tuple process_id_chain for testing chain formatting."""

        service = "svc"
        environment = "env"
        job_id = "job"
        process_id = 123
        process_id_chain = ("root", "child")  # Tuple that formats to "root>child"
        hostname = None
        request_id = None
        user_id = None
        user_name = None
        trace_id = None
        span_id = None

        def to_dict(self, *, include_none: bool = False) -> dict[str, Any]:  # noqa: ARG002
            return {
                "service": self.service,
                "environment": self.environment,
                "job_id": self.job_id,
                "process_id": self.process_id,
                "process_id_chain": "root>child",
            }

    captured: dict[str, Any] = {}

    def reporter(**payload: Any) -> None:
        captured.update(payload)

    adapter = WindowsEventLogAdapter(reporter=reporter)
    mutated = sample_event.replace()
    object.__setattr__(mutated, "context", DictContext())
    adapter.emit(mutated)
    strings = captured.get("strings", [])
    assert any(line == "PROCESS_ID_CHAIN=root>child" for line in strings)


def test_windows_eventlog_adapter_event_id_fallback(sample_event: LogEvent) -> None:
    recorded: dict[str, Any] = {}

    def reporter(**payload: Any) -> None:
        recorded.update(payload)

    adapter = WindowsEventLogAdapter(reporter=reporter)
    adapter.emit(sample_event.replace(level=LogLevel.DEBUG))
    assert recorded["event_id"] == 1000
    assert recorded["event_type"] == WindowsEventLogAdapter.EVENT_TYPES[LogLevel.DEBUG]
    assert recorded["event_type"] == WindowsEventLogAdapter.EVENT_TYPES[LogLevel.DEBUG]


def test_windows_eventlog_adapter_handles_missing_process_chain(sample_event: LogEvent) -> None:
    captured: dict[str, Any] = {}

    def reporter(**payload: Any) -> None:
        captured.update(payload)

    adapter = WindowsEventLogAdapter(reporter=reporter)
    context = sample_event.context.replace(process_id_chain=())
    adapter.emit(sample_event.replace(context=context))
    strings = captured.get("strings", [])
    assert all("PROCESS_ID_CHAIN" not in line for line in strings)
