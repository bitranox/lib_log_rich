from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def build_event(*, context: LogContext, **overrides: object) -> LogEvent:
    """Create a log event with sensible defaults."""
    defaults: dict[str, object] = {
        "event_id": "evt-1",
        "timestamp": datetime(2025, 9, 23, 11, 0, 0, tzinfo=timezone.utc),
        "logger_name": "tests",
        "level": LogLevel.INFO,
        "message": "hello",
        "context": context,
    }
    defaults.update(overrides)
    return LogEvent(**defaults)  # type: ignore[arg-type]


def test_log_event_rejects_naive_timestamp(bound_context: LogContext) -> None:
    """A naive timestamp raises a timezone error."""
    naive = datetime(2025, 9, 23, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        build_event(context=bound_context, timestamp=naive)


def test_log_event_accepts_timezone_aware_timestamp(bound_context: LogContext) -> None:
    """A UTC timestamp survives intact."""
    aware = datetime(2025, 9, 23, 11, 0, 0, tzinfo=timezone.utc)
    event = build_event(context=bound_context, timestamp=aware)
    assert event.timestamp.tzinfo is timezone.utc


def test_log_event_rejects_empty_message(bound_context: LogContext) -> None:
    """An empty message raises a validation error."""
    with pytest.raises(ValueError, match="message"):
        build_event(context=bound_context, message="")


def test_log_event_dict_carries_job_id(bound_context: LogContext) -> None:
    """`to_dict` preserves the job identifier."""
    event = build_event(context=bound_context)
    assert event.to_dict()["context"]["job_id"] == bound_context.job_id


def test_log_event_dict_carries_extra_fields(bound_context: LogContext) -> None:
    """`to_dict` reverently echoes extra payloads."""
    event = build_event(context=bound_context, extra={"code": "E100"})
    assert event.to_dict()["extra"] == {"code": "E100"}


def test_log_event_dict_carries_level_severity(bound_context: LogContext) -> None:
    """`to_dict` renders the severity as an integer."""
    event = build_event(context=bound_context, level=LogLevel.ERROR)
    assert event.to_dict()["level"] == LogLevel.ERROR.severity


def test_log_event_json_contains_event_id(bound_context: LogContext) -> None:
    """`to_json` encodes the event identifier."""
    payload = build_event(context=bound_context).to_json()
    assert json.loads(payload)["event_id"] == "evt-1"


def test_log_event_json_contains_context(bound_context: LogContext) -> None:
    """`to_json` embeds the context frame."""
    payload = build_event(context=bound_context).to_json()
    assert json.loads(payload)["context"]["job_id"] == bound_context.job_id


def test_log_event_rejects_empty_event_id(bound_context: LogContext) -> None:
    with pytest.raises(ValueError, match="event_id"):
        build_event(context=bound_context, event_id="")


def test_log_event_dict_includes_exc_info(bound_context: LogContext) -> None:
    event = build_event(context=bound_context, exc_info="traceback")
    assert event.to_dict()["exc_info"] == "traceback"


def test_log_event_dict_includes_stack_info(bound_context: LogContext) -> None:
    event = build_event(context=bound_context, stack_info="stack")
    assert event.to_dict()["stack_info"] == "stack"
