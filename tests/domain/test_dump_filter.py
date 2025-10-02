from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from lib_log_rich.domain import DumpFilter, LogContext, LogEvent, LogLevel, build_dump_filter


def _make_event(**extras: str) -> LogEvent:
    context = LogContext(service="checkout", environment="prod", job_id="job", extra={"region": "eu"})
    return LogEvent(
        event_id="evt",
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        logger_name="svc.worker",
        level=LogLevel.INFO,
        message="hello",
        context=context,
        extra=dict(extras),
    )


def test_exact_context_match() -> None:
    filters = build_dump_filter(context={"service": "checkout"})
    assert filters.matches(_make_event())


def test_contains_context_extra_match() -> None:
    filters = build_dump_filter(context_extra={"region": {"contains": "u"}})
    assert filters.matches(_make_event())


def test_icontains_event_extra_match() -> None:
    filters = build_dump_filter(extra={"trace": {"icontains": "ABC"}})
    event = _make_event(trace="abc-42")
    assert filters.matches(event)


def test_regex_requires_flag() -> None:
    with pytest.raises(ValueError):
        build_dump_filter(extra={"trace": {"pattern": "^abc"}})


def test_regex_match() -> None:
    filters = build_dump_filter(extra={"trace": {"pattern": r"^abc", "regex": True}})
    assert filters.matches(_make_event(trace="abc-42"))


def test_compiled_regex_match() -> None:
    filters = build_dump_filter(extra={"trace": {"pattern": re.compile(r"^abc"), "regex": True}})
    assert filters.matches(_make_event(trace="abc-1"))


def test_or_predicates_for_field() -> None:
    filters = build_dump_filter(context={"service": ["checkout", "billing"]})
    assert filters.matches(_make_event())
    other = _make_event()
    other = replace(other, context=replace(other.context, service="billing"))
    assert filters.matches(other)


def test_mismatch_blocks_event() -> None:
    filters = build_dump_filter(context={"service": "checkout"}, extra={"trace": "expected"})
    assert not filters.matches(_make_event(trace="other"))


def test_inactive_filter_accepts_all() -> None:
    dump_filter = DumpFilter()
    assert dump_filter.matches(_make_event())
