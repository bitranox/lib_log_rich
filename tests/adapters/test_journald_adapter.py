from __future__ import annotations

import pytest

from lib_log_rich.adapters.structured.journald import JournaldAdapter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import LINUX_ONLY, OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


@pytest.fixture
def sample_event(event_factory) -> LogEvent:
    return event_factory({"extra": {"error_code": "E100"}})


def test_journald_adapter_emits_uppercase_fields(sample_event: LogEvent) -> None:
    recorded: dict[str, object] = {}

    def _sender(**fields) -> None:
        recorded.update(fields)

    adapter = JournaldAdapter(sender=_sender)
    adapter.emit(sample_event)

    assert recorded["MESSAGE"] == sample_event.message
    assert recorded["PRIORITY"] == 6
    assert recorded["JOB_ID"] == sample_event.context.job_id
    assert recorded["LOGGER_NAME"] == sample_event.logger_name
    assert recorded["ERROR_CODE"] == "E100"


def test_journald_adapter_allows_custom_field_prefix(sample_event: LogEvent) -> None:
    recorded: dict[str, object] = {}

    def _sender(**fields) -> None:
        recorded.update(fields)

    adapter = JournaldAdapter(sender=_sender, service_field="UNIT")
    adapter.emit(sample_event)

    assert recorded["UNIT"] == sample_event.context.service
    assert "PROCESS_ID_CHAIN" in recorded


def test_journald_adapter_extra_does_not_override_core(sample_event: LogEvent) -> None:
    recorded: dict[str, object] = {}

    def _sender(**fields) -> None:
        recorded.update(fields)

    adapter = JournaldAdapter(sender=_sender)
    noisy_event = sample_event.replace(extra={"message": "spoof", "priority": 0})
    adapter.emit(noisy_event)

    assert recorded["MESSAGE"] == sample_event.message
    assert recorded["PRIORITY"] == 6
    assert recorded["EXTRA_MESSAGE"] == "spoof"
    assert recorded["EXTRA_PRIORITY"] == 0


def test_journald_adapter_translates_levels(sample_event: LogEvent) -> None:
    recorded: dict[str, object] = {}

    def _sender(**fields) -> None:
        recorded.update(fields)

    adapter = JournaldAdapter(sender=_sender)
    adapter.emit(sample_event.replace(level=LogLevel.ERROR))

    assert recorded["PRIORITY"] == 3


@LINUX_ONLY
def test_journald_adapter_with_systemd(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    journal = pytest.importorskip("systemd.journal")
    captured: dict[str, object] = {}

    def fake_send(**fields) -> None:
        captured.update(fields)

    monkeypatch.setattr(journal, "send", fake_send)

    adapter = JournaldAdapter()
    adapter.emit(sample_event)

    assert captured["MESSAGE"] == sample_event.message
    assert captured["PRIORITY"] == 6
