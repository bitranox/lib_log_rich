from __future__ import annotations

import contextlib
import json
import math
import os
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence, cast

import pytest

import lib_log_rich.application.use_cases.process_event as process_event
from lib_log_rich import bind, dump, getLogger, logdemo, runtime, shutdown
from lib_log_rich.application.ports.identity import SystemIdentityPort
from lib_log_rich.domain.context import ContextBinder, LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.identity import SystemIdentity
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.runtime import RuntimeConfig
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


JsonObject = dict[str, Any]


def _ensure_asyncio_plugin() -> None:
    try:
        __import__("pytest_asyncio")
    except ModuleNotFoundError as exc:
        raise RuntimeError("pytest-asyncio must be installed; run pip install pytest-asyncio") from exc


_ensure_asyncio_plugin()


def init_runtime(**kwargs: Any) -> None:
    runtime.init(RuntimeConfig(**kwargs))


@pytest.fixture(autouse=True)
def cradle_runtime() -> Iterator[None]:
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            shutdown()


def record_json_event(message: str, *, extra: dict[str, object] | None = None) -> JsonObject:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse", request_id="r1"):
        getLogger("poet.muse").info(message, extra=extra or {})
    entries = cast(list[JsonObject], json.loads(dump(dump_format="json")))
    return entries[0]


def configure_runtime_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_SERVICE", "env-service")
    monkeypatch.setenv("LOG_ENVIRONMENT", "env-stage")
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "error")
    monkeypatch.setenv("LOG_QUEUE_ENABLED", "0")
    init_runtime(service="ignored", environment="ignored", queue_enabled=True, enable_graylog=False)


class QueueSpy(Protocol):
    maxsize: int
    drop_policy: str
    timeout: Optional[float]
    stop_timeout: Optional[float]
    stop_calls: list[tuple[bool, Optional[float]]]


def _install_queue_spy(monkeypatch: pytest.MonkeyPatch) -> Sequence[QueueSpy]:
    """Replace the queue adapter with a recording double and return created instances."""
    instances: list[RecordingQueueSpy] = []

    class RecordingQueueSpy:
        def __init__(
            self,
            *,
            worker: Optional[Callable[[LogEvent], None]] = None,
            maxsize: int = 2048,
            drop_policy: str = "block",
            on_drop: Optional[Callable[[LogEvent], None]] = None,
            timeout: Optional[float] = None,
            stop_timeout: Optional[float] = 5.0,
            diagnostic: Optional[Callable[[str, dict[str, object]], None]] = None,
            failure_reset_after: Optional[float] = 30.0,
        ) -> None:
            self._worker = worker
            self.maxsize = maxsize
            self.drop_policy = drop_policy
            self.on_drop = on_drop
            self.timeout = timeout
            self.stop_timeout = stop_timeout
            self.diagnostic = diagnostic
            self.failure_reset_after = failure_reset_after
            self.started = False
            self.stop_calls: list[tuple[bool, Optional[float]]] = []
            self.events: list[LogEvent] = []
            instances.append(self)

        def start(self) -> None:
            self.started = True

        def set_worker(self, worker: Callable[[LogEvent], None]) -> None:
            self._worker = worker

        def put(self, event: LogEvent) -> bool:
            self.events.append(event)
            if self._worker is not None:
                self._worker(event)
            return True

        def stop(self, *, drain: bool = True, timeout: Optional[float] = None) -> None:
            self.stop_calls.append((drain, timeout))

        def wait_until_idle(self, timeout: Optional[float] = None) -> bool:  # noqa: ARG002
            return True

        @property
        def worker_failed(self) -> bool:
            return False

    monkeypatch.setattr("lib_log_rich.adapters.queue.QueueAdapter", RecordingQueueSpy)
    monkeypatch.setattr("lib_log_rich.runtime._composition.QueueAdapter", RecordingQueueSpy)
    return instances


class RecordingConsole:
    def __init__(
        self,
        *,
        console: object | None = None,
        force_color: bool,
        no_color: bool,
        styles: Mapping[str, str] | None = None,
        format_preset: str | None = None,
        format_template: str | None = None,
    ) -> None:
        self.console = console
        self.force_color = force_color
        self.no_color = no_color
        self.styles = dict(styles or {})
        self.format_preset = format_preset
        self.format_template = format_template

    def emit(self, event: object, *, colorize: bool) -> None:  # noqa: D401, ARG002
        return None


