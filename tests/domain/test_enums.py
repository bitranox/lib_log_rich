"""The domain's string enums: one string form on every supported Python.

A ``str, Enum`` member formats as its value on Python 3.10 but as
``Name.MEMBER`` on 3.11+, so a member that reaches a log line, a filename or a
caller's f-string would change shape across the version matrix. Every string
enum here formats as its value, the way ``enum.StrEnum`` does on 3.11+.
"""

from __future__ import annotations

from enum import Enum

import pytest

from lib_log_rich.domain import enums as enums_module
from lib_log_rich.domain.enums import (
    ConsoleStream,
    ConsoleTheme,
    DiagnosticEvent,
    DropReason,
    ExportStyle,
    FormatPreset,
    GraylogProtocol,
    QueuePolicy,
)
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

_STRING_ENUMS: tuple[type[Enum], ...] = (ConsoleStream, ConsoleTheme, DiagnosticEvent, DropReason, ExportStyle, FormatPreset, GraylogProtocol, QueuePolicy)


@pytest.mark.parametrize("enum_type", _STRING_ENUMS, ids=lambda enum_type: enum_type.__name__)
def test_every_member_formats_as_its_value(enum_type: type[Enum]) -> None:
    for member in enum_type:
        assert str(member) == member.value
        assert f"{member}" == member.value
        assert f"[{member:>20}]" == f"[{member.value:>20}]"


@pytest.mark.parametrize("enum_type", _STRING_ENUMS, ids=lambda enum_type: enum_type.__name__)
def test_every_member_still_equals_its_value(enum_type: type[Enum]) -> None:
    for member in enum_type:
        assert member == member.value
        assert hash(member) == hash(member.value)


def test_every_string_enum_in_the_module_is_covered() -> None:
    declared = {obj for obj in vars(enums_module).values() if isinstance(obj, type) and issubclass(obj, Enum) and issubclass(obj, str)}
    declared.discard(enums_module.StrValueEnum)
    assert declared == set(_STRING_ENUMS)


def test_diagnostic_event_names_are_the_documented_ones() -> None:
    # DIAGNOSTIC.md documents these names to diagnostic-hook authors; a member
    # added, renamed or dropped here is a change to that public contract.
    assert {member.value for member in DiagnosticEvent} == {
        "adapter_error",
        "context_extra_keys_dropped",
        "context_extra_total_trimmed",
        "context_extra_value_truncated",
        "context_extra_value_truncated_depth_collapsed",
        "emitted",
        "exc_info_truncated",
        "extra_invalid",
        "extra_keys_dropped",
        "extra_total_trimmed",
        "extra_value_truncated",
        "extra_value_truncated_depth_collapsed",
        "message_format_failed",
        "message_truncated",
        "queue_degraded_drop_mode",
        "queue_drop_callback_error",
        "queue_dropped",
        "queue_full",
        "queue_shutdown_timeout",
        "queue_worker_error",
        "queued",
        "rate_limited",
        "stack_info_truncated",
    }


def test_drop_reason_values_are_the_process_result_reasons() -> None:
    assert {member.value for member in DropReason} == {"adapter_error", "logger_level", "queue_failure", "queue_full", "rate_limited"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("full", FormatPreset.FULL),
        ("  Short ", FormatPreset.SHORT),
        ("FULL_LOC", FormatPreset.FULL_LOC),
        ("short_loc", FormatPreset.SHORT_LOC),
        ("short_loc_icon", FormatPreset.SHORT_LOC_ICON),
    ],
)
def test_format_preset_parses_a_human_entered_name(raw: str, expected: FormatPreset) -> None:
    assert FormatPreset.from_str(raw) is expected


def test_format_preset_refuses_an_unknown_name_and_lists_the_valid_ones() -> None:
    with pytest.raises(ValueError, match="'medium'") as caught:
        FormatPreset.from_str("medium")
    assert "short_loc_icon" in str(caught.value)


@pytest.mark.parametrize(
    ("raw", "expected"), [("classic", ConsoleTheme.CLASSIC), (" DARK ", ConsoleTheme.DARK), ("Neon", ConsoleTheme.NEON), ("pastel", ConsoleTheme.PASTEL)]
)
def test_console_theme_parses_a_human_entered_name(raw: str, expected: ConsoleTheme) -> None:
    assert ConsoleTheme.from_str(raw) is expected


def test_console_theme_refuses_an_unknown_name_and_lists_the_valid_ones() -> None:
    with pytest.raises(ValueError, match=r"^Invalid console theme: 'nope'; must be one of 'classic', 'dark', 'neon', 'pastel'$"):
        ConsoleTheme.from_str("nope")


@pytest.mark.parametrize(("raw", "expected"), [("ansi", ExportStyle.ANSI), (" HTML ", ExportStyle.HTML)])
def test_export_style_parses_a_human_entered_name(raw: str, expected: ExportStyle) -> None:
    assert ExportStyle.from_str(raw) is expected
