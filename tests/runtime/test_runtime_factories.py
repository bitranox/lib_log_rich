from __future__ import annotations

import contextvars
from io import StringIO
from typing import TYPE_CHECKING, Any

from lib_log_rich.application.ports.identity import SystemIdentityPort
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.enums import ConsoleStream, DropReason
from lib_log_rich.domain.identity import SystemIdentity
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.runtime._factories import (
    FeatureFlags,
    LoggerProxy,
    SystemIdentityProvider,
    clear_identity_cache,
    create_console,
    create_runtime_binder,
    create_structured_backends,
)
from lib_log_rich.runtime.settings import ConsoleAppearance
from tests.os_markers import OS_AGNOSTIC

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from lib_log_rich.application.use_cases._types import ProcessResult
    from lib_log_rich.domain.events import LogEvent

pytestmark = [OS_AGNOSTIC]


def test_system_identity_provider_uses_environment_when_getuser_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_identity_cache()  # Clear cached values before test
    monkeypatch.setattr("lib_log_rich.runtime._factories.getpass", None)
    monkeypatch.setattr("lib_log_rich.runtime._factories.os", __import__("os"))
    monkeypatch.setenv("USER", "env-user")
    identity = SystemIdentityProvider().resolve_identity()
    assert identity.user_name == "env-user"


def test_create_structured_backends_returns_optional_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    adapters: dict[str, Any] = {"journald": object(), "eventlog": object()}

    monkeypatch.setattr("lib_log_rich.runtime._factories.JournaldAdapter", lambda: adapters["journald"])
    monkeypatch.setattr("lib_log_rich.runtime._factories.WindowsEventLogAdapter", lambda: adapters["eventlog"])

    backends = create_structured_backends(FeatureFlags(queue=True, ring_buffer=True, journald=True, eventlog=True))
    assert backends == [adapters["journald"], adapters["eventlog"]]


class _FixedIdentity(SystemIdentityPort):
    def resolve_identity(self) -> SystemIdentity:
        return SystemIdentity(user_name="dev", hostname="box", process_id=4242)


def test_create_runtime_binder_seeds_exactly_one_bootstrap_frame() -> None:
    binder = create_runtime_binder("svc", "prod", _FixedIdentity())

    assert binder.serialize().stack == (
        LogContext(
            service="svc",
            environment="prod",
            job_id="bootstrap",
            user_name="dev",
            hostname="box",
            process_id=4242,
            process_id_chain=(4242,),
        ),
    )


def test_create_runtime_binder_frame_reaches_a_fresh_context() -> None:
    # A thread or task starts from an empty contextvars context; the seeded frame
    # must still be there, or its events would carry no service at all.
    binder = create_runtime_binder("svc", "prod", _FixedIdentity())

    current = contextvars.Context().run(binder.current)

    assert current is not None
    assert current.job_id == "bootstrap"


def test_logger_proxy_below_its_level_reports_the_logger_level_drop_reason() -> None:
    calls: list[object] = []

    def process(**payload: object) -> ProcessResult:
        calls.append(payload)
        raise AssertionError("an event below the proxy level must not be processed")

    proxy = LoggerProxy("tests.factories", process)
    proxy.setLevel(LogLevel.ERROR)

    result = proxy.info("quiet")

    assert result.ok is False
    assert result.reason is DropReason.LOGGER_LEVEL
    assert calls == []


def test_create_console_writes_to_a_custom_stream_target(event_factory: Callable[[dict[str, Any] | None], LogEvent]) -> None:
    target = StringIO()
    appearance = ConsoleAppearance(stream=ConsoleStream.CUSTOM, stream_target=target, no_color=True)

    console = create_console(appearance)
    console.emit(event_factory({"message": "to the custom target"}), colorize=False)
    console.flush()

    assert "to the custom target" in target.getvalue()
