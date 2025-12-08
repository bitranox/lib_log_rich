from __future__ import annotations

from lib_log_rich.domain import LogLevel, SeverityMonitor
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def test_severity_monitor_tracks_counts_and_thresholds() -> None:
    monitor = SeverityMonitor()

    assert monitor.highest() is None
    assert monitor.total_events() == 0

    monitor.record(LogLevel.DEBUG)
    monitor.record(LogLevel.WARNING)
    monitor.record(LogLevel.ERROR)

    counts = monitor.counts()
    thresholds = monitor.threshold_counts()

    assert counts[LogLevel.DEBUG] == 1
    assert counts[LogLevel.WARNING] == 1
    assert counts[LogLevel.ERROR] == 1
    assert monitor.highest() is LogLevel.ERROR
    assert monitor.total_events() == 3
    assert thresholds[LogLevel.WARNING] == 2  # WARNING + ERROR
    assert thresholds[LogLevel.ERROR] == 1


def test_severity_monitor_reset_clears_state() -> None:
    monitor = SeverityMonitor()
    monitor.record(LogLevel.CRITICAL)
    monitor.record_drop(LogLevel.CRITICAL, "queue_full")

    monitor.reset()

    assert monitor.highest() is None
    assert monitor.total_events() == 0
    assert all(count == 0 for count in monitor.counts().values())
    assert all(count == 0 for count in monitor.threshold_counts().values())
    assert monitor.dropped_total() == 0
    assert all(count == 0 for count in monitor.drops_by_reason().values())
    assert all(count == 0 for count in monitor.drops_by_level().values())


def test_severity_monitor_records_drops_by_reason_and_level() -> None:
    monitor = SeverityMonitor()

    monitor.record_drop(LogLevel.ERROR, "queue_full")
    monitor.record_drop(LogLevel.WARNING, "rate_limited")
    monitor.record_drop(LogLevel.WARNING, "rate_limited")

    assert monitor.dropped_total() == 3
    assert monitor.drops_by_reason()["queue_full"] == 1
    assert monitor.drops_by_reason()["rate_limited"] == 2
    assert monitor.drops_by_level()[LogLevel.WARNING] == 2
    assert monitor.drops_by_reason_and_level()[("rate_limited", LogLevel.WARNING)] == 2


def test_severity_monitor_uses_default_threshold_when_provided_empty_iterable() -> None:
    monitor = SeverityMonitor(thresholds=[])
    monitor.record(LogLevel.ERROR)
    assert monitor.threshold_counts()[LogLevel.ERROR] == 1
