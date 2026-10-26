"""init() persists the ring buffer to a JSONL checkpoint when one is configured.

Without ``ring_buffer_checkpoint`` (or ``LOG_RING_BUFFER_CHECKPOINT``) the
ring-buffer flushes in ``flush(flush_ring_buffer=True)``, ``shutdown()`` and
after a dump have nowhere to write; these tests drive each through ``init()``.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import TYPE_CHECKING, Any

import pytest

from lib_log_rich import bind, dump, flush, getLogger, shutdown
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.runtime import RuntimeConfig, init, is_initialised, validate_config
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = [OS_AGNOSTIC]


@pytest.fixture(autouse=True)
def no_ambient_log_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith("LOG_")]:
        monkeypatch.delenv(name)


@pytest.fixture(autouse=True)
def no_runtime_left_behind() -> Iterator[None]:
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            shutdown()


def _config(**overrides: Any) -> RuntimeConfig:
    base: dict[str, Any] = {"service": "svc", "environment": "test", "queue_enabled": False, "console_stream": "none"}
    base.update(overrides)
    return RuntimeConfig(**base)


def _log(message: str) -> None:
    with bind(job_id="checkpoint"):
        getLogger("tests.checkpoint").info(message)


def _stored_messages(path: Path) -> list[str]:
    return [json.loads(line)["message"] for line in path.read_text(encoding="utf-8").splitlines()]


def test_flush_with_ring_buffer_persists_to_the_configured_checkpoint(tmp_path: Path) -> None:
    store = tmp_path / "ring.jsonl"
    init(_config(ring_buffer_checkpoint=store))
    _log("persist me")

    flush(flush_ring_buffer=True)

    assert _stored_messages(store) == ["persist me"]


def test_shutdown_persists_the_ring_buffer_to_the_checkpoint(tmp_path: Path) -> None:
    store = tmp_path / "ring.jsonl"
    init(_config(ring_buffer_checkpoint=store))
    _log("kept at shutdown")

    shutdown()

    assert _stored_messages(store) == ["kept at shutdown"]


def test_a_new_runtime_starts_from_the_events_its_checkpoint_holds(tmp_path: Path) -> None:
    store = tmp_path / "ring.jsonl"
    init(_config(ring_buffer_checkpoint=store))
    _log("from the first run")
    shutdown()

    init(_config(ring_buffer_checkpoint=store))
    events = json.loads(dump(dump_format=DumpFormat.JSON))

    assert [event["message"] for event in events] == ["from the first run"]


def test_the_environment_variable_beats_the_config_field(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from_config = tmp_path / "config.jsonl"
    from_env = tmp_path / "env.jsonl"
    monkeypatch.setenv("LOG_RING_BUFFER_CHECKPOINT", str(from_env))
    init(_config(ring_buffer_checkpoint=from_config))
    _log("routed by the environment")

    shutdown()

    assert _stored_messages(from_env) == ["routed by the environment"]
    assert not from_config.exists()


@pytest.mark.parametrize("unset", ["", "   "], ids=["empty", "blank"])
def test_an_empty_setting_means_no_checkpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unset: str) -> None:
    # An empty string is what TOML and .env files spell "unset", as for the other settings.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_RING_BUFFER_CHECKPOINT", unset)
    init(_config(ring_buffer_checkpoint=unset))
    _log("stays in memory")

    shutdown()

    assert list(tmp_path.iterdir()) == []


def test_a_checkpoint_path_that_is_a_directory_is_refused_by_validate_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"^Invalid runtime settings: ring_buffer_checkpoint: .* is a directory"):
        validate_config(_config(ring_buffer_checkpoint=tmp_path))


def test_a_corrupt_checkpoint_stops_init_and_names_the_file(tmp_path: Path) -> None:
    store = tmp_path / "ring.jsonl"
    store.write_text('{"not": "an event"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"^Invalid checkpoint .*ring\.jsonl, line 1:"):
        init(_config(ring_buffer_checkpoint=store))

    assert is_initialised() is False


def test_validate_config_does_not_read_the_checkpoint_file(tmp_path: Path) -> None:
    # Reading it would load an unbounded file just to judge a config; init() reports a bad one.
    store = tmp_path / "ring.jsonl"
    store.write_text('{"not": "an event"}\n', encoding="utf-8")

    validate_config(_config(ring_buffer_checkpoint=store))
