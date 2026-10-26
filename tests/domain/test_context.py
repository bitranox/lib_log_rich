from __future__ import annotations

import contextvars
import json
import multiprocessing as mp
from dataclasses import asdict, replace
from typing import Any, cast

import pytest

from lib_log_rich.domain.context import ContextBinder, ContextSnapshot, LogContext
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def make_context(**overrides: Any) -> LogContext:
    """Return a baseline context for the tests; overrides are untyped to reach invalid values."""
    return replace(LogContext(service="svc", environment="test", job_id="job-1"), **overrides)


def _child_process(queue: mp.Queue[Any], serialized: ContextSnapshot | str) -> None:
    binder = ContextBinder()
    binder.deserialize(ContextSnapshot.from_json(serialized) if isinstance(serialized, str) else serialized)
    current = binder.current()
    if current is None:
        raise AssertionError("ContextBinder.current() returned None in child process")
    queue.put(asdict(current))


def collect_child_payload(serialized: ContextSnapshot | str) -> dict[str, Any]:
    queue: mp.Queue[Any] = mp.Queue()
    process = mp.Process(target=_child_process, args=(queue, serialized))
    process.start()
    process.join(timeout=2)
    if process.exitcode != 0:
        raise AssertionError("Child process failed to propagate context")
    return cast("dict[str, Any]", queue.get(timeout=2))


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
        make_context(service=None)


def test_log_context_rejects_none_environment() -> None:
    with pytest.raises(ValueError):
        make_context(environment=None)


def test_log_context_rejects_none_job_id() -> None:
    with pytest.raises(ValueError):
        make_context(job_id=None)


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
    with binder.bind(service="svc", environment="test", job_id="job-1", request_id="root"), binder.bind(request_id="child"):
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
    with binder.bind(service="svc", environment="test", job_id="job-1"), binder.bind(user_id="user-42"):
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
    with pytest.raises(ValueError, match="Missing required context fields"), binder.bind(environment="prod", job_id="job-1"):
        raise AssertionError("Context should not bind when required fields are missing")


def test_context_binder_infers_process_chain_for_child_scope() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="prod", job_id="root"), binder.bind(process_id=999) as child:
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


def test_context_binder_propagates_json_text_to_child_process() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", extra={"tenant": "acme"}):
        text = binder.serialize().to_json()
    child_data = collect_child_payload(text)
    assert (child_data["job_id"], child_data["extra"]) == ("job-1", {"tenant": "acme"})


def test_log_context_merge_keeps_parent_values_for_none() -> None:
    parent = make_context(request_id="req", user_id="u1", extra={"a": 1})
    child = parent.merge(user_id=None, trace_id="t1")
    assert (child.request_id, child.user_id, child.trace_id, child.extra) == ("req", "u1", "t1", {"a": 1})


def test_log_context_merge_replaces_extra_and_copies_it() -> None:
    source = {"b": 2}
    child = make_context(extra={"a": 1}).merge(extra=source)
    source["c"] = 3
    assert child.extra == {"b": 2}


def test_log_context_merge_normalises_the_pid_chain_to_a_tuple() -> None:
    assert make_context().merge(process_id_chain=[1, 2]).process_id_chain == (1, 2)


def test_log_context_merge_refuses_an_unknown_field() -> None:
    with pytest.raises(TypeError):
        make_context().merge(**cast("dict[str, Any]", {"route": "/x"}))


def test_context_binder_bind_refuses_an_unknown_field_without_a_parent() -> None:
    binder = ContextBinder()
    with pytest.raises(TypeError), binder.bind(**cast("dict[str, Any]", {"service": "s", "environment": "e", "job_id": "j", "route": "/x"})):
        raise AssertionError("an unknown context field must not bind")


def test_context_binder_names_every_missing_required_field() -> None:
    binder = ContextBinder()
    with pytest.raises(ValueError, match=r"exists: service, job_id$"), binder.bind(service="", environment="prod"):
        raise AssertionError("Context should not bind when required fields are missing")


def test_context_binder_root_bind_accepts_explicit_none_extra() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="prod", job_id="1", extra=None, process_id_chain=[7, 8]) as ctx:
        assert (ctx.extra, ctx.process_id_chain) == ({}, (7, 8))


