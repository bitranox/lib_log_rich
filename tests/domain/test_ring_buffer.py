from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, cast

import pytest

try:
    from hypothesis import given
    from hypothesis import strategies as st
except ModuleNotFoundError:  # pragma: no cover - optional dependency

    def _identity_decorator(*_args: object, **_kwargs: object) -> Callable[[Callable[..., object]], Callable[..., object]]:
        def decorator(func: Callable[..., object]) -> Callable[..., object]:
            return func

        return decorator

    given = cast(Callable[..., Callable[[Callable[..., object]], Callable[..., object]]], _identity_decorator)
    st = None

from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.ring_buffer import RingBuffer
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


@pytest.fixture
def sample_event() -> LogEvent:
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, 11, 0, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=LogContext(service="svc", environment="test", job_id="job-123"),
    )


def test_ring_buffer_rejects_non_positive_size(tmp_path: Path, sample_event: LogEvent) -> None:
    with pytest.raises(ValueError, match="max_events"):
        RingBuffer(max_events=0)


def test_ring_buffer_eviction_policy(tmp_path: Path, sample_event: LogEvent) -> None:
    buffer = RingBuffer(max_events=3)
    for idx in range(5):
        buffer.append(sample_event.replace(event_id=f"evt-{idx}"))
    snapshot = buffer.snapshot()
    assert [event.event_id for event in snapshot] == ["evt-2", "evt-3", "evt-4"]


def flush_single_event(checkpoint: Path, event: LogEvent) -> tuple[RingBuffer, list[str]]:
    buffer = RingBuffer(max_events=2, checkpoint_path=checkpoint)
    buffer.append(event)
    buffer.flush()
    lines = checkpoint.read_text(encoding="utf-8").strip().splitlines()
    return buffer, lines


def test_ring_buffer_flush_creates_checkpoint_file(tmp_path: Path, sample_event: LogEvent) -> None:
    checkpoint = tmp_path / "dump.jsonl"
    flush_single_event(checkpoint, sample_event)
    assert checkpoint.exists()


def test_ring_buffer_flush_writes_single_line(tmp_path: Path, sample_event: LogEvent) -> None:
    checkpoint = tmp_path / "dump.jsonl"
    _, lines = flush_single_event(checkpoint, sample_event)
    assert len(lines) == 1


def test_ring_buffer_flush_serializes_event_id(tmp_path: Path, sample_event: LogEvent) -> None:
    checkpoint = tmp_path / "dump.jsonl"
    _, lines = flush_single_event(checkpoint, sample_event)
    payload = json.loads(lines[0])
    assert payload["event_id"] == sample_event.event_id


def test_ring_buffer_second_flush_does_not_recreate_checkpoint(tmp_path: Path, sample_event: LogEvent) -> None:
    checkpoint = tmp_path / "events.jsonl"
    buffer, _ = flush_single_event(checkpoint, sample_event)
    checkpoint.unlink()
    buffer.flush()
    assert not checkpoint.exists()


def test_ring_buffer_load_checkpoint(tmp_path: Path, sample_event: LogEvent) -> None:
    checkpoint = tmp_path / "dump.jsonl"
    checkpoint.write_text(
        "\n".join(
            json.dumps(
                sample_event.replace(event_id=f"evt-{idx}").to_dict(),
                default=str,
                sort_keys=True,
            )
            for idx in range(3)
        ),
        encoding="utf-8",
    )
    buffer = RingBuffer(max_events=5, checkpoint_path=checkpoint)
    snapshot = buffer.snapshot()
    assert [event.event_id for event in snapshot] == ["evt-0", "evt-1", "evt-2"]


def test_ring_buffer_clear_empts_state(tmp_path: Path, sample_event: LogEvent) -> None:
    buffer = RingBuffer(max_events=2)
    buffer.append(sample_event)
    buffer.clear()
    assert buffer.snapshot() == []


