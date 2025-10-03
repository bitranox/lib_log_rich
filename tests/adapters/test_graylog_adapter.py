from __future__ import annotations

import asyncio
import json
import socket
import ssl
from datetime import datetime, timezone
from typing import cast

import pytest

from lib_log_rich.adapters.graylog import GraylogAdapter
from lib_log_rich.domain.context import LogContext
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

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True, protocol="udp")
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
