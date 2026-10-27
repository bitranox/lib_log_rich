"""How the payload sanitizer turns ``exc_info`` and ``stack_info`` into bounded text.

A traceback arrives as an exception, a ``(type, value, traceback)`` tuple or
ready-made text. It is recorded as the text ``traceback.format_exception``
produces, compacted by FRAMES (``stacktrace_max_frames`` at each end), and,
when still longer than ``extra_max_value_chars``, cut from the START so the
innermost frames and the exception line survive.
"""

from __future__ import annotations

import builtins
import sys
import traceback
from typing import TYPE_CHECKING, Any

import pytest

from lib_log_rich.application.use_cases._payload_sanitizer import TRUNCATION_PREFIX, PayloadSanitizer
from lib_log_rich.domain.enums import DiagnosticEvent
from lib_log_rich.runtime import PayloadLimits
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from types import TracebackType

    from lib_log_rich.application.use_cases._types import DiagnosticPayload

pytestmark = [OS_AGNOSTIC]

_ROOMY = 1_000_000


def _sanitizer(**limits: Any) -> tuple[PayloadSanitizer, list[tuple[DiagnosticEvent, DiagnosticPayload]]]:
    seen: list[tuple[DiagnosticEvent, DiagnosticPayload]] = []

    def record(event: DiagnosticEvent, payload: DiagnosticPayload) -> None:
        seen.append((event, payload))

    return PayloadSanitizer(PayloadLimits(**limits), record), seen


def _recurse(depth: int) -> None:
    if depth == 0:
        raise ValueError("boom at the bottom")
    _recurse(depth - 1)


def _caught(depth: int) -> tuple[type[BaseException], BaseException, TracebackType | None]:
    try:
        _recurse(depth)
    except ValueError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        assert exc_type is not None
        assert exc_value is not None
        return exc_type, exc_value, exc_tb
    raise AssertionError("unreachable")


def _frame_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.lstrip().startswith('File "')]


def test_an_exc_info_tuple_is_recorded_as_the_formatted_traceback() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY)
    info = _caught(2)

    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=info)

    assert exc_text == "".join(traceback.format_exception(*info)).rstrip("\n")


def test_an_exception_instance_is_recorded_as_the_formatted_traceback() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY)
    _, exc_value, _ = _caught(1)

    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=exc_value)

    assert exc_text is not None
    assert exc_text.startswith("Traceback (most recent call last):")
    assert exc_text.endswith("ValueError: boom at the bottom")


def test_traceback_text_given_as_a_string_is_kept_as_is() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY)

    _, exc_text, _ = sanitizer.sanitize_extra({"exc_info": "already formatted"}, event_id="evt", logger_name="t")

    assert exc_text == "already formatted"


def test_frame_compaction_keeps_the_first_and_last_frames_and_the_exception_line() -> None:
    sanitizer, seen = _sanitizer(extra_max_value_chars=_ROOMY, stacktrace_max_frames=2)
    info = _caught(30)
    full = "".join(traceback.format_exception(*info))
    all_frames = _frame_lines(full)

    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=info)

    assert exc_text is not None
    removed = len(all_frames) - 4
    assert _frame_lines(exc_text) == [*all_frames[:2], *all_frames[-2:]]
    assert f"... truncated {removed} frame(s) ..." in exc_text
    assert exc_text.startswith("Traceback (most recent call last):")
    assert exc_text.endswith("ValueError: boom at the bottom")
    assert (DiagnosticEvent.EXC_INFO_TRUNCATED, {"event_id": "evt", "logger": "t", "frames_removed": removed}) in seen


def test_a_traceback_within_the_frame_limit_is_not_compacted() -> None:
    sanitizer, seen = _sanitizer(extra_max_value_chars=_ROOMY, stacktrace_max_frames=10)
    info = _caught(3)

    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=info)

    assert exc_text == "".join(traceback.format_exception(*info)).rstrip("\n")
    assert seen == []


def test_stack_info_frames_are_compacted_by_frame_not_by_line() -> None:
    sanitizer, seen = _sanitizer(extra_max_value_chars=_ROOMY, stacktrace_max_frames=1)
    stack = "Stack (most recent call last):\n" + "".join(f'  File "m.py", line {n}, in f{n}\n    f{n + 1}()\n' for n in range(5))

    _, _, stack_text = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", stack_info=stack)

    assert stack_text is not None
    assert _frame_lines(stack_text) == ['  File "m.py", line 0, in f0', '  File "m.py", line 4, in f4']
    assert "    f1()" in stack_text
    assert "    f5()" in stack_text
    assert "... truncated 3 frame(s) ..." in stack_text
    assert (DiagnosticEvent.STACK_INFO_TRUNCATED, {"event_id": "evt", "logger": "t", "frames_removed": 3}) in seen


