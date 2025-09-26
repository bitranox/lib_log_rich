from __future__ import annotations

import asyncio
import json
import socket
import ssl
import threading
import time
from datetime import datetime, timezone

import pytest

from lib_log_rich.adapters.graylog import GraylogAdapter
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


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


class _Server:
    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]
        self.messages: list[bytes] = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._running = threading.Event()

    def start(self) -> None:
        self._running.set()
        self._thread.start()

    def close(self) -> None:
        self._running.clear()
        try:
            self._sock.close()
        finally:
            self._thread.join(timeout=1)

    def _run(self) -> None:
        while self._running.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            with conn:
                data = conn.recv(4096)
                if data:
                    self.messages.append(data.strip(b"\x00"))


@pytest.fixture
def tcp_server() -> _Server:
    server = _Server()
    server.start()
    try:
        yield server
    finally:
        server.close()


def test_graylog_adapter_sends_gelf_message(sample_event: LogEvent, tcp_server: _Server) -> None:
    adapter = GraylogAdapter(host="127.0.0.1", port=tcp_server.port, enabled=True)
    adapter.emit(sample_event)
    asyncio.run(adapter.flush())

    for _ in range(10):
        if tcp_server.messages:
            break
        time.sleep(0.05)

    assert tcp_server.messages, "server should receive at least one message"
    payload = json.loads(tcp_server.messages[0].decode("utf-8"))
    assert payload["short_message"] == sample_event.message
    assert payload["_job_id"] == sample_event.context.job_id
    assert payload["level"] == 3


def test_graylog_adapter_can_be_disabled(sample_event: LogEvent, tcp_server: _Server) -> None:
    adapter = GraylogAdapter(host="127.0.0.1", port=tcp_server.port, enabled=False)
    adapter.emit(sample_event)
    asyncio.run(adapter.flush())
    assert tcp_server.messages == []


def test_graylog_adapter_udp_transport(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    sent_packets: list[tuple[bytes, tuple[str, int]]] = []

    class DummySocket:
        def __init__(self, *_args, **_kwargs) -> None:
            self.timeout: float | None = None

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

        def sendto(self, data: bytes, address: tuple[str, int]) -> None:
            sent_packets.append((data, address))

        def __enter__(self) -> "DummySocket":
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: DummySocket())

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True, protocol="udp")
    adapter.emit(sample_event)

    assert sent_packets
    data, address = sent_packets[0]
    assert address == ("gray.example", 12201)
    payload = json.loads(data.rstrip(b"\x00").decode("utf-8"))
    assert payload["short_message"] == sample_event.message


def test_graylog_adapter_reuses_tcp_connection(monkeypatch: pytest.MonkeyPatch, sample_event: LogEvent) -> None:
    created: list[object] = []

    class DummyConnection:
        def __init__(self) -> None:
            self.sent: list[bytes] = []
            self.closed = False
            self.timeout: float | None = None

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

        def close(self) -> None:
            self.closed = True

    def fake_create_connection(_address, *, timeout=None):
        conn = DummyConnection()
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
    class FailingConnection:
        def __init__(self) -> None:
            self.closed = False
            self.timeout: float | None = None
            self.sent: list[bytes] = []
            self.calls = 0

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def sendall(self, data: bytes) -> None:
            if self.calls == 0:
                self.calls += 1
                raise BrokenPipeError("broken pipe")
            self.sent.append(data)

        def close(self) -> None:
            self.closed = True

    class HealthyConnection(FailingConnection):
        def sendall(self, data: bytes) -> None:
            self.sent.append(data)

    connections: list[FailingConnection] = []

    def fake_create_connection(_address, *, timeout=None):
        if not connections:
            conn = FailingConnection()
        else:
            conn = HealthyConnection()
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

        def settimeout(self, value: float) -> None:
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

    def fake_create_connection(*_args, **_kwargs) -> DummyConnection:
        return DummyConnection()

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
    sent: list[bytes] = []

    class DummyConnection:
        def __init__(self) -> None:
            self.timeout: float | None = None

        def settimeout(self, value: float) -> None:
            self.timeout = value

        def sendall(self, data: bytes) -> None:
            sent.append(data)

        def close(self) -> None:
            return None

    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: DummyConnection())

    adapter = GraylogAdapter(host="gray.example", port=12201, enabled=True)
    adapter.emit(event)

    assert sent
    payload = json.loads(sent[0].rstrip(b"\x00").decode("utf-8"))
    assert payload["_user"] == "tester"
    assert payload["_hostname"] == "api01"
    assert payload["_pid"] == 90210
    assert payload["_process_id_chain"] == "9000>90210"
    assert payload["_service"] == "svc"
