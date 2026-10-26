"""Severity analytics helpers for processed log events.

Purpose
-------
Provide thread-safe counters that track the highest severity observed
and per-level tallies without mixing analytics concerns into the ring
buffer implementation.

Contents
--------
* :class:`SeverityMonitor` accumulating level counts and thresholds.

System Role
-----------
Bound into the runtime pipeline so operators can quickly inspect
aggregate severities (e.g., deciding whether to ship a log dump) without
replaying buffered events.

Alignment Notes
---------------
Counter semantics align with ``docs/systemdesign/module_reference.md``
(``Diagnostics & Monitoring`` section). Future histogram ideas are noted
in architectural concepts instead of the executable code.
"""

from __future__ import annotations

from collections import Counter
from threading import RLock
from typing import TYPE_CHECKING

from .levels import LogLevel

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .enums import DropReason

_DEFAULT_THRESHOLDS: tuple[LogLevel, ...] = (LogLevel.WARNING, LogLevel.ERROR)


def _normalize_thresholds(thresholds: Iterable[LogLevel] | None) -> tuple[LogLevel, ...]:
    """Normalize and sort thresholds, ensuring at least one is present."""
    if thresholds is None:
        return _DEFAULT_THRESHOLDS
    unique = set(thresholds)
    if not unique:
        unique = {LogLevel.ERROR}
    return tuple(sorted(unique))


class SeverityMonitor:
    """Accumulate per-level counts and expose the peak severity.

    Keeps analytics decoupled from the ring buffer while offering quick
    answers to "did we breach a threshold?" without scanning every
    buffered event.

    Args:
        thresholds: Optional iterable of :class:`LogLevel` used to maintain
            counts for "greater or equal" checks (defaults to ``WARNING`` and
            ``ERROR``).
        drop_reasons: Optional :class:`DropReason` members initialised at zero
            so snapshots always expose them as keys; any other reason appears
            the first time an event is dropped for it.

    Example:
        >>> monitor = SeverityMonitor()
        >>> monitor.highest() is None
        True
        >>> monitor.record(LogLevel.INFO)
        >>> monitor.record(LogLevel.ERROR)
        >>> monitor.highest() is LogLevel.ERROR
        True
        >>> monitor.counts()[LogLevel.INFO]
        1
        >>> monitor.threshold_counts()[LogLevel.WARNING]
        1
        >>> from lib_log_rich.domain.enums import DropReason
        >>> monitor.record_drop(LogLevel.ERROR, DropReason.QUEUE_FULL)
        >>> monitor.total_events()
        2
        >>> monitor.dropped_total()
        1
        >>> monitor.drops_by_reason()[DropReason.QUEUE_FULL]
        1
        >>> monitor.drops_by_reason()["queue_full"]  # members hash as their value
        1
        >>> monitor.reset(); monitor.highest() is None
        True

    """

    def __init__(
        self,
        *,
        thresholds: Iterable[LogLevel] | None = None,
        drop_reasons: Iterable[DropReason] | None = None,
    ) -> None:
        """Initialize the severity monitor with optional thresholds and drop reasons."""
        self._lock = RLock()
        self._highest: LogLevel | None = None
        self._total_events = 0
        self._level_counts: Counter[LogLevel] = Counter(dict.fromkeys(LogLevel, 0))
        self._thresholds = _normalize_thresholds(thresholds)
        self._threshold_counts: Counter[LogLevel] = Counter(dict.fromkeys(self._thresholds, 0))
        self._dropped_total = 0
        self._drops_by_reason: Counter[DropReason] = Counter(dict.fromkeys(drop_reasons or (), 0))
        self._drops_by_level: Counter[LogLevel] = Counter(dict.fromkeys(LogLevel, 0))
        self._drops_by_reason_and_level: Counter[tuple[DropReason, LogLevel]] = Counter()

    def record(self, level: LogLevel) -> None:
        """Register an emitted event at ``level``.

        Args:
            level: Severity level of the emitted event.

        Note:
            Increments internal counters and updates the cached peak level.

        """
        with self._lock:
            self._total_events += 1
            self._level_counts[level] += 1
            if self._highest is None or level > self._highest:
                self._highest = level
            for threshold in self._thresholds:
                if level >= threshold:
                    self._threshold_counts[threshold] += 1

    def record_drop(self, level: LogLevel, reason: DropReason) -> None:
        """Register that an event of ``level`` was dropped for ``reason``."""
        with self._lock:
            self._dropped_total += 1
            self._drops_by_reason[reason] += 1
            self._drops_by_level[level] += 1
            self._drops_by_reason_and_level[(reason, level)] += 1

    def highest(self) -> LogLevel | None:
        """Return the highest severity observed so far."""
        with self._lock:
            return self._highest

    def counts(self) -> Mapping[LogLevel, int]:
        """Return a snapshot of per-level counts."""
        with self._lock:
            return dict(self._level_counts)

    def threshold_counts(self) -> Mapping[LogLevel, int]:
        """Return counts of events meeting configured thresholds."""
        with self._lock:
            return dict(self._threshold_counts)

    def total_events(self) -> int:
        """Return the total number of recorded events."""
        with self._lock:
            return self._total_events

    def dropped_total(self) -> int:
        """Return the total number of dropped events."""
        with self._lock:
            return self._dropped_total

    def drops_by_reason(self) -> Mapping[DropReason, int]:
        """Return drop counts keyed by drop reason."""
        with self._lock:
            return dict(self._drops_by_reason)

    def drops_by_level(self) -> Mapping[LogLevel, int]:
        """Return drop counts grouped by severity level."""
        with self._lock:
            return dict(self._drops_by_level)

    def drops_by_reason_and_level(self) -> Mapping[tuple[DropReason, LogLevel], int]:
        """Return drop counts keyed by ``(reason, level)`` tuples."""
        with self._lock:
            return dict(self._drops_by_reason_and_level)

    def reset(self) -> None:
        """Clear counters and forget the current peak."""
        with self._lock:
            self._highest = None
            self._total_events = 0
            self._level_counts = Counter(dict.fromkeys(LogLevel, 0))
            self._threshold_counts = Counter(dict.fromkeys(self._thresholds, 0))
            self._dropped_total = 0
            self._drops_by_reason = Counter(dict.fromkeys(self._drops_by_reason, 0))
            self._drops_by_level = Counter(dict.fromkeys(LogLevel, 0))
            self._drops_by_reason_and_level = Counter()


__all__ = ["SeverityMonitor"]
