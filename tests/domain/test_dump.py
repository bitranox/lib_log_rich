from __future__ import annotations

import pytest

from lib_log_rich.domain.dump import DumpFormat


def test_dump_format_from_name_accepts_known_values() -> None:
    assert DumpFormat.from_name("text") is DumpFormat.TEXT
    assert DumpFormat.from_name("JSON") is DumpFormat.JSON
    assert DumpFormat.from_name("Html") is DumpFormat.HTML


def test_dump_format_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        DumpFormat.from_name("yaml")
