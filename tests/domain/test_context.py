from __future__ import annotations

import multiprocessing as mp
from dataclasses import asdict
from typing import Any

import pytest

from lib_log_rich.domain.context import ContextBinder, LogContext


def _child_process(queue: mp.Queue, serialized: dict[str, Any]) -> None:
    binder = ContextBinder()
    binder.deserialize(serialized)
    queue.put(asdict(binder.current()))


def test_log_context_requires_core_fields() -> None:
    with pytest.raises(ValueError):
        LogContext(service="", environment="test", job_id="job")
    with pytest.raises(ValueError):
        LogContext(service="svc", environment="", job_id="job")
    with pytest.raises(ValueError):
        LogContext(service="svc", environment="test", job_id="")


def test_log_context_defaults_optional_fields() -> None:
    ctx = LogContext(service="svc", environment="test", job_id="job-1")
    assert ctx.request_id is None
    assert ctx.user_id is None
    assert ctx.user_name is None
    assert ctx.hostname is None
    assert ctx.process_id is None
    assert ctx.trace_id is None
    assert ctx.span_id is None
    assert ctx.extra == {}


def test_log_context_to_dict_filters_none() -> None:
    ctx = LogContext(
        service="svc",
        environment="test",
        job_id="job-1",
        request_id="req-123",
        user_id="user-9",
        extra={"feature": "search"},
    )
    data = ctx.to_dict()
    assert data["service"] == "svc"
    assert "trace_id" not in data
    assert "user_name" not in data
    assert "hostname" not in data
    assert "process_id" not in data
    assert data["extra"] == {"feature": "search"}


def test_log_context_to_dict_includes_system_fields() -> None:
    ctx = LogContext(
        service="svc",
        environment="test",
        job_id="job-1",
        user_name="alice",
        hostname="api01",
        process_id=4321,
    )
    data = ctx.to_dict(include_none=True)
    assert data["user_name"] == "alice"
    assert data["hostname"] == "api01"
    assert data["process_id"] == 4321


def test_context_binder_bind_and_current_restore() -> None:
    binder = ContextBinder()
    assert binder.current() is None

    with binder.bind(service="svc", environment="test", job_id="job-1") as ctx:
        assert ctx.service == "svc"
        assert binder.current() is ctx

    assert binder.current() is None


def test_context_binder_nested_bindings_override_fields() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="root"):
        assert binder.current().request_id == "root"
        with binder.bind(request_id="child", user_id="user-42"):
            ctx = binder.current()
            assert ctx.request_id == "child"
            assert ctx.user_id == "user-42"

        assert binder.current().request_id == "root"
        assert binder.current().user_id is None


def test_context_binder_serialize_deserialize_roundtrip() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="req-9"):
        serialized = binder.serialize()

    new_binder = ContextBinder()
    new_binder.deserialize(serialized)
    restored = new_binder.current()
    assert restored is not None
    assert restored.service == "svc"
    assert restored.request_id == "req-9"


def test_context_binder_propagates_to_child_process() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="req-99"):
        serialized = binder.serialize()

    queue: mp.Queue = mp.Queue()  # type: ignore[assignment]
    process = mp.Process(target=_child_process, args=(queue, serialized))
    process.start()
    process.join(timeout=2)
    assert process.exitcode == 0
    child_data = queue.get(timeout=2)
    assert child_data["service"] == "svc"
    assert child_data["request_id"] == "req-99"
