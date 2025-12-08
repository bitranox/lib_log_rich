from __future__ import annotations

from typing import Any

import pytest

from lib_log_rich.application.use_cases._payload_sanitizer import (
    TRUNCATION_SUFFIX,
    PayloadSanitizer,
)
from lib_log_rich.application.use_cases._types import DiagnosticPayload
from lib_log_rich.domain.context import LogContext
from lib_log_rich.runtime import PayloadLimits
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def _collecting_sanitizer(**limit_overrides: Any) -> tuple[PayloadSanitizer, list[tuple[str, DiagnosticPayload]]]:
    diagnostics: list[tuple[str, DiagnosticPayload]] = []
    limits = PayloadLimits(**limit_overrides) if limit_overrides else PayloadLimits()

    def recorder(event_name: str, payload: DiagnosticPayload) -> None:
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
