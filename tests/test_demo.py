from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

import lib_log_rich.demo as demo_module
from lib_log_rich.demo import logdemo
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def test_resolve_demo_theme_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unknown console theme"):
        cast(Any, demo_module)._resolve_demo_theme("unknown-theme")


def test_demo_identity_falls_back_to_defaults() -> None:
    service, environment = cast(Any, demo_module)._demo_identity(None, None, "classic")
    assert (service, environment) == ("logdemo", "demo-classic")


def test_demo_graylog_endpoint_provides_default_when_enabled() -> None:
    assert cast(Any, demo_module)._demo_graylog_endpoint(True, None) == ("127.0.0.1", 12201)


def test_demo_render_dump_returns_none_without_format() -> None:
    payload = cast(Any, demo_module)._demo_render_dump(
        dump_format=None,
        dump_path=None,
        color=None,
        dump_format_preset=None,
        dump_format_template=None,
        theme="classic",
        styles={},
    )
    assert payload is None


def test_logdemo_rejects_when_runtime_already_initialised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("lib_log_rich.demo.is_initialised", lambda: True)
    with pytest.raises(RuntimeError, match="requires lib_log_rich to be uninitialised"):
        logdemo()


def test_logdemo_emits_events_for_classic_theme() -> None:
    result = logdemo(theme="classic")
    assert len(result.events) == 5


def test_logdemo_writes_dump_to_target_path(tmp_path: Path) -> None:
    target = tmp_path / "demo.json"
    logdemo(theme="classic", dump_format="json", dump_path=target)
    assert target.exists()
