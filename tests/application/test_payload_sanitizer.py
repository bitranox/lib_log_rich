from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from lib_log_rich.application.use_cases._payload_sanitizer import (
    TRUNCATION_SUFFIX,
    PayloadSanitizer,
)
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.enums import DiagnosticEvent
from lib_log_rich.runtime import PayloadLimits
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from lib_log_rich.application.use_cases._types import DiagnosticPayload

pytestmark = [OS_AGNOSTIC]


def _collecting_sanitizer(**limit_overrides: Any) -> tuple[PayloadSanitizer, list[tuple[DiagnosticEvent, DiagnosticPayload]]]:
    diagnostics: list[tuple[DiagnosticEvent, DiagnosticPayload]] = []
    limits = PayloadLimits(**limit_overrides) if limit_overrides else PayloadLimits()

    def recorder(event_name: DiagnosticEvent, payload: DiagnosticPayload) -> None:
        diagnostics.append((event_name, payload))

    sanitizer = PayloadSanitizer(limits, recorder)
    return sanitizer, diagnostics


def test_sanitize_extra_collapses_nested_mapping() -> None:
    sanitizer, diagnostics = _collecting_sanitizer(extra_max_depth=2)
    payload = {
        "deep": {
            "level1": {
                "level2": {
                    "level3": "value",
                }
            }
        }
    }

    sanitized, exc_info, stack_info = sanitizer.sanitize_extra(payload, event_id="evt", logger_name="tests")

    assert exc_info is None
    assert stack_info is None
    collapsed = sanitized["deep"]["level1"]["level2"]
    assert isinstance(collapsed, str)
    assert "level3" in collapsed
    assert any(name == "extra_value_truncated_depth_collapsed" for name, _ in diagnostics)


def test_sanitize_message_truncates_when_enabled() -> None:
    sanitizer, diagnostics = _collecting_sanitizer(message_max_chars=12, truncate_message=True)

    result = sanitizer.sanitize_message("a really long message", event_id="evt", logger_name="tests")

    assert result.endswith(TRUNCATION_SUFFIX)
    assert any(name == "message_truncated" for name, _ in diagnostics)


def test_sanitize_context_returns_new_instance_when_trimmed() -> None:
    sanitizer, diagnostics = _collecting_sanitizer(context_max_keys=1)

    context = LogContext(service="svc", environment="env", job_id="job", extra={"a": 1, "b": 2})
    updated, changed = sanitizer.sanitize_context(context, event_id="evt", logger_name="tests")

    assert changed is True
    assert updated.extra.keys() <= {"a", "b"}
    assert len(updated.extra) == 1
    assert context.extra == {"a": 1, "b": 2}
    assert any(name == "context_extra_keys_dropped" for name, _ in diagnostics)


def test_sanitize_message_raises_when_truncation_disabled() -> None:
    sanitizer, _ = _collecting_sanitizer(message_max_chars=5, truncate_message=False)

    with pytest.raises(ValueError):
        sanitizer.sanitize_message("too long", event_id="evt", logger_name="tests")


def _deep_payload() -> dict[str, Any]:
    return {
        "outer": {"inner": {"leaf": {"deepest": "x" * 30}}, "long": "y" * 30, "spare1": 1, "spare2": 2},
        "wide": "z" * 40,
        "spare": 3,
    }


def _tight_sanitizer() -> tuple[PayloadSanitizer, list[tuple[DiagnosticEvent, DiagnosticPayload]]]:
    return _collecting_sanitizer(
        extra_max_keys=2,
        extra_max_value_chars=16,
        extra_max_depth=3,
        extra_max_total_bytes=40,
        context_max_keys=2,
        context_max_value_chars=16,
    )


def test_nested_extra_reports_only_the_documented_extra_event_names() -> None:
    # A nested mapping reports with its top-level mapping's events; the nesting
    # level is in the payload, not compounded into the event name.
    sanitizer, diagnostics = _tight_sanitizer()

    sanitizer.sanitize_extra(_deep_payload(), event_id="evt", logger_name="tests")

    names = {name for name, _ in diagnostics}
    assert names == {
        DiagnosticEvent.EXTRA_VALUE_TRUNCATED,
        DiagnosticEvent.EXTRA_VALUE_TRUNCATED_DEPTH_COLLAPSED,
        DiagnosticEvent.EXTRA_KEYS_DROPPED,
        DiagnosticEvent.EXTRA_TOTAL_TRIMMED,
    }
    # Innermost first: each level reports once its own nested mapping is done.
    collapsed_depths = [payload["depth"] for name, payload in diagnostics if name is DiagnosticEvent.EXTRA_VALUE_TRUNCATED_DEPTH_COLLAPSED]
    assert collapsed_depths == [3, 2, 1]


def test_nested_context_extra_reports_only_the_documented_context_event_names() -> None:
    sanitizer, diagnostics = _tight_sanitizer()
    context = LogContext(service="svc", environment="env", job_id="job", extra=_deep_payload())

    sanitizer.sanitize_context(context, event_id="evt", logger_name="tests")

    names = {name for name, _ in diagnostics}
    assert names == {
        DiagnosticEvent.CONTEXT_EXTRA_VALUE_TRUNCATED,
        DiagnosticEvent.CONTEXT_EXTRA_VALUE_TRUNCATED_DEPTH_COLLAPSED,
        DiagnosticEvent.CONTEXT_EXTRA_KEYS_DROPPED,
    }


def test_truncation_reasons_reach_the_payload_as_plain_strings() -> None:
    sanitizer, diagnostics = _tight_sanitizer()

    sanitizer.sanitize_message("m" * 5000, event_id="evt", logger_name="tests")
    sanitizer.sanitize_extra(_deep_payload(), event_id="evt", logger_name="tests")

    reasons = [payload["reason"] for _, payload in diagnostics if "reason" in payload]
    assert set(reasons) == {"message", "depth", "length"}
    assert all(type(reason) is str for reason in reasons)
