from __future__ import annotations

import builtins
import socket
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Callable, Iterable, Optional, TypeAlias, cast

import pytest

from lib_log_rich.adapters.structured import journald as journald_module
from lib_log_rich.adapters.structured.journald import JournaldAdapter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import LINUX_ONLY, OS_AGNOSTIC, POSIX_ONLY

pytestmark = [OS_AGNOSTIC]

_UNIX_SOCKET_FAMILY: Optional[int] = cast(Optional[int], getattr(socket, "AF_UNIX", None))


Recorder: TypeAlias = list[dict[str, object]]


@pytest.fixture(autouse=True)
def reset_systemd_modules(monkeypatch: pytest.MonkeyPatch) -> Iterable[None]:
    saved = {name: sys.modules.pop(name, None) for name in ("systemd", "systemd.journal")}
    monkeypatch.setattr(journald_module, "_systemd_send", None)
    try:
        yield
    finally:
        for name in ("systemd", "systemd.journal"):
            sys.modules.pop(name, None)
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module


def _make_event(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> LogEvent:
    return event_factory(
        {
            "message": "lantern",
            "extra": {"theme": "dawn", "trace": "42"},
        }
    )


def test_adapter_whispers_structured_fields_via_public_emit(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    recorded: Recorder = []

    def capture(**payload: object) -> None:
        recorded.append(dict(payload))

    adapter = JournaldAdapter(sender=capture)
    adapter.emit(_make_event(event_factory))
    payload = recorded.pop()
    assert payload["MESSAGE"] == "lantern"
    assert payload["TRACE"] == "42"
    assert payload["PRIORITY"] == 6  # INFO priority
    assert payload["SERVICE"] == "svc"


@POSIX_ONLY
def test_adapter_braids_process_chain_when_context_offers_iterable(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    recorded: Recorder = []

    def capture(**payload: object) -> None:
        recorded.append(dict(payload))

    event = _make_event(event_factory).replace(context=_make_event(event_factory).context.merge(process_id_chain=(123, 456, 789)))
    adapter = JournaldAdapter(sender=capture)
    adapter.emit(event)
    payload = recorded.pop()
    assert payload["PROCESS_ID_CHAIN"] == "123>456>789"


def test_adapter_treats_string_process_chain_as_single_segment(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    recorded: Recorder = []

    def capture(**payload: object) -> None:
        recorded.append(dict(payload))

    event = _make_event(event_factory)

    class StringContext:
        """Mock context with string process_id_chain for testing single segment handling."""

        service = "svc"
        environment = "test"
        job_id = "job-1"
        process_id_chain = ("root",)  # Tuple that formats to single segment "root"
        request_id = None
        user_id = None
        user_name = None
        hostname = None
        process_id = None
        trace_id = None
        span_id = None
        extra: dict[str, object] = {}

        def to_dict(self, *, include_none: bool = False) -> dict[str, object]:  # noqa: ARG002
            return {
                "service": self.service,
                "environment": self.environment,
                "job_id": self.job_id,
                "process_id_chain": "root",
            }

    object.__setattr__(event, "context", StringContext())
    adapter = JournaldAdapter(sender=capture)
    adapter.emit(event)
    payload = recorded.pop()
    assert payload["PROCESS_ID_CHAIN"] == "root"


def test_adapter_honours_custom_service_field_alias(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    recorded: Recorder = []

    def capture(**payload: object) -> None:
        recorded.append(dict(payload))

    adapter = JournaldAdapter(sender=capture, service_field="unit")
    adapter.emit(_make_event(event_factory))
    assert recorded.pop()["UNIT"] == "svc"


def test_adapter_prefers_context_theme_and_prefixes_extra_theme(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    recorded: Recorder = []

    def capture(**payload: object) -> None:
        recorded.append(dict(payload))

    context_with_theme = _make_event(event_factory).context.merge(extra={"theme": "context"})
    event = _make_event(event_factory).replace(context=context_with_theme, extra={"theme": "event"})
    adapter = JournaldAdapter(sender=capture)
    adapter.emit(event)
    payload = recorded.pop()
    assert payload["THEME"] == "context"
    assert payload["EXTRA_THEME"] == "event"


def test_adapter_preserves_existing_message_when_extra_collision_occurs(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    recorded: Recorder = []

    def capture(**payload: object) -> None:
        recorded.append(dict(payload))

    event = _make_event(event_factory).replace(extra={"message": "shadow"})
    adapter = JournaldAdapter(sender=capture)
    adapter.emit(event)
    payload = recorded.pop()
    assert payload["MESSAGE"] == "lantern"
    assert payload["EXTRA_MESSAGE"] == "shadow"


def test_adapter_layers_extra_keys_when_context_extras_repeat(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    recorded: Recorder = []

    def capture(**payload: object) -> None:
        recorded.append(dict(payload))

    base_context = _make_event(event_factory).context.merge(extra={"message": "context", "extra_message": "again"})
    event = _make_event(event_factory).replace(context=base_context, extra={})
    adapter = JournaldAdapter(sender=capture)
    adapter.emit(event)
    payload = recorded.pop()
    assert payload["EXTRA_MESSAGE"] == "context"
    assert payload["EXTRA_EXTRA_MESSAGE"] == "again"


def test_adapter_delegates_to_existing_systemd_journal_module(
    monkeypatch: pytest.MonkeyPatch, event_factory: Callable[[dict[str, object] | None], LogEvent]
) -> None:
    recorded: Recorder = []

    def fake_send(**fields: object) -> None:
        recorded.append(dict(fields))

    pkg = ModuleType("systemd")
    pkg.__path__ = []
    journal_module = SimpleNamespace(send=fake_send)
    setattr(pkg, "journal", journal_module)
    monkeypatch.setitem(sys.modules, "systemd", pkg)
    monkeypatch.setitem(sys.modules, "systemd.journal", journal_module)
    adapter = JournaldAdapter()
    adapter.emit(_make_event(event_factory))
    assert recorded.pop()["MESSAGE"] == "lantern"


def test_adapter_promotes_top_level_systemd_module(monkeypatch: pytest.MonkeyPatch, event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    recorded: Recorder = []

    def fake_send(**fields: object) -> None:
        recorded.append(dict(fields))

    monkeypatch.setitem(sys.modules, "systemd", SimpleNamespace(journal=SimpleNamespace(send=fake_send)))
    adapter = JournaldAdapter()
    adapter.emit(_make_event(event_factory))
    assert recorded.pop()["MESSAGE"] == "lantern"


def test_adapter_reuses_preloaded_journal_module_when_import_fails(
    monkeypatch: pytest.MonkeyPatch, event_factory: Callable[[dict[str, object] | None], LogEvent]
) -> None:
    recorded: Recorder = []

    def fake_send(**fields: object) -> None:
        recorded.append(dict(fields))

    monkeypatch.setitem(sys.modules, "systemd.journal", SimpleNamespace(send=fake_send))

    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith("systemd"):
            raise ModuleNotFoundError("systemd missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    adapter = JournaldAdapter()
    adapter.emit(_make_event(event_factory))
    assert recorded.pop()["MESSAGE"] == "lantern"


def test_adapter_reuses_top_level_journal_attribute_when_import_fails(
    monkeypatch: pytest.MonkeyPatch, event_factory: Callable[[dict[str, object] | None], LogEvent]
) -> None:
    recorded: Recorder = []

    def fake_send(**fields: object) -> None:
        recorded.append(dict(fields))

    pkg = ModuleType("systemd")
    pkg.__path__ = []
    setattr(pkg, "journal", SimpleNamespace(send=fake_send))
    monkeypatch.setitem(sys.modules, "systemd", pkg)
    monkeypatch.delitem(sys.modules, "systemd.journal", raising=False)

    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith("systemd"):
            raise ModuleNotFoundError("systemd missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    adapter = JournaldAdapter()
    adapter.emit(_make_event(event_factory))
    assert recorded.pop()["MESSAGE"] == "lantern"


def test_adapter_caches_sender_across_instances(monkeypatch: pytest.MonkeyPatch, event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    calls: list[int] = []

    def fake_send(**fields: object) -> None:
        calls.append(1)

    pkg = ModuleType("systemd")
    pkg.__path__ = []
    journal_module = ModuleType("systemd.journal")
    journal_module.send = fake_send  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "systemd", pkg)
    monkeypatch.setitem(sys.modules, "systemd.journal", journal_module)

    first = JournaldAdapter()
    first.emit(_make_event(event_factory))

    monkeypatch.delitem(sys.modules, "systemd", raising=False)
    monkeypatch.delitem(sys.modules, "systemd.journal", raising=False)

    second = JournaldAdapter()
    second.emit(_make_event(event_factory))

    assert calls == [1, 1]


def _start_socket_listener(path: Path, capture: list[bytes]) -> threading.Thread:
    family = _UNIX_SOCKET_FAMILY
    if family is None:
        pytest.skip("UNIX domain sockets unavailable on this platform")

    def runner() -> None:
        if path.exists():
            path.unlink()
        with socket.socket(family, socket.SOCK_DGRAM) as srv:
            srv.bind(str(path))
            srv.settimeout(1)
            try:
                data, _ = srv.recvfrom(4096)
                capture.append(data)
            except socket.timeout:
                pass
        if path.exists():
            path.unlink()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread


def _wait_for_socket(path: Path, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)


@POSIX_ONLY
def test_adapter_writes_to_real_socket_when_systemd_is_missing(
    tmp_path: Path, event_factory: Callable[[dict[str, object] | None], LogEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "journal.sock"
    packets: list[bytes] = []
    thread = _start_socket_listener(socket_path, packets)
    _wait_for_socket(socket_path)

    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith("systemd"):
            raise ModuleNotFoundError("systemd missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    adapter = JournaldAdapter()
    monkeypatch.setattr(journald_module, "_JOURNAL_SOCKETS", (str(socket_path),))
    noisy_event = _make_event(event_factory).replace(extra={"binary": b"\x00\x01", "theme": "dawn"})
    try:
        adapter.emit(noisy_event)
    except RuntimeError as exc:
        if "Unable to write to journald socket" in str(exc):
            pytest.skip("journald socket fallback unavailable on runner")
        raise
    thread.join(timeout=1)
    assert packets and packets[0].startswith(b"MESSAGE=lantern")


@POSIX_ONLY
def test_adapter_recovers_when_native_send_is_not_callable(
    tmp_path: Path, event_factory: Callable[[dict[str, object] | None], LogEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "fallback.sock"
    packets: list[bytes] = []
    thread = _start_socket_listener(socket_path, packets)
    _wait_for_socket(socket_path)

    pkg = ModuleType("systemd")
    pkg.__path__ = []
    setattr(pkg, "journal", SimpleNamespace(send="not callable"))
    monkeypatch.setitem(sys.modules, "systemd", pkg)
    monkeypatch.setitem(sys.modules, "systemd.journal", pkg.journal)

    adapter = JournaldAdapter()
    monkeypatch.setattr(journald_module, "_JOURNAL_SOCKETS", (str(socket_path),))
    try:
        adapter.emit(_make_event(event_factory))
    except RuntimeError as exc:
        if "Unable to write to journald socket" in str(exc):
            pytest.skip("journald socket fallback unavailable on runner")
        raise
    thread.join(timeout=1)
    assert packets and packets[0].startswith(b"MESSAGE=lantern")


@POSIX_ONLY
def test_adapter_raises_runtime_error_when_all_sockets_fail(
    tmp_path: Path, event_factory: Callable[[dict[str, object] | None], LogEvent], monkeypatch: pytest.MonkeyPatch
) -> None:
    socket_path = tmp_path / "missing.sock"

    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith("systemd"):
            raise ModuleNotFoundError("systemd missing")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    adapter = JournaldAdapter()
    monkeypatch.setattr(journald_module, "_JOURNAL_SOCKETS", (str(socket_path),))
    with pytest.raises(RuntimeError):
        adapter.emit(_make_event(event_factory))


@LINUX_ONLY
def test_linux_adapter_passes_payload_to_native_journal(monkeypatch: pytest.MonkeyPatch, event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    journal = pytest.importorskip("systemd.journal")
    recorded: Recorder = []

    def fake_send(**fields: object) -> None:
        recorded.append(dict(fields))

    monkeypatch.setattr(journal, "send", fake_send)
    adapter = JournaldAdapter()
    adapter.emit(_make_event(event_factory))
    assert recorded.pop()["MESSAGE"] == "lantern"


@LINUX_ONLY
def test_linux_adapter_maps_error_level_to_priority_three(
    monkeypatch: pytest.MonkeyPatch, event_factory: Callable[[dict[str, object] | None], LogEvent]
) -> None:
    journal = pytest.importorskip("systemd.journal")
    recorded: Recorder = []

    def fake_send(**fields: object) -> None:
        recorded.append(dict(fields))

    monkeypatch.setattr(journal, "send", fake_send)
    event = _make_event(event_factory).replace(level=LogLevel.ERROR)
    adapter = JournaldAdapter()
    adapter.emit(event)
    assert recorded.pop()["PRIORITY"] == 3


def test_adapter_strips_emoji_from_message(event_factory: Callable[[dict[str, object] | None], LogEvent]) -> None:
    """Verify that emoji and Unicode icons are removed from MESSAGE field for structured logging."""
    recorded: Recorder = []

    def capture(**payload: object) -> None:
        recorded.append(dict(payload))

    # Test with common log level icons
    test_cases = [
        ("Info ℹ message", "Info  message"),
        ("Warning ⚠ detected", "Warning  detected"),
        ("Error ✖ occurred", "Error  occurred"),
        ("Critical ☠ failure", "Critical  failure"),
        ("Debug 🐞 trace", "Debug  trace"),
        ("Mixed 🔥 emoji 💥 test", "Mixed  emoji  test"),
        ("Plain text", "Plain text"),
    ]

    adapter = JournaldAdapter(sender=capture)
    for original, expected in test_cases:
        event = event_factory({"message": original})
        adapter.emit(event)
        payload = recorded.pop()
        assert payload["MESSAGE"] == expected, f"Failed to strip emoji from: {original}"
