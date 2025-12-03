"""Sliding-window rate limiter for log events.

Purpose
-------
Throttle log event emission per logger/level pair so downstream adapters are
not overwhelmed during error loops.

Contents
--------
* :class:`SlidingWindowRateLimiter` – implementation of :class:`RateLimiterPort`.

System Role
-----------
Implements the resilience guidance from ``concept_architecture_plan.md`` by
tracking per-bucket quotas and exposing a simple ``allow`` predicate.

Alignment Notes
---------------
Configuration shape (max events, interval) matches the options referenced in
``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta

from lib_log_rich.application.ports.rate_limiter import RateLimiterPort
from lib_log_rich.domain.events import LogEvent


class SlidingWindowRateLimiter(RateLimiterPort):
    """Limit events per logger/level combination within a time window.

    Protects downstream systems from event floods while keeping burst capacity
    configurable.

    Args:
        max_events: Maximum number of events permitted within ``interval``.
        interval: Window size tracked for each logger/level pair.

    Example:
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('1', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
        >>> limiter = SlidingWindowRateLimiter(max_events=1, interval=timedelta(seconds=60))
        >>> limiter.allow(event)
        True
        >>> limiter.allow(event)
        False

    """

    def __init__(self, *, max_events: int, interval: timedelta) -> None:
        """Initialise the limiter with capacity and sliding window size."""
        self._max_events = max_events
        self._interval = interval
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, event: LogEvent) -> bool:
        """Return ``True`` when ``event`` is within the configured quota.

        Implements sliding window rate limiting per (logger_name, severity) bucket:

        1. Retrieve or create a deque of timestamps for this bucket
        2. Remove all timestamps older than the current window
        3. Check if bucket has capacity for one more event
        4. If yes, record this event's timestamp and allow it
        5. If no, reject the event (rate limit exceeded)

        Note:
            NOT thread-safe. Caller must ensure exclusive access if used across threads.

        """
        # Use (logger_name, severity) as bucket key to track quotas independently
        # per logger/level combination (e.g., "app.worker" at ERROR vs DEBUG)
        key = (event.logger_name, event.level.severity)
        bucket = self._buckets[key]

        # Calculate the sliding window: all events within [cutoff, now] are counted
        now = event.timestamp.timestamp()
        cutoff = now - self._interval.total_seconds()

        # Evict expired timestamps from left (oldest) side of deque
        # This maintains the sliding window by removing events outside the interval
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        # Reject if we've already hit the quota within this window
        if len(bucket) >= self._max_events:
            return False

        # Accept the event and record its timestamp for future checks
        bucket.append(now)
        return True


__all__ = ["SlidingWindowRateLimiter"]
