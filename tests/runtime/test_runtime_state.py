from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

from lib_log_rich.application.use_cases._types import ProcessResult
from lib_log_rich.domain import ContextBinder, LogLevel, SeverityMonitor
from lib_log_rich.runtime._settings import PayloadLimits
from lib_log_rich.runtime._state import (
    LoggingRuntime,
    clear_runtime,
    current_runtime,
    get_minimum_log_level,
    is_initialised,
    runtime_initialisation,
    set_runtime,
)
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]

_DUPLICATE_ERROR_MESSAGE = "lib_log_rich.init() cannot be called twice without shutdown(); call lib_log_rich.shutdown() first"


def _mock_process(**payload: Any) -> ProcessResult:
    """Mock process function for test LoggingRuntime instances."""
    return ProcessResult(ok=True)


def _mock_capture_dump(**kwargs: Any) -> str:
    """Mock capture_dump function for test LoggingRuntime instances."""
    return ""


@pytest.fixture(autouse=True)
def runtime_state_clean() -> Iterator[None]:
    clear_runtime()
    yield
    clear_runtime()


def _make_runtime(*, service: str = "svc") -> LoggingRuntime:
    binder = ContextBinder()
    monitor = SeverityMonitor()

    def process(**payload: object) -> ProcessResult:
        return ProcessResult(ok=True)

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
        backend_enabled=False,
        graylog_level=LogLevel.ERROR,
        graylog_enabled=False,
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


def test_get_minimum_log_level_raises_when_not_initialized() -> None:
    """get_minimum_log_level() raises RuntimeError before init."""
    assert is_initialised() is False
    with pytest.raises(RuntimeError, match="lib_log_rich.init\\(\\) must be called"):
        get_minimum_log_level()


def test_get_minimum_log_level_returns_console_when_lowest() -> None:
    """Console level is returned when it's the most permissive."""
    runtime = LoggingRuntime(
        binder=ContextBinder(),
        process=_mock_process,
        capture_dump=_mock_capture_dump,
        shutdown_async=lambda: None,
        queue=None,
        service="svc",
        environment="test",
        console_level=LogLevel.DEBUG,
        backend_level=LogLevel.INFO,
        backend_enabled=False,
        graylog_level=LogLevel.WARNING,
        graylog_enabled=True,
        severity_monitor=SeverityMonitor(),
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )
    set_runtime(runtime)

    assert get_minimum_log_level() == LogLevel.DEBUG


def test_get_minimum_log_level_returns_backend_when_lowest() -> None:
    """Backend level is returned when it's the most permissive."""
    runtime = LoggingRuntime(
        binder=ContextBinder(),
        process=_mock_process,
        capture_dump=_mock_capture_dump,
        shutdown_async=lambda: None,
        queue=None,
        service="svc",
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.DEBUG,
        backend_enabled=True,  # Backend (journald/eventlog) is enabled
        graylog_level=LogLevel.WARNING,
        graylog_enabled=True,
        severity_monitor=SeverityMonitor(),
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )
    set_runtime(runtime)

    assert get_minimum_log_level() == LogLevel.DEBUG


def test_get_minimum_log_level_returns_graylog_when_lowest_and_enabled() -> None:
    """Graylog level is returned when it's the most permissive and Graylog is enabled."""
    runtime = LoggingRuntime(
        binder=ContextBinder(),
        process=_mock_process,
        capture_dump=_mock_capture_dump,
        shutdown_async=lambda: None,
        queue=None,
        service="svc",
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        backend_enabled=False,
        graylog_level=LogLevel.DEBUG,
        graylog_enabled=True,
        severity_monitor=SeverityMonitor(),
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )
    set_runtime(runtime)

    assert get_minimum_log_level() == LogLevel.DEBUG


