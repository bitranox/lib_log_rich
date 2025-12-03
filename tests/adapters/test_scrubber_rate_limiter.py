from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import FrozenSet

from lib_log_rich.adapters.rate_limiter import SlidingWindowRateLimiter
from lib_log_rich.adapters.scrubber import RegexScrubber
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


TokenValue = str | Mapping[str, str] | Sequence[str] | set[str] | FrozenSet[str] | bytes | int


def build_event(
    timestamp: datetime,
    password: str = "secret",
    token: TokenValue = "abc123",
) -> LogEvent:
    return LogEvent(
        event_id="evt",
        timestamp=timestamp,
        logger_name="tests",
        level=LogLevel.ERROR,
        message="boom",
        context=LogContext(service="svc", environment="test", job_id="job"),
        extra={"password": password, "token": token},
    )


def make_scrubber() -> RegexScrubber:
    return RegexScrubber(patterns={"password": r".+", "token": r"[0-9]+"})


def make_limiter(*, max_events: int, seconds: int) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(max_events=max_events, interval=timedelta(seconds=seconds))


def test_scrubber_ignores_blank_pattern_keys() -> None:
    scrubber = RegexScrubber(patterns={"   ": "secret"})
    event = build_event(datetime(2025, 9, 23, tzinfo=timezone.utc))
    assert scrubber.scrub(event) is event


def test_scrubber_masks_password_field() -> None:
    scrubbed = make_scrubber().scrub(build_event(datetime(2025, 9, 23, tzinfo=timezone.utc)))
    assert scrubbed.extra["password"] == "***"


def test_scrubber_masks_token_field() -> None:
    scrubbed = make_scrubber().scrub(build_event(datetime(2025, 9, 23, tzinfo=timezone.utc)))
    assert scrubbed.extra["token"] == "***"


def test_scrubber_masks_nested_mapping() -> None:
    event = build_event(
        datetime(2025, 9, 23, tzinfo=timezone.utc),
        token={"current": "abc123", "previous": "zzz"},
    )
    scrubbed = make_scrubber().scrub(event)
    assert scrubbed.extra["token"]["current"] == "***"
    assert scrubbed.extra["token"]["previous"] == "zzz"


def test_scrubber_masks_sequences() -> None:
    event = build_event(
        datetime(2025, 9, 23, tzinfo=timezone.utc),
        token=["abc123", "safe"],
    )
    scrubbed = make_scrubber().scrub(event)
    assert scrubbed.extra["token"][0] == "***"
    assert scrubbed.extra["token"][1] == "safe"


def test_scrubber_masks_sets() -> None:
    event = build_event(
        datetime(2025, 9, 23, tzinfo=timezone.utc),
        token={"abc123", "safe"},
    )
    scrubbed = make_scrubber().scrub(event)
    assert "***" in scrubbed.extra["token"]


def test_scrubber_masks_bytes_payload() -> None:
    event = build_event(datetime(2025, 9, 23, tzinfo=timezone.utc), token=b"abc123")
    scrubbed = make_scrubber().scrub(event)
    assert scrubbed.extra["token"] == "***"


def test_scrubber_preserves_frozenset_type() -> None:
    event = build_event(datetime(2025, 9, 23, tzinfo=timezone.utc), token=frozenset({"abc123", "safe"}))
    scrubbed = make_scrubber().scrub(event)
    assert isinstance(scrubbed.extra["token"], frozenset)
    assert "***" in scrubbed.extra["token"]


def test_scrubber_masks_tuple_payload() -> None:
    event = build_event(datetime(2025, 9, 23, tzinfo=timezone.utc), token=("abc123", "safe"))
    scrubbed = make_scrubber().scrub(event)
    assert isinstance(scrubbed.extra["token"], tuple)
    assert scrubbed.extra["token"][0] == "***"
    assert scrubbed.extra["token"][1] == "safe"


def test_scrubber_preserves_non_matching_values() -> None:
    event = build_event(datetime(2025, 9, 23, tzinfo=timezone.utc), token=12345)
    scrubbed = make_scrubber().scrub(event)
    assert scrubbed.extra["token"] == 12345


def test_scrubber_matches_keys_case_insensitively() -> None:
    base = build_event(datetime(2025, 9, 23, tzinfo=timezone.utc))
    mixed_case = base.replace(extra={"Password": "hunter2", "TOKEN": "12345"})
    scrubbed = make_scrubber().scrub(mixed_case)
    assert scrubbed.extra["Password"] == "***"
    assert scrubbed.extra["TOKEN"] == "***"


def test_scrubber_masks_context_extra() -> None:
    context = LogContext(
        service="svc",
        environment="test",
        job_id="job",
        extra={"password": "secret", "note": "safe"},
    )
    event = LogEvent(
        event_id="ctx",
        timestamp=datetime(2025, 9, 23, 12, 0, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=context,
    )
    scrubbed = make_scrubber().scrub(event)
    assert scrubbed.context.extra["password"] == "***"
    assert scrubbed.context.extra["note"] == "safe"
    # original context remains untouched to avoid leaking redacted values
    assert context.extra["password"] == "secret"


def test_rate_limiter_allows_first_event() -> None:
    limiter = make_limiter(max_events=2, seconds=1)
    base = datetime(2025, 9, 23, tzinfo=timezone.utc)
    assert limiter.allow(build_event(base)) is True


def test_rate_limiter_allows_second_event_within_window() -> None:
    limiter = make_limiter(max_events=2, seconds=1)
    base = datetime(2025, 9, 23, tzinfo=timezone.utc)
    limiter.allow(build_event(base))
    assert limiter.allow(build_event(base)) is True


def test_rate_limiter_blocks_third_event_within_window() -> None:
    limiter = make_limiter(max_events=2, seconds=1)
    base = datetime(2025, 9, 23, tzinfo=timezone.utc)
    limiter.allow(build_event(base))
    limiter.allow(build_event(base))
    assert limiter.allow(build_event(base)) is False


def test_rate_limiter_resets_after_interval() -> None:
    limiter = make_limiter(max_events=1, seconds=1)
    base = datetime(2025, 9, 23, tzinfo=timezone.utc)
    limiter.allow(build_event(base))
    later = base + timedelta(seconds=2)
    assert limiter.allow(build_event(later)) is True