def test_context_binder_seed_replaces_the_stack_with_one_frame() -> None:
    binder = ContextBinder()
    seeded = make_context(job_id="bootstrap")
    with binder.bind(service="svc", environment="test", job_id="outer"):
        binder.seed(seeded)
        assert binder.serialize().stack == (seeded,)
    assert contextvars.Context().run(binder.current) == seeded


def test_context_binder_deserialize_no_longer_takes_a_mapping() -> None:
    binder = ContextBinder()
    with pytest.raises(AttributeError):
        binder.deserialize(cast("Any", {"version": 1, "stack": []}))


def test_context_snapshot_json_round_trip_keeps_every_field() -> None:
    frame = LogContext(
        service="svc",
        environment="prod",
        job_id="job",
        request_id="req",
        user_id="u1",
        user_name="alice",
        hostname="api01",
        process_id=11,
        process_id_chain=(10, 11),
        trace_id="t",
        span_id="s",
        extra={"nested": {"n": [1, 2]}},
    )
    snapshot = ContextSnapshot(stack=(make_context(), frame))
    assert ContextSnapshot.from_json(snapshot.to_json()) == snapshot


def test_context_snapshot_from_json_fills_absent_optional_fields() -> None:
    snapshot = ContextSnapshot.from_json('{"version": 1, "stack": [{"service": "s", "environment": "e", "job_id": "j"}]}')
    assert snapshot.stack == (LogContext(service="s", environment="e", job_id="j"),)


def test_context_snapshot_to_json_refuses_an_unencodable_extra() -> None:
    with pytest.raises(TypeError):
        ContextSnapshot(stack=(make_context(extra={"when": object()}),)).to_json()


def test_context_snapshot_refuses_an_unsupported_version() -> None:
    with pytest.raises(ValueError, match="version: unsupported version 2"):
        ContextSnapshot(version=2)


def _frame(**fields: object) -> dict[str, object]:
    return {"service": "s", "environment": "e", "job_id": "j", **fields}


def _document(*frames: object, **top: object) -> str:
    return json.dumps({"version": 1, "stack": list(frames), **top})


@pytest.mark.parametrize(
    ("text", "location", "reason"),
    [
        pytest.param("{not json", "document", "not valid JSON", id="not-json"),
        pytest.param("[]", "document", "expected an object, got list", id="top-level-array"),
        pytest.param(_document(owner="x"), "document.owner", "unknown field", id="unknown-top-level-key"),
        pytest.param('{"stack": []}', "version", "expected an integer, got null", id="missing-version"),
        pytest.param(_document(version=True), "version", "expected an integer, got bool", id="boolean-version"),
        pytest.param(_document(version=3), "version", "unsupported version 3", id="unknown-version"),
        pytest.param('{"version": 1}', "stack", "expected an array, got null", id="missing-stack"),
        pytest.param(_document("frame"), "stack[0]", "expected an object, got str", id="frame-not-object"),
        pytest.param(_document(_frame(), _frame(route="/x")), "stack[1].route", "unknown field", id="unknown-frame-key"),
        pytest.param(_document({"environment": "e", "job_id": "j"}), "stack[0].service", "expected a string, got null", id="missing-service"),
        pytest.param(_document(_frame(job_id="  ")), "stack[0].job_id", "must not be empty", id="blank-job-id"),
        pytest.param(_document(_frame(request_id=5)), "stack[0].request_id", "expected a string, got int", id="numeric-request-id"),
        pytest.param(_document(_frame(process_id="12")), "stack[0].process_id", "expected an integer, got str", id="text-process-id"),
        pytest.param(_document(_frame(process_id=False)), "stack[0].process_id", "expected an integer, got bool", id="boolean-process-id"),
        pytest.param(_document(_frame(process_id_chain=[1, "2"])), "stack[0].process_id_chain[1]", "expected an integer, got str", id="text-pid-in-chain"),
        pytest.param(_document(_frame(process_id_chain={})), "stack[0].process_id_chain", "expected an array, got dict", id="chain-not-array"),
        pytest.param(_document(_frame(extra=[1])), "stack[0].extra", "expected an object, got list", id="extra-not-object"),
    ],
)
def test_context_snapshot_from_json_refuses_a_malformed_document_naming_the_field(text: str, location: str, reason: str) -> None:
    with pytest.raises(ValueError, match="Invalid context snapshot") as caught:
        ContextSnapshot.from_json(text)
    assert str(caught.value).startswith(f"Invalid context snapshot: {location}: {reason}")
