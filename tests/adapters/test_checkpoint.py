"""JSONL checkpoint adapter: the typed boundary for ring-buffer persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import pytest

from lib_log_rich.adapters.checkpoint import JsonlCheckpoint
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.ring_buffer import RingBuffer
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [OS_AGNOSTIC]

# A line exactly as lib_log_rich 6.x wrote it (LogEvent.to_dict with sorted keys), so a
# checkpoint left behind by the previous major still hydrates.
_SIX_X_LINE = (
    '{"context":{"environment":"prod","job_id":"job-1","process_id_chain":[10,11],"service":"svc"},'
    '"event_id":"evt-6x","extra":{"tenant":"alpha"},"level":"warning","logger_name":"svc.worker",'
    '"message":"carried over","timestamp":"2025-09-30T12:00:00+00:00"}'
)


def _event(event_id: str, **overrides: Any) -> LogEvent:
    base = LogEvent(
        event_id=event_id,
        timestamp=datetime(2025, 9, 30, 12, 0, 0, 123456, tzinfo=timezone.utc),
        logger_name="svc.worker",
        level=LogLevel.INFO,
        message="hello",
        context=LogContext(service="svc", environment="test", job_id="job-1"),
    )
    return base.replace(**overrides)


def _rich_event() -> LogEvent:
    context = LogContext(
        service="svc",
        environment="prod",
        job_id="job-9",
        request_id="req-1",
        user_name="alice",
        hostname="host-a",
        process_id=42,
        process_id_chain=(1, 42),
        trace_id="t" * 32,
        span_id="s" * 16,
        extra={"tenant": "beta", "unset": None},
    )
    return _event(
        "evt-rich",
        level=LogLevel.ERROR,
        message="boom",
        context=context,
        extra={"count": 3, "nested": {"a": [1, 2]}, "unset": None},
        exc_info="Traceback ...",
        stack_info="Stack ...",
    )


def _refusal(tmp_path: Path, line: str) -> str:
    path = tmp_path / "bad.jsonl"
    path.write_text(_event("evt-ok").to_json() + "\n" + line + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checkpoint") as excinfo:
        JsonlCheckpoint(path).load()
    return str(excinfo.value)


def test_append_then_load_round_trips_every_field(tmp_path: Path) -> None:
    checkpoint = JsonlCheckpoint(tmp_path / "events.jsonl")
    events = [_event("evt-plain"), _rich_event()]

    checkpoint.append(events)

    assert checkpoint.load() == events


def test_append_writes_the_same_lines_as_the_event_json(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    events = [_event("evt-plain"), _rich_event()]

    JsonlCheckpoint(path).append(events)

    assert path.read_text(encoding="utf-8").splitlines() == [event.to_json() for event in events]


def test_append_adds_to_an_existing_file(tmp_path: Path) -> None:
    checkpoint = JsonlCheckpoint(tmp_path / "nested" / "events.jsonl")

    checkpoint.append([_event("evt-1")])
    checkpoint.append([_event("evt-2")])

    assert [event.event_id for event in checkpoint.load()] == ["evt-1", "evt-2"]


def test_load_reads_a_line_written_by_the_previous_major(tmp_path: Path) -> None:
    path = tmp_path / "six.jsonl"
    path.write_text(_SIX_X_LINE + "\n", encoding="utf-8")

    (event,) = JsonlCheckpoint(path).load()

    assert event.event_id == "evt-6x"
    assert event.level is LogLevel.WARNING
    assert event.context.process_id_chain == (10, 11)
    assert event.context.request_id is None
    assert event.extra == {"tenant": "alpha"}
    assert event.timestamp == datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc)


def test_load_of_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert JsonlCheckpoint(tmp_path / "missing.jsonl").load() == []


def test_load_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "blank.jsonl"
    path.write_text("\n" + _event("evt-1").to_json() + "\n   \n\n", encoding="utf-8")

    assert [event.event_id for event in JsonlCheckpoint(path).load()] == ["evt-1"]


def test_a_line_that_is_not_json_is_refused_with_its_line_number(tmp_path: Path) -> None:
    message = _refusal(tmp_path, "{not json")

    assert "line 2" in message
    assert "errors.pydantic.dev" not in message


def test_a_line_missing_a_field_is_refused_naming_the_field(tmp_path: Path) -> None:
    message = _refusal(tmp_path, '{"event_id":"x","level":"info","logger_name":"l","message":"m","timestamp":"2025-09-30T12:00:00+00:00"}')

    assert "line 2" in message
    assert "context:" in message


def test_an_unknown_field_is_refused(tmp_path: Path) -> None:
    line = _event("evt-x").to_json()[:-1] + ',"surprise":1}'

    assert "surprise:" in _refusal(tmp_path, line)


def test_an_unknown_level_is_refused_naming_the_level(tmp_path: Path) -> None:
    line = _event("evt-x").to_json().replace('"level":"info"', '"level":"fatal"')

    message = _refusal(tmp_path, line)

    assert "level:" in message
    assert "fatal" in message


def test_a_naive_timestamp_is_refused(tmp_path: Path) -> None:
    line = _event("evt-x").to_json().replace("+00:00", "")

    assert "timestamp:" in _refusal(tmp_path, line)


def test_a_blank_context_service_is_refused(tmp_path: Path) -> None:
    line = _event("evt-x").to_json().replace('"service":"svc"', '"service":"  "')

    assert "context.service:" in _refusal(tmp_path, line)


def test_a_string_where_a_pid_belongs_is_refused(tmp_path: Path) -> None:
    line = _SIX_X_LINE.replace("[10,11]", '["10",11]')

    assert "context.process_id_chain.0:" in _refusal(tmp_path, line)


def test_a_json_array_line_is_refused(tmp_path: Path) -> None:
    assert "line 2" in _refusal(tmp_path, "[]")


def test_ring_buffer_hydrates_from_its_checkpoint(tmp_path: Path) -> None:
    checkpoint = JsonlCheckpoint(tmp_path / "events.jsonl")
    checkpoint.append([_event(f"evt-{idx}") for idx in range(3)])

    buffer = RingBuffer(max_events=2, checkpoint=checkpoint)

    assert [event.event_id for event in buffer] == ["evt-1", "evt-2"]


def test_ring_buffer_flush_appends_to_its_checkpoint_and_clears(tmp_path: Path) -> None:
    checkpoint = JsonlCheckpoint(tmp_path / "events.jsonl")
    buffer = RingBuffer(max_events=4, checkpoint=checkpoint)
    buffer.append(_event("evt-1"))

    buffer.flush()

    assert len(buffer) == 0
    assert [event.event_id for event in checkpoint.load()] == ["evt-1"]


def test_ring_buffer_refuses_a_corrupt_checkpoint_at_construction(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{not json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line 1"):
        RingBuffer(max_events=2, checkpoint=JsonlCheckpoint(path))


def test_ring_buffer_no_longer_takes_a_checkpoint_path(tmp_path: Path) -> None:
    # Spread as Any so the removed keyword reaches the runtime without a checker suppression.
    removed: dict[str, Any] = {"max_events": 2, "checkpoint_path": tmp_path / "x.jsonl"}

    with pytest.raises(TypeError, match="checkpoint_path"):
        RingBuffer(**removed)


def test_log_event_has_no_dict_parser() -> None:
    assert not hasattr(LogEvent, "from_dict")


def test_a_timestamp_that_overflows_once_converted_to_utc_is_refused(tmp_path: Path) -> None:
    line = _event("evt-x").to_json().replace("2025-09-30T12:00:00.123456+00:00", "0001-01-01T00:00:00+01:00")

    message = _refusal(tmp_path, line)

    assert "line 2" in message
    assert "timestamp:" in message


@pytest.mark.parametrize("stored", ["20", "true"])
def test_a_level_that_is_not_a_severity_name_is_refused_naming_the_names(tmp_path: Path, stored: str) -> None:
    line = _event("evt-x").to_json().replace('"level":"info"', f'"level":{stored}')

    message = _refusal(tmp_path, line)

    assert "level:" in message
    assert "warning" in message
    assert "10, 20" not in message
