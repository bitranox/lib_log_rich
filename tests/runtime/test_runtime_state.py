from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

from lib_log_rich.domain import ContextBinder, LogLevel, SeverityMonitor
from lib_log_rich.runtime._settings import PayloadLimits
from lib_log_rich.runtime._state import (
    LoggingRuntime,
    clear_runtime,
    current_runtime,
    is_initialised,
    runtime_initialisation,
    set_runtime,
)


_DUPLICATE_ERROR_MESSAGE = "lib_log_rich.init() cannot be called twice without shutdown(); call lib_log_rich.shutdown() first"


@pytest.fixture(autouse=True)
def runtime_state_clean() -> Iterator[None]:
    clear_runtime()
    yield
    clear_runtime()


def _make_runtime(*, service: str = "svc") -> LoggingRuntime:
    binder = ContextBinder()
    monitor = SeverityMonitor()

    def process(**payload: object) -> dict[str, object]:
        return dict(payload)

    def capture_dump(**kwargs: object) -> str:
        return f"dump:{kwargs.get('min_level', 'any')}"

    def shutdown() -> None:
        return None

    return LoggingRuntime(
        binder=binder,
        process=process,
        capture_dump=capture_dump,
        shutdown_async=shutdown,
        queue=None,
        service=service,
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        graylog_level=LogLevel.ERROR,
        severity_monitor=monitor,
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )


def test_set_runtime_installs_singleton() -> None:
    runtime = _make_runtime()
    set_runtime(runtime)

    assert is_initialised() is True
    assert current_runtime() is runtime


def test_clear_runtime_resets_state() -> None:
    runtime = _make_runtime()
    set_runtime(runtime)
    clear_runtime()

    assert is_initialised() is False
    with pytest.raises(RuntimeError):
        current_runtime()


def test_set_runtime_twice_raises_duplicate_error() -> None:
    set_runtime(_make_runtime())
    with pytest.raises(RuntimeError, match=re.escape(_DUPLICATE_ERROR_MESSAGE)):
        set_runtime(_make_runtime(service="other"))


def test_runtime_initialisation_guard_detects_in_progress() -> None:
    with runtime_initialisation() as install:
        with pytest.raises(RuntimeError, match="already running in another thread"):
            with runtime_initialisation():
                pass
        install(_make_runtime())

    assert is_initialised() is True


def test_runtime_initialisation_without_install_raises() -> None:
    with pytest.raises(RuntimeError, match="initialisation guard exited without installing"):
        with runtime_initialisation():
            pass


def test_runtime_initialisation_rejects_second_install() -> None:
    first = _make_runtime()
    second = _make_runtime(service="other")
    with runtime_initialisation() as install:
        install(first)
        with pytest.raises(RuntimeError, match=re.escape(_DUPLICATE_ERROR_MESSAGE)):
            install(second)
