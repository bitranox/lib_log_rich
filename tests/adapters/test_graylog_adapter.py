from __future__ import annotations

import asyncio
import importlib
import json
import socket
import ssl
from datetime import date, datetime, timezone
from typing import Any, cast

import pytest

from lib_log_rich.adapters.graylog import GraylogAdapter
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

    def sendall(self, data: bytes) -> None:  # noqa: D401 - behaviour described above
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
        if not connections:
            conn = FailingConnection()
        else:
            conn = HealthyConnection()
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


def test_graylog_adapter_tls(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    class DummyConnection:
        def __init__(self) -> None:
            self.closed = False
            self.timeout: float | None = None

        def settimeout(self, value: float | None) -> None:
            self.timeout = value

        def close(self) -> None:
            self.closed = True

    class DummyWrapped:
        def __init__(self, connection: DummyConnection) -> None:
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

    wrapped_instances: list[DummyWrapped] = []
    context_calls: list[str] = []

    def fake_create_connection(
        address: tuple[str, int],
        timeout: float | None = None,
        source_address: tuple[str, int] | None = None,
    ) -> socket.socket:
        del address
        del source_address
        connection = DummyConnection()
        connection.settimeout(timeout)
        return cast(socket.socket, connection)

    def fake_create_default_context() -> ssl.SSLContext:
        class _Context:
            def wrap_socket(self, sock: DummyConnection, *, server_hostname: str):
                context_calls.append(server_hostname)
                wrapped = DummyWrapped(sock)
                wrapped_instances.append(wrapped)
                return wrapped

        return _Context()  # type: ignore[return-value]

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
    module = importlib.import_module("lib_log_rich.adapters.graylog")
    coerce = getattr(cast(Any, module), "_coerce_json_value")
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
    assert cast(Any, adapter)._get_tcp_socket() is stub
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
    gelf_payload = cast(Any, adapter)._build_payload(event)
    # Access dataclass attributes directly, then check dict via to_dict()
    assert gelf_payload.user == "user"
    assert gelf_payload.hostname == "host"
    assert gelf_payload.pid == 4321
    assert gelf_payload.process_id_chain == "1>2>3"
    # Extra fields only visible via to_dict()
    payload_dict = gelf_payload.to_dict()
    assert payload_dict["_bytes"] == "value"


def test_graylog_build_payload_accepts_custom_context(sample_event: LogEvent) -> None:
    class DictContext:
        """Mock context with string process_id_chain for testing non-tuple chains."""

        service = "svc"
        environment = "env"
        job_id = "job"
        request_id = "req"
        process_id_chain = ("worker-1",)  # Tuple format as expected
        hostname = None
        user_name = None
        process_id = None

        def to_dict(self, *, include_none: bool = False) -> dict[str, Any]:  # noqa: ARG002
            return {
                "service": self.service,
                "environment": self.environment,
                "job_id": self.job_id,
                "request_id": self.request_id,
                "process_id_chain": "worker-1",
            }

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=False)
    custom_event = sample_event.replace()
    object.__setattr__(custom_event, "context", DictContext())
    gelf_payload = cast(Any, adapter)._build_payload(custom_event)
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
    assert cast(Any, adapter)._socket is None


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
