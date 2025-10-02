from __future__ import annotations

import pytest

from lib_log_rich.domain.dump import DumpFormat
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


@pytest.mark.parametrize(
    "name",
    ["text", "TEXT"],
)
def test_dump_format_name_resolves_to_text(name: str) -> None:
    assert DumpFormat.from_name(name) is DumpFormat.TEXT


@pytest.mark.parametrize(
    "name",
    ["json", "JSON"],
)
def test_dump_format_name_resolves_to_json(name: str) -> None:
    assert DumpFormat.from_name(name) is DumpFormat.JSON


@pytest.mark.parametrize(
    "name",
    ["html_table", "HTML", "Html_Table"],
)
def test_dump_format_name_resolves_to_html_table(name: str) -> None:
    assert DumpFormat.from_name(name) is DumpFormat.HTML_TABLE


@pytest.mark.parametrize(
    "name",
    ["html_txt", "HTML_TXT"],
)
def test_dump_format_name_resolves_to_html_txt(name: str) -> None:
    assert DumpFormat.from_name(name) is DumpFormat.HTML_TXT


def test_dump_format_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        DumpFormat.from_name("yaml")