def test_get_minimum_log_level_ignores_graylog_when_disabled() -> None:
    """Graylog level is ignored when Graylog is disabled."""
    runtime = LoggingRuntime(
        binder=ContextBinder(),
        process=_mock_process,
        capture_dump=_mock_capture_dump,
        shutdown_async=lambda: None,
        queue=None,
        service="svc",
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        backend_enabled=False,
        graylog_level=LogLevel.DEBUG,  # Would be lowest, but Graylog is disabled
        graylog_enabled=False,
        severity_monitor=SeverityMonitor(),
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )
    set_runtime(runtime)

    # Should return INFO (console), not DEBUG (disabled Graylog)
    assert get_minimum_log_level() == LogLevel.INFO


def test_get_minimum_log_level_includes_graylog_critical_when_enabled() -> None:
    """Graylog level CRITICAL is included when Graylog is explicitly enabled."""
    runtime = LoggingRuntime(
        binder=ContextBinder(),
        process=_mock_process,
        capture_dump=_mock_capture_dump,
        shutdown_async=lambda: None,
        queue=None,
        service="svc",
        environment="test",
        console_level=LogLevel.ERROR,
        backend_level=LogLevel.ERROR,
        backend_enabled=False,
        graylog_level=LogLevel.CRITICAL,  # User wants CRITICAL-only Graylog
        graylog_enabled=True,  # But Graylog IS enabled
        severity_monitor=SeverityMonitor(),
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )
    set_runtime(runtime)

    # Should return ERROR (minimum of console/backend), CRITICAL doesn't affect it
    assert get_minimum_log_level() == LogLevel.ERROR


def test_get_minimum_log_level_with_all_same_level() -> None:
    """Returns the common level when all three are identical."""
    runtime = LoggingRuntime(
        binder=ContextBinder(),
        process=_mock_process,
        capture_dump=_mock_capture_dump,
        shutdown_async=lambda: None,
        queue=None,
        service="svc",
        environment="test",
        console_level=LogLevel.WARNING,
        backend_level=LogLevel.WARNING,
        backend_enabled=False,
        graylog_level=LogLevel.WARNING,
        graylog_enabled=True,
        severity_monitor=SeverityMonitor(),
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )
    set_runtime(runtime)

    assert get_minimum_log_level() == LogLevel.WARNING


def test_get_minimum_log_level_works_with_mixed_levels() -> None:
    """Returns the minimum across a mix of levels."""
    runtime = LoggingRuntime(
        binder=ContextBinder(),
        process=_mock_process,
        capture_dump=_mock_capture_dump,
        shutdown_async=lambda: None,
        queue=None,
        service="svc",
        environment="test",
        console_level=LogLevel.ERROR,
        backend_level=LogLevel.INFO,
        backend_enabled=True,  # Backend is enabled
        graylog_level=LogLevel.WARNING,
        graylog_enabled=True,
        severity_monitor=SeverityMonitor(),
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )
    set_runtime(runtime)

    assert get_minimum_log_level() == LogLevel.INFO


def test_get_minimum_log_level_with_stdlib_integration() -> None:
    """Verify README example: setting stdlib root logger level with get_minimum_log_level()."""
    import logging

    runtime = LoggingRuntime(
        binder=ContextBinder(),
        process=_mock_process,
        capture_dump=_mock_capture_dump,
        shutdown_async=lambda: None,
        queue=None,
        service="svc",
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
        backend_enabled=False,
        graylog_level=LogLevel.ERROR,
        graylog_enabled=True,
        severity_monitor=SeverityMonitor(),
        theme=None,
        console_styles=None,
        limits=PayloadLimits(),
    )
    set_runtime(runtime)

    # This is the usage pattern documented in README.md
    min_level = get_minimum_log_level()
    assert min_level == LogLevel.INFO

    # Verify to_python_level() works and returns stdlib level
    stdlib_level = min_level.to_python_level()
    assert stdlib_level == logging.INFO

    # Verify setting stdlib logger doesn't raise
    root_logger = logging.getLogger()
    original_level = root_logger.level
    try:
        root_logger.setLevel(stdlib_level)
        assert root_logger.level == logging.INFO
    finally:
        # Restore original level for other tests
        root_logger.setLevel(original_level)
