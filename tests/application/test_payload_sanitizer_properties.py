from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hypothesis import given, settings
from hypothesis import strategies as st

from lib_log_rich.application.use_cases._payload_sanitizer import PayloadSanitizer
from lib_log_rich.domain import json_codec
from lib_log_rich.domain.context import LogContext
from lib_log_rich.runtime import PayloadLimits
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from hypothesis.strategies import SearchStrategy

    from lib_log_rich.application.use_cases._types import DiagnosticPayload
    from lib_log_rich.domain.enums import DiagnosticEvent

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


def _build_sanitizer(**overrides: Any) -> tuple[PayloadSanitizer, list[tuple[DiagnosticEvent, DiagnosticPayload]]]:
    diagnostics: list[tuple[DiagnosticEvent, DiagnosticPayload]] = []
    limits = PayloadLimits(**overrides)

    def recorder(event_name: DiagnosticEvent, payload: DiagnosticPayload) -> None:
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
        encoded = _emitted_size(sanitized)
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


def _emitted_size(payload: dict[str, Any]) -> int:
    """UTF-8 size of ``payload`` as the active codec emits it, a refused value as its text."""
    try:
        return len(json_codec.dumps(payload, default=str).encode())
    except TypeError:
        return len(json_codec.dumps({key: str(value) for key, value in payload.items()}).encode())


def test_total_bytes_counts_an_unserialisable_value_as_its_json_text() -> None:
    """A value JSON cannot encode is measured as the escaped text it is emitted as.

    Pinned from a Hypothesis counterexample: bytes nested in an extra were
    measured by Python's repr of the whole entry (fewer bytes than the JSON
    output, where each backslash is escaped), so the payload overshot the limit.
    """
    extra = {"first": "kept", "blob": {"": [b"\x00" * 18]}}
    emitted = _emitted_size(extra)
    sanitizer, diagnostics = _build_sanitizer(extra_max_keys=6, extra_max_depth=2, extra_max_value_chars=200, extra_max_total_bytes=emitted - 1)

    sanitized, _exc_info, _stack_info = sanitizer.sanitize_extra(extra, event_id="evt", logger_name="tests")

    assert _emitted_size(sanitized) <= emitted - 1
    assert "first" in sanitized
    assert diagnostics, "trimming for size must be reported"


def test_total_bytes_measures_an_int_json_cannot_hold_as_its_text() -> None:
    """An int beyond 64 bits is measured as the codec emits it.

    orjson refuses it even with a default, so it is measured as its quoted digits;
    stdlib json (a free-threaded build) writes the digits themselves.
    """
    huge = 2**70
    extra = {"first": "kept", "huge": huge}
    emitted = _emitted_size(extra)
    sanitizer, _diagnostics = _build_sanitizer(extra_max_keys=6, extra_max_depth=2, extra_max_value_chars=200, extra_max_total_bytes=emitted - 1)

    sanitized, _exc_info, _stack_info = sanitizer.sanitize_extra(extra, event_id="evt", logger_name="tests")

    assert "huge" not in sanitized
    assert sanitized["first"] == "kept"
