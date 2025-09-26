"""Sliding-window rate limiter for log events.

Implements the throttling policy described in ``konzept_architecture_plan.md``
by tracking per-logger/level buckets.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta
from typing import Deque, Dict, Tuple

from lib_log_rich.application.ports.rate_limiter import RateLimiterPort
from lib_log_rich.domain.events import LogEvent


class SlidingWindowRateLimiter(RateLimiterPort):
    """Limit events per logger/level combination within a time window."""

    def __init__(self, *, max_events: int, interval: timedelta) -> None:
        """Initialise the limiter with capacity and sliding window size."""
        self._max_events = max_events
        self._interval = interval
        self._buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

    def allow(self, event: LogEvent) -> bool:
        """Return ``True`` when ``event`` is within the configured quota."""
        key = (event.logger_name, event.level.severity)
        bucket = self._buckets[key]
        now = event.timestamp.timestamp()
        cutoff = now - self._interval.total_seconds()
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self._max_events:
            return False
        bucket.append(now)
        return True


__all__ = ["SlidingWindowRateLimiter"]