def test_an_over_long_traceback_keeps_its_end_and_is_marked_at_the_start() -> None:
    sanitizer, seen = _sanitizer(extra_max_value_chars=120, stacktrace_max_frames=10)
    info = _caught(4)
    full = "".join(traceback.format_exception(*info)).rstrip("\n")

    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=info)

    assert exc_text is not None
    assert len(exc_text) == 120
    assert exc_text.startswith(TRUNCATION_PREFIX)
    assert exc_text.endswith("ValueError: boom at the bottom")
    assert exc_text[len(TRUNCATION_PREFIX) :] == full[-(120 - len(TRUNCATION_PREFIX)) :]
    payload = {"event_id": "evt", "logger": "t", "reason": "length", "original_length": len(full), "new_length": 120, "key": "exc_info"}
    assert (DiagnosticEvent.EXC_INFO_TRUNCATED, payload) in seen


def _caught_chain() -> BaseException:
    try:
        try:
            _recurse(4)
        except ValueError as cause:
            raise RuntimeError("wrapper") from cause
    except RuntimeError as exc:
        return exc
    raise AssertionError("unreachable")


def test_compaction_keeps_every_exception_line_of_a_chain() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY, stacktrace_max_frames=1)
    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=_caught_chain())

    assert exc_text is not None
    assert "ValueError: boom at the bottom" in exc_text
    assert "The above exception was the direct cause of the following exception:" in exc_text
    assert exc_text.count("Traceback (most recent call last):") == 2
    assert exc_text.endswith("RuntimeError: wrapper")


def test_compaction_keeps_the_group_line_and_every_sub_exception_line() -> None:
    group_type: Any = getattr(builtins, "ExceptionGroup", None)
    if group_type is None:
        pytest.skip("ExceptionGroup needs Python 3.11+")
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY, stacktrace_max_frames=1)
    subs = [_caught_with(3, ValueError(message)) for message in ("sub one", "sub two")]
    group = _caught_with(0, group_type("the group", subs))
    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=group)

    assert exc_text is not None
    assert "ExceptionGroup: the group (2 sub-exceptions)" in exc_text
    assert "ValueError: sub one" in exc_text
    assert "ValueError: sub two" in exc_text
    markers = [line for line in exc_text.splitlines() if "... truncated" in line]
    # Every frame of a group traceback sits behind the "|" gutter, so the markers that replace them do too.
    assert markers
    assert all(line.split("...")[0].strip().startswith("|") for line in markers)


def _caught_with(depth: int, error: BaseException) -> BaseException:
    try:
        _recurse_with(depth, error)
    except BaseException as exc:
        return exc
    raise AssertionError("unreachable")


def test_a_repeat_line_goes_with_the_frame_it_repeats() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY, stacktrace_max_frames=1)
    text = "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "m.py", line 1, in top',
            "    loop()",
            '  File "m.py", line 2, in loop',
            "    loop()",
            "  [Previous line repeated 27 more times]",
            '  File "m.py", line 3, in bottom',
            "    raise ValueError",
            "ValueError",
        ]
    )

    _, exc_text, _ = sanitizer.sanitize_extra({"exc_info": text}, event_id="evt", logger_name="t")

    assert exc_text is not None
    assert "Previous line repeated" not in exc_text
    assert exc_text.splitlines()[3].strip() == "... truncated 1 frame(s) ..."


def test_file_lines_inside_an_exception_message_are_not_frames() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY, stacktrace_max_frames=1)
    message = 'parse failed:\n  File "config.yaml", line 3\n    key: value\n  File "other.yaml", line 9\n    k: v'
    error = _caught_with(2, ValueError(message))
    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=error)

    assert exc_text is not None
    assert "ValueError: parse failed:" in exc_text
    assert '  File "other.yaml", line 9' in exc_text
    assert '  File "config.yaml", line 3' in exc_text
    # Frames: _caught_with, then _recurse_with three times; the first and the innermost stay.
    assert "... truncated 2 frame(s) ..." in exc_text
    assert exc_text.count(", in _recurse_with") == 1


def _recurse_with(depth: int, error: BaseException) -> None:
    if depth == 0:
        raise error
    _recurse_with(depth - 1, error)


def test_a_malformed_exc_info_tuple_in_extra_is_recorded_as_text_without_raising() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY)
    bogus = (ValueError, ValueError("x"), "not-a-traceback")

    _, exc_text, _ = sanitizer.sanitize_extra({"exc_info": bogus}, event_id="evt", logger_name="t")

    assert exc_text is not None
    assert "not-a-traceback" in exc_text


def test_an_empty_exc_info_tuple_records_no_exception() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=_ROOMY)

    _, exc_text, _ = sanitizer.sanitize_extra({}, event_id="evt", logger_name="t", exc_info=(None, None, None))

    assert exc_text is None


def test_a_limit_shorter_than_the_marker_keeps_a_marker_prefix() -> None:
    sanitizer, _ = _sanitizer(extra_max_value_chars=5)

    _, exc_text, _ = sanitizer.sanitize_extra({"exc_info": "x" * 50}, event_id="evt", logger_name="t")

    assert exc_text == TRUNCATION_PREFIX[:5]
