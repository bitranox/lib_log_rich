from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pytest

from lib_log_rich.adapters._formatting import build_format_payload
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.events import LogEvent
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

EventFactory = Callable[[dict[str, Any] | None], LogEvent]


@pytest.fixture
def formatted_event(event_factory: EventFactory) -> dict[str, Any]:
    """Provide an event tuned to exercise every formatting branch."""

    context = LogContext(
        service="svc",
        environment="prod",
        job_id="job-7",
        user_name="bard",
        hostname="orchestra",
        process_id=777,
        process_id_chain=(123, 456, 777),
    )
    extra = {"theme": "dawn", "custom": "glow"}
    base: LogEvent = event_factory(
        {
            "context": context,
            "extra": extra,
            "timestamp": datetime(2025, 10, 13, 14, 15, 16, 789123, tzinfo=timezone.utc),
            "level": LogLevel.WARNING,
            "message": "lantern",
        }
    )
    return build_format_payload(base)


@pytest.mark.parametrize(
    ("raw_chain", "expected"),
    [
        pytest.param([1, 2, 3], "1>2>3", id="iterable"),
        pytest.param(None, "", id="none"),
        pytest.param("solo", "solo", id="string"),
    ],
)
def test_the_payload_normalises_process_chain(event_factory: EventFactory, raw_chain: object, expected: str) -> None:
    """Process chain inputs collapse into their formatted variants."""

    event: LogEvent = event_factory(None)

    class DictContext:
        def to_dict(self, *, include_none: bool = False) -> dict[str, object]:
            return {
                "service": "svc",
                "environment": "prod",
                "job_id": "job",
                "process_id": 1,
                "process_id_chain": raw_chain,
            }

    object.__setattr__(event, "context", DictContext())
    payload = build_format_payload(event)
    assert payload["process_id_chain"] == expected


def test_the_payload_whispers_when_context_is_empty(event_factory: EventFactory) -> None:
    """Empty context dictionaries leave context_fields blank."""

    class EmptyContext:
        def to_dict(self, *, include_none: bool = False) -> dict[str, object]:
            return {}

    event: LogEvent = event_factory(
        {
            "context": EmptyContext(),  # type: ignore[arg-type]
            "extra": {},
        }
    )
    payload = build_format_payload(event)
    assert payload["context_fields"] == ""


def test_the_payload_paints_the_iso_timestamp(formatted_event: dict[str, object]) -> None:
    """The primary timestamp glows as an ISO string."""

    assert formatted_event["timestamp"] == "2025-10-13T14:15:16.789123+00:00"


def test_the_payload_trims_microseconds(formatted_event: dict[str, object]) -> None:
    """The trimmed timestamp drops its trailing shimmer."""

    assert formatted_event["timestamp_trimmed"] == "2025-10-13T14:15:16+00:00"


def test_the_payload_echoes_level_icons(formatted_event: dict[str, object]) -> None:
    """Level icons mirror their dotted alias."""

    assert formatted_event["level.icon"] == formatted_event["level_icon"]


def test_the_payload_rescues_context_fields(formatted_event: dict[str, object]) -> None:
    """Context fields waltz into a sorted string."""

    assert (
        formatted_event["context_fields"]
        == " custom=glow environment=prod hostname=orchestra job_id=job-7 process_id=777 process_id_chain=[123, 456, 777] service=svc theme=dawn user_name=bard"
    )


def test_the_payload_announces_process_chain(formatted_event: dict[str, object]) -> None:
    """Process chains arrive already braided."""

    assert formatted_event["process_id_chain"] == "123>456>777"


def test_the_payload_remembers_local_time(formatted_event: dict[str, object]) -> None:
    """Local timestamps keep their timezone hue."""

    utc_stamp = datetime(2025, 10, 13, 14, 15, 16, tzinfo=timezone.utc)
    local_expected = utc_stamp.astimezone().replace(microsecond=0)
    assert formatted_event["timestamp_trimmed_naive_loc"] == local_expected.strftime("%Y-%m-%dT%H:%M:%S")
