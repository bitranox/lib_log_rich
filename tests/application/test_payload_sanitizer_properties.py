from __future__ import annotations

from typing import Any

import orjson
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from lib_log_rich.application.use_cases._payload_sanitizer import PayloadSanitizer
from lib_log_rich.application.use_cases._types import DiagnosticPayload
from lib_log_rich.domain.context import LogContext
from lib_log_rich.runtime import PayloadLimits
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def _build_value_strategy() -> SearchStrategy[Any]:
    scalar = st.one_of(
        st.none(),
        st.booleans(),
        st.integers(-10_000, 10_000),
        st.floats(allow_nan=False, allow_infinity=False),
        st.text(max_size=32),
        st.binary(max_size=64),
    )
    return st.recursive(
        scalar,
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(st.text(max_size=8), children, max_size=4),
            st.lists(children, max_size=4),
        ),
        max_leaves=12,
    )


def _build_sanitizer(**overrides: Any) -> tuple[PayloadSanitizer, list[tuple[str, DiagnosticPayload]]]:
    diagnostics: list[tuple[str, DiagnosticPayload]] = []
    limits = PayloadLimits(**overrides)

    def recorder(event_name: str, payload: DiagnosticPayload) -> None:
        diagnostics.append((event_name, payload))

    return PayloadSanitizer(limits, recorder), diagnostics


_key_strategy = st.one_of(st.text(max_size=8), st.integers(-50, 50))
VALUE_STRATEGY = _build_value_strategy()


@settings(max_examples=100, deadline=None)
@given(
    extra=st.dictionaries(_key_strategy, VALUE_STRATEGY, max_size=25),
    extra_max_keys=st.integers(min_value=1, max_value=10),
    extra_max_depth=st.integers(min_value=1, max_value=5),
    extra_max_value_chars=st.integers(min_value=16, max_value=512),
    extra_max_total_bytes=st.one_of(st.none(), st.integers(min_value=128, max_value=4096)),
)
def test_sanitize_extra_respects_limits(
    extra: dict[Any, Any],
    extra_max_keys: int,
    extra_max_depth: int,
    extra_max_value_chars: int,
    extra_max_total_bytes: int | None,
) -> None:
    sanitizer, diagnostics = _build_sanitizer(
        extra_max_keys=extra_max_keys,
        extra_max_depth=extra_max_depth,
        extra_max_value_chars=extra_max_value_chars,
        extra_max_total_bytes=extra_max_total_bytes,
    )

    sanitized, exc_info, stack_info = sanitizer.sanitize_extra(extra, event_id="evt", logger_name="tests")
    baseline_extra = {str(key): value for key, value in extra.items() if str(key) not in {"exc_info", "stack_info"}}

    assert len(sanitized) <= extra_max_keys
    assert exc_info is None or isinstance(exc_info, str)
    assert stack_info is None or isinstance(stack_info, str)

    if extra_max_total_bytes is not None:
        encoded = len(orjson.dumps(sanitized, default=str))
        assert encoded <= extra_max_total_bytes

    for value in sanitized.values():
        if isinstance(value, str):
            assert len(value) <= extra_max_value_chars

    payload_changed = sanitized != baseline_extra
    if payload_changed:
        assert diagnostics, "expected diagnostics when payload changes"


@settings(max_examples=75, deadline=None)
@given(
    context_extra=st.dictionaries(_key_strategy, VALUE_STRATEGY, max_size=25),
    context_max_keys=st.integers(min_value=1, max_value=12),
    context_max_value_chars=st.integers(min_value=8, max_value=256),
    extra_max_depth=st.integers(min_value=1, max_value=5),
)
def test_sanitize_context_respects_limits(
    context_extra: dict[Any, Any],
    context_max_keys: int,
    context_max_value_chars: int,
    extra_max_depth: int,
) -> None:
    sanitizer, _ = _build_sanitizer(
        context_max_keys=context_max_keys,
        context_max_value_chars=context_max_value_chars,
        extra_max_depth=extra_max_depth,
    )

    context = LogContext(service="svc", environment="env", job_id="job", extra=context_extra)
    updated, changed = sanitizer.sanitize_context(context, event_id="evt", logger_name="tests")

    assert len(updated.extra) <= context_max_keys
    for value in updated.extra.values():
        if isinstance(value, str):
            assert len(value) <= context_max_value_chars

    if not changed:
        assert updated.extra == context.extra
