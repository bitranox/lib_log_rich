from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.dump_filter import DumpFilter, FieldFilter, FieldPredicate, PredicateKind, build_dump_filter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


@pytest.fixture
def sample_event() -> LogEvent:
    context = LogContext(
        service="svc",
        environment="prod",
        job_id="job-1",
        extra={"region": "eu"},
    )
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
        logger_name="svc.worker",
        level=LogLevel.INFO,
        message="hello",
        context=context,
        extra={"request": "REQ-123", "payload": b"token"},
    )


def test_field_predicate_matching_modes() -> None:
    exact = FieldPredicate(kind=PredicateKind.EXACT, expected="value")
    contains = FieldPredicate(kind=PredicateKind.CONTAINS, expected="REQ")
    icontains = FieldPredicate(kind=PredicateKind.ICONTAINS, expected="req")

    assert exact.matches("value")
    assert contains.matches("REQ-1")
    assert contains.matches(b"REQ-2")
    assert icontains.matches("Req-3")
    assert not icontains.matches(None)


def test_field_predicate_regex_matching() -> None:
    predicate = FieldPredicate(kind=PredicateKind.REGEX, expected="^req", pattern=None)
    assert predicate.matches("req-1") is False  # pattern missing

    compiled = FieldPredicate(kind=PredicateKind.REGEX, expected="^req", pattern=re.compile("^req", re.IGNORECASE))
    assert compiled.matches("REQ-5")


def test_build_dump_filter_matches_event(sample_event: LogEvent) -> None:
    filters = build_dump_filter(
        context={"service": "svc"},
        context_extra={"region": {"icontains": "EU"}},
        extra={"request": {"regex": True, "pattern": "^req", "flags": ["IGNORECASE"]}},
    )
    assert filters.matches(sample_event)


def test_dump_filter_rejects_on_context_mismatch(sample_event: LogEvent) -> None:
    filters = build_dump_filter(context={"service": "other"})
    assert filters.matches(sample_event) is False


def test_build_dump_filter_rejects_empty_predicates() -> None:
    with pytest.raises(ValueError, match="No predicates defined"):
        build_dump_filter(context={"service": []})


def test_build_dump_filter_requires_regex_configuration() -> None:
    with pytest.raises(ValueError, match="must specify exactly one predicate mode"):
        build_dump_filter(extra={"request": {"regex": True}})

    with pytest.raises(ValueError, match="requires a 'pattern' value"):
        build_dump_filter(extra={"request": {"regex": True, "pattern": None}})

    with pytest.raises(ValueError, match="must set 'regex'"):
        build_dump_filter(extra={"request": {"pattern": "^req$"}})


def test_parse_regex_flags_supports_sequence(sample_event: LogEvent) -> None:
    filters = build_dump_filter(
        extra={
            "request": {
                "regex": True,
                "pattern": "^req",
                "flags": ["IGNORECASE", "MULTILINE"],
            }
        }
    )
    assert filters.matches(sample_event)

    with pytest.raises(ValueError, match="Unsupported regex flag specification"):
        build_dump_filter(extra={"request": {"regex": True, "pattern": "^req", "flags": {"bad": True}}})


def test_field_filter_matches_bytes_using_contains() -> None:
    predicate = FieldPredicate(kind=PredicateKind.CONTAINS, expected="tok")
    field_filter = FieldFilter(field="payload", predicates=(predicate,))
    event = LogEvent(
        event_id="evt",
        timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc),
        logger_name="svc",
        level=LogLevel.INFO,
        message="msg",
        context=LogContext(service="svc", environment="prod", job_id="job"),
        extra={"payload": b"token"},
    )
    assert field_filter.matches(event.extra["payload"])


def test_dump_filter_without_filters_allows_all(sample_event: LogEvent) -> None:
    filters = DumpFilter()
    assert filters.matches(sample_event)


def test_dump_filter_context_extra_mismatch(sample_event: LogEvent) -> None:
    filters = build_dump_filter(
        context={"service": "svc"},
        context_extra={"region": "apac"},
    )
    assert filters.matches(sample_event) is False


def test_parse_sequence_predicates() -> None:
    filters = build_dump_filter(context={"service": ["svc", {"contains": "svc"}]})
    context = LogContext(service="svc", environment="prod", job_id="job")
    event = LogEvent(
        event_id="evt",
        timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc),
        logger_name="svc",
        level=LogLevel.INFO,
        message="msg",
        context=context,
    )
    assert filters.matches(event)


def test_compiled_regex_predicate_matches(sample_event: LogEvent) -> None:
    pattern = re.compile("^req", re.IGNORECASE)
    filters = build_dump_filter(extra={"request": pattern})
    assert filters.matches(sample_event)


def test_build_dump_filter_accepts_compiled_pattern_option(sample_event: LogEvent) -> None:
    filters = build_dump_filter(extra={"request": {"regex": True, "pattern": re.compile("^req", re.IGNORECASE)}})
    assert filters.matches(sample_event)


def test_mapping_modes_cover_all_predicates(sample_event: LogEvent) -> None:
    filters = build_dump_filter(
        context={"service": {"exact": "svc"}},
        extra={
            "request": {"contains": "REQ"},
            "missing": {"icontains": "none"},
        },
    )
    assert filters.matches(sample_event) is False


def test_regex_flags_support_numeric_and_string(sample_event: LogEvent) -> None:
    filters = build_dump_filter(
        extra={
            "request": {
                "regex": True,
                "pattern": "^req",
                "flags": re.IGNORECASE,
            },
            "payload": {
                "regex": True,
                "pattern": "TOKEN",
                "flags": "ignorecase",
            },
        }
    )
    assert filters.matches(sample_event)


def test_regex_flag_invalid_sequence_entry_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported regex flag specification"):
        build_dump_filter(extra={"request": {"regex": True, "pattern": "^req", "flags": [object()]}})


def test_regex_flag_defaults_to_zero(sample_event: LogEvent) -> None:
    filters = build_dump_filter(extra={"request": {"regex": True, "pattern": "^REQ"}})
    assert filters.matches(sample_event)


def test_to_text_converts_non_string_values() -> None:
    predicate = FieldPredicate(kind=PredicateKind.CONTAINS, expected="123")
    assert predicate.matches(12345)
