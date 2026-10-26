from __future__ import annotations

import asyncio
import json
import socket
import ssl
from datetime import date, datetime, timezone
from typing import Any, cast

import pytest
from pydantic import ValidationError

from lib_log_rich.adapters.graylog import GELFPayload, GraylogAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.enums import GraylogProtocol
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


class TCPConnectionStub:
    """Minimal TCP socket double recording writes and close state."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.closed = False
        self.timeout: float | None = None

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class UDPSocketStub:
    """UDP socket double capturing payloads and providing context-manager hooks."""

    def __init__(self) -> None:
        self.sent_packets: list[tuple[bytes, tuple[str, int]]] = []
        self.timeout: float | None = None
        self.closed = False

    def __enter__(self) -> UDPSocketStub:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def sendto(self, data: bytes, address: tuple[str, int]) -> None:
        self.sent_packets.append((data, address))

    def close(self) -> None:
        self.closed = True


class HealthyConnection(TCPConnectionStub):
    """TCP stub representing a connection that succeeds on send."""


class FailingConnection(TCPConnectionStub):
    """TCP stub raising on first send to trigger retry logic."""

    def __init__(self) -> None:
        super().__init__()
        self._attempts = 0

    def sendall(self, data: bytes) -> None:
        self._attempts += 1
        if self._attempts == 1:
            raise OSError("simulated connection failure")
        super().sendall(data)


@pytest.fixture
def sample_event() -> LogEvent:
    return LogEvent(
        event_id="evt-1",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.ERROR,
        message="boom",
        context=LogContext(service="svc", environment="test", job_id="job-1", request_id="req"),
        extra={"foo": "bar"},
    )


def test_graylog_adapter_sends_gelf_message(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    connection = TCPConnectionStub()

    def fake_create_connection(_address: tuple[str, int], timeout: float | None = None) -> TCPConnectionStub:
        connection.settimeout(timeout)
        return connection

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    adapter.emit(sample_event)
    asyncio.run(adapter.flush())

    assert connection.sent
    payload = json.loads(connection.sent[0].rstrip(b"\x00").decode("utf-8"))
    assert payload["short_message"] == sample_event.message
    assert payload["_job_id"] == sample_event.context.job_id
    assert payload["level"] == 3


def test_graylog_adapter_can_be_disabled(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    connection = TCPConnectionStub()

    def fake_create_connection(_address: tuple[str, int], timeout: float | None = None) -> TCPConnectionStub:
        connection.settimeout(timeout)
        return connection

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=False)
    adapter.emit(sample_event)
    asyncio.run(adapter.flush())
    assert connection.sent == []


def test_graylog_adapter_udp_transport(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    udp_socket = UDPSocketStub()

    def fake_socket(*_args: object, **_kwargs: object) -> UDPSocketStub:
        return udp_socket

    monkeypatch.setattr(socket, "socket", fake_socket)

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True, protocol=GraylogProtocol.UDP)
    adapter.emit(sample_event)

    assert udp_socket.sent_packets
    data, address = udp_socket.sent_packets[0]
    assert address == ("gray.example", 12201)
    payload = json.loads(data.rstrip(b"\x00").decode("utf-8"))
    assert payload["short_message"] == sample_event.message


def test_graylog_adapter_reuses_tcp_connection(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    created: list[TCPConnectionStub] = []

    def fake_create_connection(address: tuple[str, int], *, timeout: float | None = None) -> TCPConnectionStub:
        conn = TCPConnectionStub()
        conn.settimeout(timeout)
        created.append(conn)
        return conn

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    adapter.emit(sample_event)
    adapter.emit(sample_event)

    assert len(created) == 1
    assert len(created[0].sent) == 2

    asyncio.run(adapter.flush())
    assert created[0].closed is True

    adapter.emit(sample_event)
    assert len(created) == 2


def test_graylog_adapter_reconnects_after_failure(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    connections: list[TCPConnectionStub] = []

    def fake_create_connection(_address: tuple[str, int], *, timeout: float | None = None) -> TCPConnectionStub:
        conn = FailingConnection() if not connections else HealthyConnection()
        conn.settimeout(timeout)
        connections.append(conn)
        return conn

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    adapter.emit(sample_event)

    assert len(connections) == 2
    assert connections[0].closed is True
    assert connections[1].sent, "second connection should receive payload"

    asyncio.run(adapter.flush())


class _TLSDummyConnection:
    """Plain TCP connection double handed to the TLS context for wrapping."""

    def __init__(self) -> None:
        self.closed = False
        self.timeout: float | None = None

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def close(self) -> None:
        self.closed = True


class _TLSDummyWrapped:
    """TLS-wrapped socket double recording the bytes sent through it."""

    def __init__(self, connection: _TLSDummyConnection) -> None:
        self._connection = connection
        self.closed = False
        self.sent: list[bytes] = []
        self.timeout: float | None = None

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


class _TLSDummyContext:
    """SSL context double recording the server hostname of each wrap."""

    def __init__(self, context_calls: list[str], wrapped_instances: list[_TLSDummyWrapped]) -> None:
        self._context_calls = context_calls
        self._wrapped_instances = wrapped_instances

    def wrap_socket(self, sock: _TLSDummyConnection, *, server_hostname: str) -> _TLSDummyWrapped:
        self._context_calls.append(server_hostname)
        wrapped = _TLSDummyWrapped(sock)
        self._wrapped_instances.append(wrapped)
        return wrapped


def test_graylog_adapter_tls(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    wrapped_instances: list[_TLSDummyWrapped] = []
    context_calls: list[str] = []

    def fake_create_connection(
        address: tuple[str, int],
        timeout: float | None = None,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        del address
        del source_address
        connection = _TLSDummyConnection()
        connection.settimeout(timeout)
        return cast("socket.socket", connection)

    def fake_create_default_context() -> ssl.SSLContext:
        return cast("ssl.SSLContext", _TLSDummyContext(context_calls, wrapped_instances))

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    monkeypatch.setattr(ssl, "create_default_context", fake_create_default_context)

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True, use_tls=True)
    adapter.emit(sample_event)

    assert context_calls == ["gray.example"]
    assert wrapped_instances
    sent = wrapped_instances[0].sent[0]
    payload = json.loads(sent.rstrip(b"\x00").decode("utf-8"))
    assert payload["_request_id"] == sample_event.context.request_id


def test_graylog_adapter_includes_system_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    context = LogContext(
        service="svc",
        environment="test",
        job_id="job-1",
        user_name="tester",
        hostname="api01",
        process_id=90210,
        process_id_chain=(9000, 90210),
    )
    event = LogEvent(
        event_id="evt",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=context,
    )
    connection = TCPConnectionStub()

    def fake_create_connection(_address: tuple[str, int], timeout: float | None = None) -> TCPConnectionStub:
        connection.settimeout(timeout)
        return connection

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    adapter.emit(event)

    assert connection.sent
    payload = json.loads(connection.sent[0].rstrip(b"\x00").decode("utf-8"))
    assert payload["_user"] == "tester"
    assert payload["_hostname"] == "api01"
    assert payload["_pid"] == 90210
    assert payload["_process_id_chain"] == "9000>90210"
    assert payload["_service"] == "svc"


def test_graylog_adapter_serialises_complex_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = TCPConnectionStub()

    def fake_create_connection(_address: tuple[str, int], timeout: float | None = None) -> TCPConnectionStub:
        connection.settimeout(timeout)
        return connection

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)

    context = LogContext(service="svc", environment="test", job_id="job-1")
    event = LogEvent(
        event_id="evt",
        timestamp=datetime(2025, 9, 23, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message="hello",
        context=context,
        extra={
            "timestamp": datetime(2025, 9, 23, 1, tzinfo=timezone.utc),
            "identifiers": {"alpha", "beta"},
            "payload": {"count": 3},
        },
    )

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    adapter.emit(event)

    payload = json.loads(connection.sent[0].rstrip(b"\x00").decode("utf-8"))
    assert set(payload["_identifiers"]) == {"alpha", "beta"}
    assert payload["_payload"] == {"count": 3}
    assert isinstance(payload["_timestamp"], str)


def test_coerce_json_value_handles_various_types() -> None:
    # coerce_json_value was moved to shared _json_coerce module
    from lib_log_rich.adapters._json_coerce import coerce_json_value as coerce

    naive = datetime(2025, 10, 8, 12, 0, 0)
    aware = datetime(2025, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    today = date(2025, 10, 8)
    assert coerce(naive).startswith("2025-10-08T12:00:00")
    assert coerce(aware).endswith("+00:00")
    assert coerce(today) == "2025-10-08"
    assert coerce(b"data") == "data"
    assert coerce(bytes([0xFF])) == "ff"
    mapping = {"nested": {1, 2}, "bytes": b"hi"}
    result = coerce(mapping)
    assert sorted(result["nested"]) == [1, 2]
    assert result["bytes"] == "hi"
    assert coerce(object()).startswith("<")
    # Path objects serialize as POSIX strings
    from pathlib import Path

    assert coerce(Path("/var/log/app.log")) == "/var/log/app.log"
    assert coerce({"log_file": Path("/var/log")})["log_file"] == "/var/log"
    assert coerce([Path("/a"), Path("/b")]) == ["/a", "/b"]


def test_graylog_adapter_rejects_tls_over_udp() -> None:
    with pytest.raises(ValueError, match="TLS is only supported for TCP"):
        GraylogAdapter(host="gray.example", port=12201, protocol=GraylogProtocol.UDP, use_tls=True)


def test_graylog_adapter_raises_after_consecutive_failures(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    class AlwaysFailing:
        def __init__(self) -> None:
            self.closed = False

        def settimeout(self, value: float | None) -> None:
            pass

        def sendall(self, data: bytes) -> None:
            raise OSError("send failure")

        def close(self) -> None:
            self.closed = True

    def fake_create_connection(_address: tuple[str, int], timeout: float | None = None) -> AlwaysFailing:
        return AlwaysFailing()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    with pytest.raises(OSError, match="send failure"):
        adapter.emit(sample_event)


def test_graylog_get_tcp_socket_returns_cached_instance() -> None:
    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    stub = socket.socket()
    object.__setattr__(adapter, "_socket", stub)
    assert cast("Any", adapter)._get_tcp_socket() is stub
    stub.close()
    object.__setattr__(adapter, "_socket", None)


def test_graylog_build_payload_includes_optional_fields(sample_event: LogEvent) -> None:
    context = sample_event.context.replace(
        user_name="user",
        hostname="host",
        process_id=4321,
        process_id_chain=(1, 2, 3),
    )
    event = sample_event.replace(context=context, extra={"bytes": b"value"})
    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=False)
    gelf_payload: GELFPayload = cast("Any", adapter)._build_payload(event)
    assert gelf_payload.user == "user"
    assert gelf_payload.hostname == "host"
    assert gelf_payload.pid == 4321
    assert gelf_payload.process_id_chain == "1>2>3"
    # Extra fields are flattened only in the serialized GELF form.
    assert gelf_payload.model_dump()["_bytes"] == "value"


def test_gelf_payload_has_no_to_dict() -> None:
    """The hand-written ``to_dict`` is gone; ``model_dump`` is the only serialized form."""
    payload = GELFPayload(version="1.1", short_message="m", host="h", timestamp=1.0, level=6, logger="l")
    assert not hasattr(payload, "to_dict")
    assert payload.model_dump() == {
        "version": "1.1",
        "short_message": "m",
        "host": "h",
        "timestamp": 1.0,
        "level": 6,
        "logger": "l",
    }


def test_gelf_payload_is_frozen_and_rejects_unknown_fields() -> None:
    payload = GELFPayload(version="1.1", short_message="m", host="h", timestamp=1.0, level=6, logger="l")
    with pytest.raises(ValidationError):
        payload.level = 3
    with pytest.raises(ValidationError, match="unknown"):
        GELFPayload.model_validate({"version": "1.1", "short_message": "m", "host": "h", "timestamp": 1.0, "level": 6, "logger": "l", "unknown": 1})


def test_graylog_build_payload_accepts_custom_context(sample_event: LogEvent) -> None:
    class DictContext:
        """Duck-typed context whose process chain holds a string part."""

        service = "svc"
        environment = "env"
        job_id = "job"
        request_id = "req"
        process_id_chain = ("worker-1",)  # Tuple format as expected
        hostname = None
        user_name = None
        process_id = None

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=False)
    custom_event = sample_event.replace()
    object.__setattr__(custom_event, "context", DictContext())
    gelf_payload = cast("Any", adapter)._build_payload(custom_event)
    assert gelf_payload.process_id_chain == "worker-1"


def test_graylog_flush_closes_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    closed: list[bool] = []

    class DummySocket:
        def close(self) -> None:
            closed.append(True)

    object.__setattr__(adapter, "_socket", DummySocket())
    asyncio.run(adapter.flush())
    assert closed == [True]
    assert cast("Any", adapter)._socket is None


def test_graylog_strips_emoji_from_short_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that emoji and Unicode icons are removed from short_message field for GELF."""
    stub = UDPSocketStub()

    def fake_socket(*_args: object, **_kwargs: object) -> UDPSocketStub:
        return stub

    monkeypatch.setattr(socket, "socket", fake_socket)

    test_cases = [
        ("Info ℹ message", "Info  message"),
        ("Warning ⚠ detected", "Warning  detected"),
        ("Error ✖ occurred", "Error  occurred"),
        ("Critical ☠ failure", "Critical  failure"),
        ("Debug 🐞 trace", "Debug  trace"),
        ("Mixed 🔥 emoji 💥 test", "Mixed  emoji  test"),
        ("Plain text", "Plain text"),
    ]

    ctx = LogContext(service="svc", environment="prod", job_id="job-1", request_id="req-1")
    adapter = GraylogAdapter(host="localhost", port=12201, protocol=GraylogProtocol.UDP)

    for original, expected in test_cases:
        event = LogEvent(
            event_id="test",
            timestamp=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
            logger_name="test",
            level=LogLevel.INFO,
            message=original,
            context=ctx,
        )
        adapter.emit(event)
        packet = stub.sent_packets.pop()[0]
        payload = json.loads(packet.rstrip(b"\x00").decode("utf-8"))
        assert payload["short_message"] == expected, f"Failed to strip emoji from: {original}"


