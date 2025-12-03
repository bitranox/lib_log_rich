from __future__ import annotations

import multiprocessing as mp
from dataclasses import asdict
from typing import Any, cast

import pytest

from lib_log_rich.domain.context import ContextBinder, LogContext
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def make_context(**overrides: object) -> LogContext:
    """Return a baseline context for the tests."""
    defaults: dict[str, object] = {
        "service": "svc",
        "environment": "test",
        "job_id": "job-1",
    }
    defaults.update(overrides)
    return LogContext(**defaults)  # type: ignore[arg-type]


def _child_process(queue: mp.Queue[Any], serialized: dict[str, Any]) -> None:
    binder = ContextBinder()
    binder.deserialize(serialized)
    current = binder.current()
    if current is None:
        raise AssertionError("ContextBinder.current() returned None in child process")
    queue.put(asdict(current))


def collect_child_payload(serialized: dict[str, Any]) -> dict[str, Any]:
    queue: mp.Queue[Any] = mp.Queue()
    process = mp.Process(target=_child_process, args=(queue, serialized))
    process.start()
    process.join(timeout=2)
    if process.exitcode != 0:
        raise AssertionError("Child process failed to propagate context")
    return cast(dict[str, Any], queue.get(timeout=2))


def test_log_context_rejects_blank_service() -> None:
    with pytest.raises(ValueError):
        make_context(service="")


def test_log_context_rejects_blank_environment() -> None:
    with pytest.raises(ValueError):
        make_context(environment="")


def test_log_context_rejects_blank_job_id() -> None:
    with pytest.raises(ValueError):
        make_context(job_id="")


def test_log_context_rejects_none_service() -> None:
    with pytest.raises(ValueError):
        make_context(service=None)  # type: ignore[arg-type]


def test_log_context_rejects_none_environment() -> None:
    with pytest.raises(ValueError):
        make_context(environment=None)  # type: ignore[arg-type]


def test_log_context_rejects_none_job_id() -> None:
    with pytest.raises(ValueError):
        make_context(job_id=None)  # type: ignore[arg-type]


def test_log_context_defaults_request_id_to_none() -> None:
    assert make_context().request_id is None


def test_log_context_defaults_user_id_to_none() -> None:
    assert make_context().user_id is None


def test_log_context_defaults_user_name_to_none() -> None:
    assert make_context().user_name is None


def test_log_context_defaults_hostname_to_none() -> None:
    assert make_context().hostname is None


def test_log_context_defaults_process_id_to_none() -> None:
    assert make_context().process_id is None


def test_log_context_defaults_trace_id_to_none() -> None:
    assert make_context().trace_id is None


def test_log_context_defaults_span_id_to_none() -> None:
    assert make_context().span_id is None


def test_log_context_defaults_extra_to_empty_dict() -> None:
    assert make_context().extra == {}


def test_log_context_to_dict_includes_service() -> None:
    payload = make_context(request_id="req").to_dict()
    assert payload["service"] == "svc"


def test_log_context_to_dict_excludes_trace_id_when_none() -> None:
    payload = make_context().to_dict()
    assert "trace_id" not in payload


def test_log_context_to_dict_excludes_user_name_when_none() -> None:
    payload = make_context().to_dict()
    assert "user_name" not in payload


def test_log_context_to_dict_excludes_hostname_when_none() -> None:
    payload = make_context().to_dict()
    assert "hostname" not in payload


def test_log_context_to_dict_excludes_process_id_when_none() -> None:
    payload = make_context().to_dict()
    assert "process_id" not in payload


def test_log_context_to_dict_preserves_extra() -> None:
    payload = make_context(extra={"feature": "search"}).to_dict()
    assert payload["extra"] == {"feature": "search"}


def test_log_context_to_dict_with_include_none_carries_user_name() -> None:
    payload = make_context(user_name="alice").to_dict(include_none=True)
    assert payload["user_name"] == "alice"


def test_log_context_to_dict_with_include_none_carries_hostname() -> None:
    payload = make_context(hostname="api01").to_dict(include_none=True)
    assert payload["hostname"] == "api01"


def test_log_context_to_dict_with_include_none_carries_process_id() -> None:
    payload = make_context(process_id=4321).to_dict(include_none=True)
    assert payload["process_id"] == 4321


def test_context_binder_current_is_none_initially() -> None:
    binder = ContextBinder()
    assert binder.current() is None


def test_context_binder_bind_pushes_context() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1") as ctx:
        assert binder.current() is ctx


def test_context_binder_bind_pops_context_on_exit() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1"):
        pass
    assert binder.current() is None


def test_context_binder_nested_binding_overrides_request_id() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="root"):
        with binder.bind(request_id="child"):
            current = binder.current()
            assert current is not None
            assert current.request_id == "child"


def test_context_binder_nested_binding_restores_request_id() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="root"):
        with binder.bind(request_id="child"):
            pass
        current = binder.current()
        assert current is not None
        assert current.request_id == "root"


def test_context_binder_nested_binding_overrides_user_id() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1"):
        with binder.bind(user_id="user-42"):
            current = binder.current()
            assert current is not None
            assert current.user_id == "user-42"


def test_context_binder_nested_binding_restores_user_id() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1"):
        with binder.bind(user_id="user-42"):
            pass
        current = binder.current()
        assert current is not None
        assert current.user_id is None


def test_context_binder_requires_required_fields_without_parent() -> None:
    binder = ContextBinder()
    with pytest.raises(ValueError, match="Missing required context fields"):
        with binder.bind(environment="prod", job_id="job-1"):
            raise AssertionError("Context should not bind when required fields are missing")


def test_context_binder_infers_process_chain_for_child_scope() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="prod", job_id="root"):
        with binder.bind(process_id=999) as child:
            assert child.process_id_chain == (999,)


def test_context_binder_serialize_roundtrip_preserves_service() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1"):
        serialized = binder.serialize()
    new_binder = ContextBinder()
    new_binder.deserialize(serialized)
    current = new_binder.current()
    assert current is not None
    assert current.service == "svc"


def test_context_binder_serialize_roundtrip_preserves_request_id() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="req-9"):
        serialized = binder.serialize()
    new_binder = ContextBinder()
    new_binder.deserialize(serialized)
    current = new_binder.current()
    assert current is not None
    assert current.request_id == "req-9"


def test_context_binder_propagates_to_child_process_preserves_service() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="req-99"):
        serialized = binder.serialize()
    child_data = collect_child_payload(serialized)
    assert child_data["service"] == "svc"


def test_context_binder_propagates_to_child_process_preserves_request_id() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="req-99"):
        serialized = binder.serialize()
    child_data = collect_child_payload(serialized)
    assert child_data["request_id"] == "req-99"