class RecordingScrubber:
    def __init__(self, *, patterns: Mapping[str, str], replacement: str = "***") -> None:
        self.patterns = dict(patterns)
        self.replacement = replacement

    def scrub(self, event: object) -> object:  # noqa: D401, ARG002
        return event


def create_recording_console(
    *,
    console: object | None = None,
    force_color: bool,
    no_color: bool,
    styles: Mapping[str, str] | None = None,
    format_preset: str | None = None,
    format_template: str | None = None,
) -> RecordingConsole:
    """Create a RecordingConsole matching ``RichConsoleAdapter`` signature."""
    return RecordingConsole(
        console=console,
        force_color=force_color,
        no_color=no_color,
        styles=styles,
        format_preset=format_preset,
        format_template=format_template,
    )


def test_log_event_records_message() -> None:
    entry = record_json_event("hello world")
    assert entry["message"] == "hello world"


def test_log_event_records_extra_fields() -> None:
    entry = record_json_event("hello world", extra={"tone": "warm"})
    extra = cast(dict[str, Any], entry["extra"])
    assert extra["tone"] == "warm"


def test_text_dump_respects_template() -> None:
    init_runtime(
        service="ode",
        environment="stage",
        queue_enabled=False,
        enable_graylog=False,
        dump_format_template="{logger_name}:{message}",
    )
    with bind(job_id="verse"):
        getLogger("poet.muse").warning("caution")

    first_line = dump(dump_format="text", color=False).splitlines()[0]
    assert first_line.startswith("poet.muse:caution")


def test_html_dump_contains_table_markup() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse"):
        getLogger("poet.muse").error("alarm")

    html = dump(dump_format="html_table")
    assert "<table>" in html


def test_html_dump_contains_message_text() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse"):
        getLogger("poet.muse").error("alarm")

    html = dump(dump_format="html_table")
    assert "alarm" in html