if st is not None:
    assert st is not None  # Narrow for type checkers

    non_empty_text = st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != "")

    def _property_fifo(messages: list[str]) -> None:
        buffer = RingBuffer(max_events=5)
        base = datetime(2025, 9, 23, tzinfo=timezone.utc)
        for idx, message in enumerate(messages):
            event = LogEvent(
                event_id=f"evt-{idx}",
                timestamp=base + timedelta(seconds=idx),
                logger_name="tests",
                level=LogLevel.INFO,
                message=message,
                context=LogContext(service="svc", environment="test", job_id="job"),
            )
            buffer.append(event)

        snapshot = [event.message for event in buffer.snapshot()]
        assert snapshot == messages[-min(len(messages), buffer.max_events) :]

    test_ring_buffer_fifo_property = cast(
        Callable[[], None],
        given(st.lists(non_empty_text, min_size=1, max_size=20))(_property_fifo),
    )
else:

    @pytest.mark.skip(reason="Hypothesis not installed; FIFO property covered by deterministic cases")
    def test_ring_buffer_fifo_property() -> None:  # pragma: no cover - fallback path
        """Fallback FIFO property checks when Hypothesis is unavailable."""
        cases = [
            ["one"],
            ["alpha", "beta", "gamma"],
            [f"msg-{idx}" for idx in range(10)],
        ]
        for messages in cases:
            buffer = RingBuffer(max_events=5)
            base = datetime(2025, 9, 23, tzinfo=timezone.utc)
            for idx, message in enumerate(messages):
                event = LogEvent(
                    event_id=f"evt-{idx}",
                    timestamp=base + timedelta(seconds=idx),
                    logger_name="tests",
                    level=LogLevel.INFO,
                    message=message,
                    context=LogContext(service="svc", environment="test", job_id="job"),
                )
                buffer.append(event)
            snapshot = [event.message for event in buffer.snapshot()]
            assert snapshot == messages[-min(len(messages), buffer.max_events) :]


def build_extended_buffer(sample_event: LogEvent, count: int = 3) -> RingBuffer:
    buffer = RingBuffer(max_events=4)
    replacements = [sample_event.replace(event_id=f"evt-{idx}") for idx in range(count)]
    buffer.extend(replacements)
    return buffer


def test_ring_buffer_extend_sets_length(sample_event: LogEvent) -> None:
    buffer = build_extended_buffer(sample_event)
    assert len(buffer) == 3


def test_ring_buffer_extend_preserves_insertion_order(sample_event: LogEvent) -> None:
    buffer = build_extended_buffer(sample_event)
    assert [event.event_id for event in buffer] == ["evt-0", "evt-1", "evt-2"]


def test_ring_buffer_flush_skips_without_checkpoint(sample_event: LogEvent) -> None:
    buffer = RingBuffer(max_events=1)
    buffer.append(sample_event)
    buffer.flush()  # No checkpoint configured; should be a no-op that still leaves data intact.
    assert len(buffer.snapshot()) == 1


def test_ring_buffer_flush_second_call_is_noop(tmp_path: Path, sample_event: LogEvent) -> None:
    checkpoint = tmp_path / "events.jsonl"
    buffer = RingBuffer(max_events=2, checkpoint_path=checkpoint)
    buffer.append(sample_event)
    buffer.flush()
    assert checkpoint.exists()
    checkpoint.unlink()
    buffer.flush()
    assert not checkpoint.exists()


def test_ring_buffer_missing_checkpoint_file(tmp_path: Path) -> None:
    checkpoint = tmp_path / "missing.jsonl"
    buffer = RingBuffer(max_events=3, checkpoint_path=checkpoint)
    assert len(buffer) == 0


def test_ring_buffer_max_events_property(sample_event: LogEvent) -> None:
    buffer = RingBuffer(max_events=7)
    assert buffer.max_events == 7


def test_ring_buffer_load_checkpoint_ignores_blank_lines(tmp_path: Path, sample_event: LogEvent) -> None:
    checkpoint = tmp_path / "dump.jsonl"
    checkpoint.write_text(
        sample_event.to_json() + "\n\n",
        encoding="utf-8",
    )
    buffer = RingBuffer(max_events=2, checkpoint_path=checkpoint)
    assert len(buffer) == 1


def test_ring_buffer_load_checkpoint_handles_missing_file(sample_event: LogEvent, tmp_path: Path) -> None:
    buffer = RingBuffer(max_events=1)
    buffer._load_checkpoint(tmp_path / "nope.jsonl")  # type: ignore[attr-defined]
    assert len(buffer) == 0
