"""Context snapshot JSON adapter: the typed boundary for context text crossing a process."""

from __future__ import annotations

import json
import multiprocessing as mp
from dataclasses import asdict
from typing import Any, cast

import pytest

from lib_log_rich.adapters.context_snapshot import dump_context_snapshot, load_context_snapshot
from lib_log_rich.domain.context import ContextBinder, ContextSnapshot, LogContext
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

_FULL_FRAME = LogContext(
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
    extra={"nested": {"n": [1, 2]}, "u": "\u00e4\u2603", "f": 1.5, "b": True, "z": None},
)

# Captured from ContextSnapshot.to_json() before the JSON form moved into this adapter, so the
# text a parent process writes stays byte for byte what it was.
_CAPTURED_TEXT = (
    '{"version": 1, "stack": [{"service": "s", "environment": "e", "job_id": "j", "request_id": null, '
    '"user_id": null, "user_name": null, "hostname": null, "process_id": null, "process_id_chain": [], '
    '"trace_id": null, "span_id": null, "extra": {}}, {"service": "svc", "environment": "prod", '
    '"job_id": "job", "request_id": "req", "user_id": "u1", "user_name": "alice", "hostname": "api01", '
    '"process_id": 11, "process_id_chain": [10, 11], "trace_id": "t", "span_id": "s", "extra": '
    '{"nested": {"n": [1, 2]}, "u": "\\u00e4\\u2603", "f": 1.5, "b": true, "z": null}}]}'
)


def _minimal() -> LogContext:
    return LogContext(service="s", environment="e", job_id="j")


def _child_process(queue: mp.Queue[Any], text: str) -> None:
    binder = ContextBinder()
    binder.deserialize(load_context_snapshot(text))
    current = binder.current()
    if current is None:
        raise AssertionError("ContextBinder.current() returned None in child process")
    queue.put(asdict(current))


def test_dump_writes_the_text_to_json_wrote_before_the_move() -> None:
    assert dump_context_snapshot(ContextSnapshot(stack=(_minimal(), _FULL_FRAME))) == _CAPTURED_TEXT


def test_dump_of_an_empty_snapshot() -> None:
    assert dump_context_snapshot(ContextSnapshot()) == '{"version": 1, "stack": []}'


def test_round_trip_keeps_every_field() -> None:
    snapshot = ContextSnapshot(stack=(_minimal(), _FULL_FRAME))
    assert load_context_snapshot(dump_context_snapshot(snapshot)) == snapshot


def test_load_fills_absent_optional_fields() -> None:
    snapshot = load_context_snapshot('{"version": 1, "stack": [{"service": "s", "environment": "e", "job_id": "j"}]}')
    assert snapshot.stack == (_minimal(),)


def test_dump_refuses_an_unencodable_extra() -> None:
    with pytest.raises(TypeError):
        dump_context_snapshot(ContextSnapshot(stack=(LogContext(service="s", environment="e", job_id="j", extra={"when": object()}),)))


def test_json_text_carries_the_context_to_a_child_process() -> None:
    binder = ContextBinder()
    with binder.bind(service="svc", environment="test", job_id="job-1", extra={"tenant": "acme"}):
        text = dump_context_snapshot(binder.serialize())
    queue: mp.Queue[Any] = mp.Queue()
    process = mp.Process(target=_child_process, args=(queue, text))
    process.start()
    process.join(timeout=2)
    assert process.exitcode == 0
    child_data = cast("dict[str, Any]", queue.get(timeout=2))
    assert (child_data["job_id"], child_data["extra"]) == ("job-1", {"tenant": "acme"})


def _frame(**fields: object) -> dict[str, object]:
    return {"service": "s", "environment": "e", "job_id": "j", **fields}


def _document(*frames: object, **top: object) -> str:
    return json.dumps({"version": 1, "stack": list(frames), **top})


@pytest.mark.parametrize(
    ("text", "line"),
    [
        pytest.param("{not json", "key must be a string at line 1 column 2", id="not-json"),
        pytest.param("[]", "Input should be an object", id="top-level-array"),
        pytest.param(_document(owner="x"), "owner: Extra inputs are not permitted", id="unknown-top-level-key"),
        pytest.param('{"stack": []}', "version: Field required", id="missing-version"),
        pytest.param(_document(version=True), "version: Input should be a valid integer", id="boolean-version"),
        pytest.param(_document(version=3), "version: unsupported version 3; expected 1", id="unknown-version"),
        pytest.param('{"version": 1}', "stack: Field required", id="missing-stack"),
        pytest.param(_document("frame"), "stack.0: Input should be an object", id="frame-not-object"),
        pytest.param(_document(_frame(), _frame(route="/x")), "stack.1.route: Extra inputs are not permitted", id="unknown-frame-key"),
        pytest.param(_document({"environment": "e", "job_id": "j"}), "stack.0.service: Field required", id="missing-service"),
        pytest.param(_document(_frame(service=None)), "stack.0.service: Input should be a valid string", id="null-service"),
        pytest.param(_document(_frame(job_id="  ")), "stack.0.job_id: must not be empty", id="blank-job-id"),
        pytest.param(_document(_frame(request_id=5)), "stack.0.request_id: Input should be a valid string", id="numeric-request-id"),
        pytest.param(_document(_frame(process_id="12")), "stack.0.process_id: Input should be a valid integer", id="text-process-id"),
        pytest.param(_document(_frame(process_id=False)), "stack.0.process_id: Input should be a valid integer", id="boolean-process-id"),
        pytest.param(_document(_frame(process_id_chain=[1, "2"])), "stack.0.process_id_chain.1: Input should be a valid integer", id="text-pid-in-chain"),
        pytest.param(_document(_frame(process_id_chain={})), "stack.0.process_id_chain: Input should be a valid array", id="chain-not-array"),
        pytest.param(_document(_frame(process_id_chain=None)), "stack.0.process_id_chain: Input should be a valid array", id="null-chain"),
        pytest.param(_document(_frame(extra=[1])), "stack.0.extra: Input should be an object", id="extra-not-object"),
        pytest.param(_document(_frame(extra=None)), "stack.0.extra: Input should be an object", id="null-extra"),
    ],
)
def test_load_refuses_a_malformed_document_naming_the_field(text: str, line: str) -> None:
    with pytest.raises(ValueError, match="Invalid context snapshot") as caught:
        load_context_snapshot(text)
    assert str(caught.value) == f"Invalid context snapshot: {line}"


def test_load_names_every_refused_field_in_one_message() -> None:
    with pytest.raises(ValueError, match="Invalid context snapshot") as caught:
        load_context_snapshot(_document(_frame(job_id=""), _frame(process_id=True)))
    assert str(caught.value) == "Invalid context snapshot: stack.0.job_id: must not be empty; stack.1.process_id: Input should be a valid integer"


def test_load_refusal_does_not_echo_the_refused_value() -> None:
    with pytest.raises(ValueError, match="Invalid context snapshot") as caught:
        load_context_snapshot(_document(_frame(request_id=424242, process_id="sekrit-value")))
    message = str(caught.value)
    assert ("424242" not in message, "sekrit" not in message, "errors.pydantic.dev" not in message) == (True, True, True)