def test_environment_override_replaces_service(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_runtime_with_env(monkeypatch)
    snapshot = runtime.inspect_runtime()
    assert snapshot.service == "env-service"


def test_environment_override_sets_console_level(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_runtime_with_env(monkeypatch)
    snapshot = runtime.inspect_runtime()
    assert snapshot.console_level is LogLevel.ERROR


def test_environment_override_disables_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_runtime_with_env(monkeypatch)
    snapshot = runtime.inspect_runtime()
    assert snapshot.queue_present is False


def test_environment_override_retains_critical_graylog(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_runtime_with_env(monkeypatch)
    snapshot = runtime.inspect_runtime()
    assert snapshot.graylog_level is LogLevel.CRITICAL


class _StubIdentity(SystemIdentityPort):
    def __init__(self, *, user: str | None, host: str | None, pid: int | None = None) -> None:
        normalised_host = host.split(".", 1)[0] if host else host
        self._identity = SystemIdentity(user_name=user, hostname=normalised_host, process_id=pid or os.getpid())

    def resolve_identity(self) -> SystemIdentity:
        return self._identity


def test_refresh_context_cached_identity() -> None:
    binder = ContextBinder()
    cached = LogContext(
        service="svc",
        environment="env",
        job_id="job",
        process_id=os.getpid(),
        hostname="cached-host",
        user_name="cached-user",
    )
    binder.deserialize({"version": 1, "stack": [cached.to_dict(include_none=True)]})

    identity = _StubIdentity(user="new-user", host="new-host")

    refreshed = process_event.refresh_context(binder, identity)

    assert refreshed.hostname == "cached-host"
    assert refreshed.user_name == "cached-user"


def test_refresh_context_refills_missing_identity() -> None:
    binder = ContextBinder()
    missing = LogContext(service="svc", environment="env", job_id="job", process_id=os.getpid())
    binder.deserialize({"version": 1, "stack": [missing.to_dict(include_none=True)]})

    identity = _StubIdentity(user="svc-user", host="example.local")

    refreshed = process_event.refresh_context(binder, identity)

    assert refreshed.hostname == "example"
    assert refreshed.user_name == "svc-user"


def test_queue_stop_timeout_defaults_to_five_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    instances = _install_queue_spy(monkeypatch)
    init_runtime(service="svc", environment="env", queue_enabled=True, enable_graylog=False)
    assert len(instances) == 1
    assert instances[0].stop_timeout == 5.0


def test_queue_maxsize_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_QUEUE_MAXSIZE", "512")
    instances = _install_queue_spy(monkeypatch)
    init_runtime(service="svc", environment="env", queue_enabled=True, enable_graylog=False)
    assert len(instances) == 1
    assert instances[0].maxsize == 512


def test_queue_full_policy_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_QUEUE_FULL_POLICY", "DROP")
    instances = _install_queue_spy(monkeypatch)
    init_runtime(service="svc", environment="env", queue_enabled=True, enable_graylog=False)
    assert len(instances) == 1
    assert instances[0].drop_policy == "drop"


def test_queue_put_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_QUEUE_PUT_TIMEOUT", "2.5")
    instances = _install_queue_spy(monkeypatch)
    init_runtime(service="svc", environment="env", queue_enabled=True, enable_graylog=False)
    assert len(instances) == 1
    assert instances[0].timeout == 2.5


def test_queue_stop_timeout_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_QUEUE_STOP_TIMEOUT", "1.5")
    instances = _install_queue_spy(monkeypatch)

    init_runtime(service="svc", environment="env", queue_enabled=True, enable_graylog=False)
    with bind(job_id="job"):
        getLogger("tests.queue").info("event")
    shutdown()

    assert len(instances) == 1
    stop_timeout = instances[0].stop_timeout
    assert stop_timeout is not None and math.isclose(stop_timeout, 1.5, rel_tol=1e-9)
    assert instances[0].stop_calls[-1] == (True, None)


def test_init_rejects_non_positive_ring_buffer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_RING_BUFFER_SIZE", "0")

    with pytest.raises(ValueError, match="LOG_RING_BUFFER_SIZE"):
        init_runtime(service="svc", environment="env", queue_enabled=False, enable_graylog=False)


def test_init_rejects_non_positive_ring_buffer_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_RING_BUFFER_SIZE", raising=False)

    with pytest.raises(ValueError, match="ring_buffer_size"):
        init_runtime(
            service="svc",
            environment="env",
            queue_enabled=False,
            enable_graylog=False,
            ring_buffer_size=0,
        )


def test_console_palette_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_STYLES", "INFO=bright_white")
    monkeypatch.setattr("lib_log_rich.runtime.RichConsoleAdapter", create_recording_console)
    monkeypatch.setattr("lib_log_rich.runtime._factories.RichConsoleAdapter", create_recording_console)

    init_runtime(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    snapshot = runtime.inspect_runtime()
    assert snapshot.console_styles is not None
    assert snapshot.console_styles["INFO"] == "bright_white"


def test_queue_worker_error_surfaces_via_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_QUEUE_ENABLED", raising=False)
    diagnostics: list[tuple[str, dict[str, object]]] = []

    def diagnostic_hook(name: str, payload: dict[str, object]) -> None:
        diagnostics.append((name, payload))

    init_runtime(
        service="svc",
        environment="env",
        queue_enabled=True,
        enable_graylog=False,
        diagnostic_hook=diagnostic_hook,
    )
    try:
        queue = runtime.current_runtime().queue
        assert queue is not None

        debug = queue.debug()
        original_worker = debug.current_worker()
        failed = False

        def flaky_worker(event: LogEvent) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("boom")
            if original_worker is not None:
                original_worker(event)

        queue.set_worker(flaky_worker)

        with bind(job_id="job"):
            getLogger("tests.queue").info("first")
            getLogger("tests.queue").info("second")
        assert queue.wait_until_idle(timeout=1.0) is True
        time.sleep(0.01)
    finally:
        shutdown()

    assert any(name == "queue_worker_error" for name, _ in diagnostics)


def test_console_palette_honours_code_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_STYLES", "ERROR=bold red")
    monkeypatch.setattr("lib_log_rich.runtime.RichConsoleAdapter", create_recording_console)
    monkeypatch.setattr("lib_log_rich.runtime._factories.RichConsoleAdapter", create_recording_console)

    init_runtime(service="svc", environment="env", queue_enabled=False, enable_graylog=False, console_styles={"ERROR": "bold red"})
    snapshot = runtime.inspect_runtime()
    assert snapshot.console_styles is not None
    assert snapshot.console_styles["ERROR"] == "bold red"


def test_scrubber_patterns_merge_code_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", r"secret=MASK,token=\d+")
    holder: RecordingScrubber | None = None

    def capture_scrubber(*, patterns: Mapping[str, str], replacement: str = "***") -> RecordingScrubber:
        nonlocal holder
        holder = RecordingScrubber(patterns=patterns, replacement=replacement)
        return holder

    monkeypatch.setattr(runtime, "RegexScrubber", capture_scrubber)

    init_runtime(
        service="svc",
        environment="env",
        queue_enabled=False,
        enable_graylog=False,
        scrub_patterns={"password": r"pass.+"},
    )
    assert holder is not None and holder.patterns == {"password": r"pass.+", "secret": "MASK", "token": r"\d+"}


def test_logdemo_reports_theme(tmp_path: Path) -> None:
    outcome = logdemo(
        theme="classic",
        enable_graylog=False,
        enable_journald=False,
        enable_eventlog=False,
        dump_format="text",
        dump_path=tmp_path / "demo-log.txt",
    )
    assert outcome.theme == "classic"


def test_logdemo_reports_backend_choices(tmp_path: Path) -> None:
    outcome = logdemo(
        theme="classic",
        enable_graylog=False,
        enable_journald=False,
        enable_eventlog=False,
        dump_format="text",
        dump_path=tmp_path / "demo-log.txt",
    )
    assert outcome.backends.graylog is False
    assert outcome.backends.journald is False
    assert outcome.backends.eventlog is False


def test_get_before_init_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        getLogger("poet.muse")


def test_graylog_level_follows_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_GRAYLOG_LEVEL", "error")

    init_runtime(
        service="svc",
        environment="env",
        queue_enabled=False,
        enable_graylog=True,
        graylog_endpoint=("localhost", 12201),
    )
    snapshot = runtime.inspect_runtime()
    assert snapshot.graylog_level is LogLevel.ERROR


def test_console_theme_is_stored_on_runtime() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        getLogger("poet.muse").info("coloured line")

    snapshot = runtime.inspect_runtime()
    assert snapshot.theme == "classic"


def test_init_twice_requires_shutdown() -> None:
    init_runtime(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    with pytest.raises(RuntimeError, match=r"shutdown\(\)"):
        init_runtime(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    shutdown()


def test_parallel_init_guard_blocks_concurrent_initialisation(monkeypatch: pytest.MonkeyPatch) -> None:
    start = threading.Event()
    finish = threading.Event()

    original_build = runtime.build_runtime_settings

    def slow_build_runtime_settings(*, config: RuntimeConfig) -> runtime.RuntimeSettings:
        start.set()
        finish.wait(timeout=1.0)
        return original_build(config=config)

    monkeypatch.setattr(runtime, "build_runtime_settings", slow_build_runtime_settings)
    monkeypatch.setattr("lib_log_rich.runtime._api.build_runtime_settings", slow_build_runtime_settings)

    worker = threading.Thread(
        target=runtime.init,
        args=(RuntimeConfig(service="svc", environment="env", queue_enabled=False, enable_graylog=False),),
    )
    worker.start()
    if not start.wait(timeout=1.0):
        finish.set()
        worker.join(timeout=1.0)
        pytest.fail("Runtime initialisation did not start in time")

    with pytest.raises(RuntimeError, match="already running"):
        runtime.init(RuntimeConfig(service="other", environment="env", queue_enabled=False, enable_graylog=False))

    finish.set()
    worker.join(timeout=1.0)
    runtime.shutdown()


def test_console_theme_colours_text_dump() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        getLogger("poet.muse").info("coloured line")

    payload = dump(dump_format="text", color=True)
    assert "[36m" in payload


def test_html_txt_dump_includes_markup() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        getLogger("poet.muse").info("coloured line")

    payload = dump(dump_format="html_txt", color=True)
    assert "<span" in payload


def test_html_txt_dump_includes_message() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        getLogger("poet.muse").info("coloured line")

    payload = dump(dump_format="html_txt", color=True)
    assert "coloured line" in payload


def test_runtime_exposes_severity_metrics() -> None:
    init_runtime(service="svc", environment="mon", queue_enabled=False, enable_graylog=False)
    assert runtime.max_level_seen() is None

    with bind(job_id="metrics"):
        getLogger("svc.worker").info("started")
        getLogger("svc.worker").error("boom")

    snapshot = runtime.severity_snapshot()
    assert snapshot.highest is LogLevel.ERROR
    assert snapshot.total_events == 2
    assert snapshot.counts[LogLevel.INFO] == 1
    assert snapshot.counts[LogLevel.ERROR] == 1
    assert snapshot.thresholds[LogLevel.WARNING] == 1
    assert snapshot.thresholds[LogLevel.ERROR] == 1
    assert snapshot.dropped_total == 0
    assert snapshot.drops_by_reason["rate_limited"] == 0

    runtime.reset_severity_metrics()
    assert runtime.max_level_seen() is None
    cleared = runtime.severity_snapshot()
    assert cleared.total_events == 0
    assert all(count == 0 for count in cleared.counts.values())
    assert cleared.dropped_total == 0


@pytest.mark.asyncio
async def test_shutdown_async_available_inside_running_loop() -> None:
    init_runtime(service="svc", environment="async", queue_enabled=False, enable_graylog=False)
    with pytest.raises(RuntimeError, match="await lib_log_rich.shutdown_async"):
        runtime.shutdown()
    await runtime.shutdown_async()


def test_queue_survives_adapter_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostics: list[tuple[str, dict[str, object]]] = []
    flushed = threading.Event()

    class RaisingConsole:
        def __init__(
            self,
            *,
            console: object | None = None,
            force_color: bool,
            no_color: bool,
            styles: Mapping[str, str] | None = None,
            format_preset: str | None = None,
            format_template: str | None = None,
        ) -> None:
            self.console = console
            self.force_color = force_color
            self.no_color = no_color
            self.styles = dict(styles or {})
            self.format_preset = format_preset
            self.format_template = format_template

        def emit(self, event: object, *, colorize: bool) -> None:  # noqa: D401, ARG002
            raise RuntimeError("console boom")

    def diagnostic_hook(name: str, payload: dict[str, object]) -> None:
        diagnostics.append((name, payload))
        if name == "adapter_error":
            flushed.set()

    monkeypatch.setattr("lib_log_rich.runtime.RichConsoleAdapter", RaisingConsole)
    monkeypatch.setattr("lib_log_rich.runtime._factories.RichConsoleAdapter", RaisingConsole)

    init_runtime(
        service="svc",
        environment="env",
        queue_enabled=True,
        enable_graylog=False,
        diagnostic_hook=diagnostic_hook,
    )

    try:
        with bind(job_id="job", request_id="req"):
            getLogger("tests.logger").info("message")
        assert flushed.wait(timeout=1.0)
        shutdown()
    finally:
        with contextlib.suppress(RuntimeError):
            shutdown()

    assert any(name == "adapter_error" for name, _ in diagnostics)


def test_shutdown_raises_and_preserves_runtime_when_queue_stop_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    init_runtime(
        service="svc",
        environment="env",
        queue_enabled=True,
        enable_graylog=False,
    )
    try:
        active = runtime.current_runtime()
        assert active.queue is not None
        queue = active.queue
        original_stop = queue.stop

        def failing_stop(*, drain: bool = True, timeout: float | None = None) -> None:  # noqa: D401, ARG002
            raise RuntimeError("stop boom")

        queue.stop = failing_stop  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="stop boom"):
            runtime.shutdown()

        assert runtime.is_initialised() is True

        queue.stop = original_stop  # type: ignore[assignment]
        runtime.shutdown()
        assert runtime.is_initialised() is False
    finally:
        if runtime.is_initialised():
            runtime.shutdown()


def test_dump_context_filter_exact() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="alpha"):
        getLogger("poet.muse").info("alpha message")
    with bind(job_id="beta"):
        getLogger("poet.muse").info("beta message")

    payload = dump(dump_format="json", context_filters={"job_id": "alpha"})
    entries = json.loads(payload)
    assert len(entries) == 1
    assert entries[0]["message"] == "alpha message"


def test_dump_extra_filter_icontains() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="alpha"):
        getLogger("poet.muse").info("alpha", extra={"request": "ABC-123"})
        getLogger("poet.muse").info("beta", extra={"request": "xyz-123"})

    payload = dump(dump_format="json", extra_filters={"request": {"icontains": "abc"}})
    entries = json.loads(payload)
    assert [entry["message"] for entry in entries] == ["alpha"]


def test_dump_regex_filter_requires_flag() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="alpha"):
        getLogger("poet.muse").info("msg", extra={"request": "ABC-123"})

    with pytest.raises(ValueError):
        dump(dump_format="json", extra_filters={"request": {"pattern": "^ABC"}})


def test_dump_regex_filter_accepts_matches() -> None:
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="alpha"):
        getLogger("poet.muse").info("alpha", extra={"request": "ABC-123"})
    with bind(job_id="beta"):
        getLogger("poet.muse").info("beta", extra={"request": "XYZ-555"})

    payload = dump(
        dump_format="json",
        extra_filters={"request": {"pattern": "^ABC", "regex": True}},
    )
    entries = json.loads(payload)
    assert len(entries) == 1
    assert entries[0]["message"] == "alpha"


def test_dump_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "latest.txt"
    init_runtime(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse"):
        getLogger("poet.muse").info("line")
    payload = dump(dump_format="text", path=target)
    assert target.read_text(encoding="utf-8") == payload
