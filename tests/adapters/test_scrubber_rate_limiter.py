from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lib_log_rich.adapters.scrubber import RegexScrubber
from lib_log_rich.adapters.rate_limiter import SlidingWindowRateLimiter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


def _event(ts: datetime, password: str = "secret") -> LogEvent:
    return LogEvent(
        event_id="evt",
        timestamp=ts,
        logger_name="tests",
        level=LogLevel.ERROR,
        message="boom",
        context=LogContext(service="svc", environment="test", job_id="job"),
        extra={"password": password, "token": "abc123"},
    )


def test_regex_scrubber_masks_sensitive_fields() -> None:
    scrubber = RegexScrubber(patterns={"password": r".+", "token": r"[0-9]+"})
    event = _event(datetime(2025, 9, 23, tzinfo=timezone.utc))
    scrubbed = scrubber.scrub(event)
    assert scrubbed.extra["password"] == "***"
    assert scrubbed.extra["token"] == "***"


def test_rate_limiter_blocks_excess_events() -> None:
    limiter = SlidingWindowRateLimiter(max_events=2, interval=timedelta(seconds=1))
    base = datetime(2025, 9, 23, tzinfo=timezone.utc)
    assert limiter.allow(_event(base)) is True
    assert limiter.allow(_event(base)) is True
    assert limiter.allow(_event(base)) is False


def test_rate_limiter_resets_after_window() -> None:
    limiter = SlidingWindowRateLimiter(max_events=1, interval=timedelta(seconds=1))
    base = datetime(2025, 9, 23, tzinfo=timezone.utc)
    assert limiter.allow(_event(base)) is True
    later = base + timedelta(seconds=2)
    assert limiter.allow(_event(later)) is True
