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
from typing import Deque, Dict, Tuple

from lib_log_rich.application.ports.rate_limiter import RateLimiterPort
from lib_log_rich.domain.events import LogEvent


class SlidingWindowRateLimiter(RateLimiterPort):
    """Limit events per logger/level combination within a time window.

    Why
    ---
    Protects downstream systems from event floods while keeping burst capacity
    configurable.

    Parameters
    ----------
    max_events:
        Maximum number of events permitted within ``interval``.
    interval:
        Window size tracked for each logger/level pair.

    Examples
    --------
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
