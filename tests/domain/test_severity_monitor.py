from __future__ import annotations

from typing import TYPE_CHECKING, cast

from lib_log_rich.domain import DropReason, LogLevel, SeverityMonitor
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Mapping

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
    monitor.record_drop(LogLevel.CRITICAL, DropReason.QUEUE_FULL)

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

    monitor.record_drop(LogLevel.ERROR, DropReason.QUEUE_FULL)
    monitor.record_drop(LogLevel.WARNING, DropReason.RATE_LIMITED)
    monitor.record_drop(LogLevel.WARNING, DropReason.RATE_LIMITED)

    assert monitor.dropped_total() == 3
    assert monitor.drops_by_reason()[DropReason.QUEUE_FULL] == 1
    assert monitor.drops_by_reason()[DropReason.RATE_LIMITED] == 2
    assert monitor.drops_by_level()[LogLevel.WARNING] == 2
    assert monitor.drops_by_reason_and_level()[(DropReason.RATE_LIMITED, LogLevel.WARNING)] == 2


def test_severity_monitor_drop_counters_answer_to_the_value_strings() -> None:
    # A 6.x caller indexes the counters with the plain strings; the members
    # hash and compare as their values, so that lookup keeps working untyped.
    monitor = SeverityMonitor()
    monitor.record_drop(LogLevel.WARNING, DropReason.RATE_LIMITED)

    by_reason = cast("Mapping[str, int]", monitor.drops_by_reason())
    by_reason_and_level = cast("Mapping[tuple[str, LogLevel], int]", monitor.drops_by_reason_and_level())

    assert by_reason["rate_limited"] == 1
    assert by_reason_and_level[("rate_limited", LogLevel.WARNING)] == 1


def test_severity_monitor_seeds_the_given_drop_reasons_at_zero() -> None:
    monitor = SeverityMonitor(drop_reasons=(DropReason.RATE_LIMITED, DropReason.QUEUE_FULL))

    assert dict(monitor.drops_by_reason()) == {DropReason.RATE_LIMITED: 0, DropReason.QUEUE_FULL: 0}

    monitor.record_drop(LogLevel.ERROR, DropReason.ADAPTER_ERROR)
    monitor.reset()

    assert dict(monitor.drops_by_reason()) == {DropReason.RATE_LIMITED: 0, DropReason.QUEUE_FULL: 0, DropReason.ADAPTER_ERROR: 0}


def test_severity_monitor_uses_default_threshold_when_provided_empty_iterable() -> None:
    monitor = SeverityMonitor(thresholds=[])
    monitor.record(LogLevel.ERROR)
    assert monitor.threshold_counts()[LogLevel.ERROR] == 1