# Golden wire payloads captured from the dataclass + orjson implementation before the
# GELF payload became a Pydantic model; the model must keep every key, value and JSON type.
# Compared as parsed objects: a JSON object is unordered, so no GELF consumer reads key order.
_GOLDEN_TIMESTAMP = datetime(2025, 9, 23, 1, 2, 3, 456789, tzinfo=timezone.utc)
_GOLDEN_BASE: dict[str, object] = {
    "version": "1.1",
    "short_message": "hello",
    "host": "svc",
    "timestamp": 1758589323.456789,
    "level": 6,
    "logger": "tests.golden",
    "_job_id": "job",
    "_environment": "prod",
    "_service": "svc",
}
_GOLDEN_CASES: dict[str, tuple[LogContext, dict[str, Any], str, LogLevel, dict[str, object]]] = {
    "minimal": (
        LogContext(service="svc", environment="prod", job_id="job"),
        {},
        "hello",
        LogLevel.INFO,
        dict(_GOLDEN_BASE),
    ),
    "all_context_fields": (
        LogContext(
            service="svc",
            environment="prod",
            job_id="job",
            request_id="req",
            user_name="alice",
            hostname="api01",
            process_id=4321,
            process_id_chain=(1, 4321),
        ),
        {},
        "hello",
        LogLevel.CRITICAL,
        {
            **_GOLDEN_BASE,
            "host": "api01",
            "level": 2,
            "_request_id": "req",
            "_user": "alice",
            "_hostname": "api01",
            "_pid": 4321,
            "_process_id_chain": "1>4321",
        },
    ),
    "typed_extra": (
        LogContext(service="svc", environment="prod", job_id="job"),
        {
            "none": None,
            "n": 3,
            "f": 1.5,
            "b": True,
            "nested": {"k": [1, {"x": b"hi"}]},
            "when": datetime(2025, 1, 2, tzinfo=timezone.utc),
            "day": date(2025, 1, 2),
            "tup": (1, 2),
            "uni": "Gruesse äöü 日本",
            "big": 12345678901234567890,
        },
        "hello",
        LogLevel.INFO,
        {
            **_GOLDEN_BASE,
            "_none": None,
            "_n": 3,
            "_f": 1.5,
            "_b": True,
            "_nested": {"k": [1, {"x": "hi"}]},
            "_when": "2025-01-02T00:00:00+00:00",
            "_day": "2025-01-02",
            "_tup": [1, 2],
            "_uni": "Gruesse äöü 日本",
            "_big": 12345678901234567890,
        },
    ),
    "non_ascii_message": (
        LogContext(service="svc", environment="prod", job_id="job"),
        {},
        "Grüße 日本 \U0001f525 done",
        LogLevel.INFO,
        {**_GOLDEN_BASE, "short_message": "Grüße 日本  done"},
    ),
    "extra_overrides_context_field": (
        LogContext(service="svc", environment="prod", job_id="job"),
        {"job_id": "extra-wins", "id": "x", "version": "v"},
        "hello",
        LogLevel.INFO,
        {**_GOLDEN_BASE, "_job_id": "extra-wins", "_id": "x", "_version": "v"},
    ),
}


@pytest.mark.parametrize("case", sorted(_GOLDEN_CASES))
def test_graylog_wire_payload_matches_golden(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    context, extra, message, level, expected = _GOLDEN_CASES[case]
    stub = UDPSocketStub()

    def fake_socket(*_args: object, **_kwargs: object) -> UDPSocketStub:
        return stub

    monkeypatch.setattr(socket, "socket", fake_socket)
    event = LogEvent(
        event_id="e",
        timestamp=_GOLDEN_TIMESTAMP,
        logger_name="tests.golden",
        level=level,
        message=message,
        context=context,
        extra=extra,
    )
    GraylogAdapter(host="gray.example", port=12201, protocol=GraylogProtocol.UDP).emit(event)

    data = stub.sent_packets[0][0]
    assert data.endswith(b"\x00")
    assert data.count(b"\x00") == 1, "the GELF frame carries exactly one terminator"
    payload = json.loads(data[:-1].decode("utf-8"))
    assert payload == expected
    assert {key: type(value) for key, value in payload.items()} == {key: type(value) for key, value in expected.items()}
