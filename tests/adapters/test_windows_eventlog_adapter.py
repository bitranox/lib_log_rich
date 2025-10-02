from __future__ import annotations

from typing import Callable, Dict, List, cast

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
