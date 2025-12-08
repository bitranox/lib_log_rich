from __future__ import annotations

from typing import Any

import pytest

from lib_log_rich.runtime._factories import (
    FeatureFlags,
    SystemIdentityProvider,
    clear_identity_cache,
    create_structured_backends,
)
from tests.os_markers import OS_AGNOSTIC

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
