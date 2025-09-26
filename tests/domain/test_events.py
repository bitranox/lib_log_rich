from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


def test_log_event_requires_timezone_aware_timestamp(bound_context: LogContext) -> None:
    naive = datetime(2025, 9, 23, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        LogEvent(
            event_id="evt-1",
            timestamp=naive,
            logger_name="tests",
            level=LogLevel.INFO,
            message="hello",
            context=bound_context,
        )


def test_log_event_coerces_timestamp_to_utc(bound_context: LogContext) -> None:
    aware = datetime(2025, 9, 23, 11, 0, 0, tzinfo=timezone.utc)
    event = LogEvent(
        event_id="evt-1",
        timestamp=aware,
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=bound_context,
    )
    assert event.timestamp.tzinfo is timezone.utc


def test_log_event_rejects_empty_message(bound_context: LogContext) -> None:
    with pytest.raises(ValueError, match="message"):
        LogEvent(
            event_id="evt-1",
            timestamp=datetime.now(timezone.utc),
            logger_name="tests",
            level=LogLevel.INFO,
            message="",
            context=bound_context,
        )


def test_log_event_to_dict_includes_context(bound_context: LogContext) -> None:
    event = LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, 11, 0, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.ERROR,
        message="boom",
        context=bound_context,
        extra={"code": "E100"},
    )
    data = event.to_dict()
    assert data["context"]["job_id"] == bound_context.job_id
    assert data["extra"] == {"code": "E100"}
    assert data["level"] == LogLevel.ERROR.severity


def test_log_event_to_json_produces_sorted_keys(bound_context: LogContext) -> None:
    event = LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, 11, 0, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.DEBUG,
        message="debug",
        context=bound_context,
    )
    payload = event.to_json()
    decoded = json.loads(payload)
    assert decoded["event_id"] == "evt-1"
    assert decoded["context"]["job_id"] == bound_context.job_id
