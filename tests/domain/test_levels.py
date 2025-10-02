from __future__ import annotations

import logging

import pytest

from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("debug", LogLevel.DEBUG),
        ("INFO", LogLevel.INFO),
        ("Warning", LogLevel.WARNING),
        ("error", LogLevel.ERROR),
        ("CRITICAL", LogLevel.CRITICAL),
    ],
)
def test_level_name_recognises_case_insensitive_matches(name: str, expected: LogLevel) -> None:
    assert LogLevel.from_name(name) is expected


def test_level_name_rejects_unknown_label() -> None:
    with pytest.raises(ValueError, match="Unknown log level"):
        LogLevel.from_name("verbose")


@pytest.mark.parametrize(
    "number, expected",
    [
        (10, LogLevel.DEBUG),
        (20, LogLevel.INFO),
        (30, LogLevel.WARNING),
        (40, LogLevel.ERROR),
        (50, LogLevel.CRITICAL),
    ],
)
def test_numeric_value_maps_standard_levels(number: int, expected: LogLevel) -> None:
    assert LogLevel.from_numeric(number) is expected


@pytest.mark.parametrize("number", [-5, 5, 15, 25, 35, 45, 55])
def test_numeric_value_rejects_out_of_band_levels(number: int) -> None:
    with pytest.raises(ValueError, match="Unsupported log level numeric"):
        LogLevel.from_numeric(number)


@pytest.mark.parametrize(
    "level, expected",
    [
        (logging.DEBUG, LogLevel.DEBUG),
        (logging.INFO, LogLevel.INFO),
        (logging.WARNING, LogLevel.WARNING),
        (logging.ERROR, LogLevel.ERROR),
        (logging.CRITICAL, LogLevel.CRITICAL),
    ],
)
def test_python_logging_level_translates_to_domain_level(level: int, expected: LogLevel) -> None:
    assert LogLevel.from_python_level(level) is expected


@pytest.mark.parametrize("level", LogLevel)
def test_domain_level_reports_python_constant(level: LogLevel) -> None:
    assert level.to_python_level() == getattr(logging, level.name)


@pytest.mark.parametrize(
    "level, icon",
    [
        (LogLevel.DEBUG, "🐞"),
        (LogLevel.INFO, "ℹ"),
        (LogLevel.WARNING, "⚠"),
        (LogLevel.ERROR, "✖"),
        (LogLevel.CRITICAL, "☠"),
    ],
)
def test_level_icon_matches_table(level: LogLevel, icon: str) -> None:
    assert level.icon == icon


@pytest.mark.parametrize(
    "level, code",
    [
        (LogLevel.DEBUG, "DEBG"),
        (LogLevel.INFO, "INFO"),
        (LogLevel.WARNING, "WARN"),
        (LogLevel.ERROR, "ERRO"),
        (LogLevel.CRITICAL, "CRIT"),
    ],
)
def test_level_code_matches_table(level: LogLevel, code: str) -> None:
    assert level.code == code


@pytest.mark.parametrize(
    "level, severity",
    [
        (LogLevel.DEBUG, "debug"),
        (LogLevel.INFO, "info"),
        (LogLevel.WARNING, "warning"),
        (LogLevel.ERROR, "error"),
        (LogLevel.CRITICAL, "critical"),
    ],
)
def test_level_severity_matches_lowercase_name(level: LogLevel, severity: str) -> None:
    assert level.severity == severity
