from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest

from lib_log_rich.adapters._schemas import LogContextPayload, LogEventPayload
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def build_context(**overrides: Any) -> LogContext:
    data: dict[str, Any] = dict(
        service="svc",
        environment="env",
        job_id="job",
        request_id="req",
        user_id="uid",
        user_name="user",
        hostname="host",
        process_id=4321,
        process_id_chain=(1, 2, 3),
        trace_id="trace",
        span_id="span",
        extra={"key": "value"},
    )
    data.update(overrides)
    return LogContext(**data)


def build_event(**overrides: Any) -> LogEvent:
    ctx = build_context()
    payload: dict[str, Any] = {
        "event_id": "evt-1",
        "timestamp": datetime(2025, 10, 8, 12, 0, tzinfo=timezone.utc),
        "logger_name": "tests.logger",
        "level": LogLevel.INFO,
        "message": "hello",
        "context": ctx,
        "extra": {"number": 1},
        "exc_info": None,
        "stack_info": None,
    }
    payload.update(overrides)
    return LogEvent(**payload)


def test_coerce_chain_handles_various_inputs() -> None:
    payload_cls = cast(Any, LogContextPayload)
    assert payload_cls._coerce_chain(None) == []
    assert payload_cls._coerce_chain("") == []
    assert payload_cls._coerce_chain([1, "2"]) == [1, 2]
    assert payload_cls._coerce_chain((3, 4)) == [3, 4]
    assert payload_cls._coerce_chain({5, 6}) in ([5, 6], [6, 5])
    assert payload_cls._coerce_chain("7") == [7]


def test_dict_copy_normalises_keys() -> None:
    mapping = {1: "a", "two": 2}
    payload_cls = cast(Any, LogContextPayload)
    result = payload_cls._dict_copy(mapping)
    assert result == {"1": "a", "two": 2}
    assert payload_cls._dict_copy(None) == {}
    with pytest.raises(TypeError, match="extra metadata must be a mapping"):
        payload_cls._dict_copy(["not", "mapping"])


def test_copy_extra_matches_context_validator() -> None:
    mapping = {"name": "value"}
    payload_cls = cast(Any, LogEventPayload)
    assert payload_cls._copy_extra(mapping) == mapping
    assert payload_cls._copy_extra(None) == {}
    with pytest.raises(TypeError, match="event extras must be a mapping"):
        payload_cls._copy_extra(["invalid"])


def test_context_payload_from_domain_context() -> None:
    context = build_context()
    payload = LogContextPayload.from_context(context)
    assert payload.service == "svc"
    assert payload.process_id_chain == [1, 2, 3]
    assert payload.extra == {"key": "value"}


def test_event_payload_from_domain_event() -> None:
    event = build_event()
    payload = LogEventPayload.from_event(event)
    assert payload.event_id == "evt-1"
    assert payload.level == LogLevel.INFO.severity
    assert payload.context.service == "svc"
    assert payload.extra == {"number": 1}
    assert payload.stack_info is None


def test_event_payload_includes_stack_info() -> None:
    event = build_event(stack_info="stack")
    payload = LogEventPayload.from_event(event)
    assert payload.stack_info == "stack"
