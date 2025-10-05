from __future__ import annotations

import pytest
import re
from typing import cast

from lib_log_rich import cli_stresstest as stresstest_module
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

_parse_dump_filters = getattr(stresstest_module, "_parse_dump_filters")


def test_parse_dump_filters_exact() -> None:
    result = _parse_dump_filters("job=worker", "Filters")
    assert result == {"job": "worker"}


def test_parse_dump_filters_contains_and_regex() -> None:
    result = _parse_dump_filters("user~contains:admin,service~regex:^api$", "Filters")
    assert result is not None
    assert result["user"] == {"contains": "admin"}
    service_spec = result["service"]
    assert isinstance(service_spec, dict)
    pattern = cast(re.Pattern[str], service_spec["pattern"])
    assert service_spec["regex"] is True
    assert pattern.pattern == "^api$"


def test_parse_dump_filters_accumulates_same_key() -> None:
    result = _parse_dump_filters("job=worker,job~icontains:batch", "Filters")
    assert result is not None
    job_spec = result["job"]
    assert isinstance(job_spec, list)
    assert job_spec[0] == "worker"
    assert job_spec[1] == {"icontains": "batch"}


def test_parse_dump_filters_invalid_entry_raises() -> None:
    with pytest.raises(ValueError):
        _parse_dump_filters("invalid_entry", "Filters")


def test_parse_dump_filters_blank_returns_none() -> None:
    assert _parse_dump_filters("", "Filters") is None


def test_console_theme_choices_match_palettes() -> None:
    choices = getattr(stresstest_module, "_CHOICE_FIELDS")["console_theme"]
    available_values = {value for _label, value in choices if value}
    assert choices[0] == ("Runtime default", "")
    assert available_values == set(CONSOLE_STYLE_THEMES.keys())
