from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from lib_log_rich import bind, dump, get, hello_world, init, shutdown, summary_info
from lib_log_rich.__main__ import cli as cli_entry


def _runtime_state():
    from lib_log_rich import lib_log_rich as runtime

    return runtime._STATE  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def reset_runtime():
    try:
        yield
    finally:
        try:
            shutdown()
        except RuntimeError:
            pass


def test_init_and_logging_flow() -> None:
    init(service="svc", environment="test", queue_enabled=False, enable_graylog=False)

    with bind(job_id="job-1", request_id="req-1"):
        logger = get("tests.runtime")
        result = logger.info("hello", extra={"foo": "bar"})

    assert result["ok"] is True
    payload = json.loads(dump(dump_format="json"))
    assert payload[0]["message"] == "hello"
    assert payload[0]["extra"]["foo"] == "bar"


def test_dump_text_and_html() -> None:
    init(service="svc", environment="test", queue_enabled=False, enable_graylog=False)
    with bind(job_id="job-2"):
        logger = get("tests.dump")
        logger.error("failure")

    text = dump(dump_format="text")
    assert "failure" in text

    html = dump(dump_format="html")
    assert "<html" in html
    assert "PID Chain" in html


def test_dump_uses_default_text_format(monkeypatch: pytest.MonkeyPatch) -> None:
    init(
        service="svc",
        environment="test",
        queue_enabled=False,
        enable_graylog=False,
        text_format="{message}|{process_id}",
    )
    with bind(job_id="job-default-format"):
        logger = get("tests.dump")
        logger.info("ready")

    text_output = dump(dump_format="text")
    first_line = text_output.splitlines()[0]
    assert first_line.startswith("ready|")
    assert first_line.split("|")[1].strip().isdigit()
    shutdown()

    monkeypatch.setenv("LOG_DUMP_TEXT_FORMAT", "{logger_name}:{user_name}")
    init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    try:
        with bind(job_id="job-env"):
            get("tests.env").info("ok")
        text_env = dump(dump_format="text")
        assert text_env.splitlines()[0].startswith("tests.env:")
    finally:
        shutdown()


def test_summary_and_hello_world(capsys: pytest.CaptureFixture[str]) -> None:
    hello_world()
    captured = capsys.readouterr()
    assert "Hello World" in captured.out
    info = summary_info()
    assert "Info for lib_log_rich" in info


def test_shutdown_without_init_is_safe() -> None:
    with pytest.raises(RuntimeError):
        get("tests")


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    from lib_log_rich.domain.levels import LogLevel

    monkeypatch.setenv("LOG_SERVICE", "env-service")
    monkeypatch.setenv("LOG_ENVIRONMENT", "env")
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "error")
    monkeypatch.setenv("LOG_QUEUE_ENABLED", "0")

    init(service="cfg", environment="cfg", queue_enabled=True, enable_graylog=False)
    state = _runtime_state()
    assert state is not None
    assert state.service == "env-service"
    assert state.environment == "env"
    assert state.console_level is LogLevel.ERROR
    assert state.queue is None


def test_console_styles_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    created_consoles: list[object] = []

    class RecordingConsole:
        def __init__(self, *, force_color: bool, no_color: bool, styles=None) -> None:  # noqa: D401, ANN001
            self.force_color = force_color
            self.no_color = no_color
            self.styles = styles or {}
            created_consoles.append(self)

        def emit(self, event, *, colorize: bool) -> None:  # pragma: no cover - not needed
            return None

    import lib_log_rich.lib_log_rich as runtime

    monkeypatch.setattr(runtime, "RichConsoleAdapter", RecordingConsole)
    monkeypatch.setenv("LOG_CONSOLE_STYLES", "INFO=bright_white,ERROR=bold white on red")

    runtime.init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    try:
        assert created_consoles
        console = created_consoles[0]
        assert console.styles["INFO"] == "bright_white"
        assert console.styles["ERROR"] == "bold white on red"
    finally:
        runtime.shutdown()


def test_console_style_helpers() -> None:
    from lib_log_rich.domain import LogLevel
    from lib_log_rich.lib_log_rich import _merge_console_styles, _parse_console_styles

    parsed = _parse_console_styles("INFO=bright_white, ERROR=bold red , ,invalid")
    assert parsed == {"INFO": "bright_white", "ERROR": "bold red"}

    merged = _merge_console_styles({"debug": "dim", LogLevel.WARNING: "yellow"}, parsed)
    assert merged["DEBUG"] == "dim"
    assert merged["WARNING"] == "yellow"
    assert merged["INFO"] == "bright_white"


def test_scrub_patterns_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich.lib_log_rich as runtime

    recorded: dict[str, str] = {}

    class RecordingScrubber:
        def __init__(self, *, patterns: dict[str, str]) -> None:  # noqa: D401, ANN001
            recorded.update(patterns)

        def scrub(self, event):  # noqa: D401, ANN001
            return event

    monkeypatch.setattr(runtime, "RegexScrubber", RecordingScrubber)
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", "secret=REDACTED, token=\d+")

    runtime.init(
        service="svc",
        environment="env",
        queue_enabled=False,
        enable_graylog=False,
        scrub_patterns={"password": r"pass.+"},
    )
    try:
        assert recorded["password"] == r"pass.+"
        assert recorded["secret"] == "REDACTED"
        assert recorded["token"] == r"\d+"
    finally:
        runtime.shutdown()


def test_logdemo_emits_and_honours_theme(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich.lib_log_rich as runtime

    outputs: list[tuple[str, str]] = []
    applied_styles: dict[str, str] = {}

    class RecordingConsole:
        def __init__(self, *, force_color: bool, no_color: bool, styles=None) -> None:  # noqa: D401
            self.force_color = force_color
            self.no_color = no_color
            self.styles = styles or {}
            applied_styles.update(self.styles)

        def emit(self, event, *, colorize: bool) -> None:
            outputs.append((event.level.severity, event.message))

    monkeypatch.setattr(runtime, "RichConsoleAdapter", RecordingConsole)

    result = runtime.logdemo(theme="neon")
    events = result["events"]
    assert len(events) == 5
    assert result["backends"] == {"graylog": False, "journald": False, "eventlog": False}
    assert all(event.get("ok", True) for event in events)
    assert any(level == "error" for level, _ in outputs)
    assert applied_styles.get("INFO", "").lower() == "#39ff14"


def test_logdemo_requires_clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich as log

    log.init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    try:
        with pytest.raises(RuntimeError):
            log.logdemo()
    finally:
        log.shutdown()


def test_logdemo_unknown_theme() -> None:
    import lib_log_rich as log

    with pytest.raises(ValueError):
        log.logdemo(theme="unknown-theme")


def test_logdemo_dump_returns_payload(tmp_path: Path) -> None:
    import lib_log_rich as log

    target = tmp_path / "demo-output.json"
    result = log.logdemo(
        theme="classic",
        dump_format="json",
        dump_path=target,
        service="svc",
        environment="env",
    )

    assert result["dump"] is not None
    assert target.exists()
    payload = json.loads(result["dump"])
    assert isinstance(payload, list)
    assert payload[0]["context"]["service"] == "svc"


def test_logdemo_can_enable_graylog(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich.lib_log_rich as runtime

    recorded: list[str] = []

    class RecordingGraylog:
        def __init__(self, *, host: str, port: int, enabled: bool = True, timeout: float = 1.0, protocol: str = "tcp", use_tls: bool = False) -> None:
            self.host = host
            self.port = port
            self.protocol = protocol
            self.use_tls = use_tls

        def emit(self, event) -> None:  # noqa: ANN001
            recorded.append(event.message)

        async def flush(self) -> None:
            return None

    monkeypatch.setattr(runtime, "GraylogAdapter", RecordingGraylog)

    result = runtime.logdemo(enable_graylog=True, graylog_endpoint=("localhost", 1514))

    assert recorded
    assert result["backends"]["graylog"] is True


def test_logdemo_can_enable_journald(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich.lib_log_rich as runtime

    emitted: list[str] = []

    class RecordingJournald:
        def __init__(self, *, sender=None, service_field: str = "SERVICE") -> None:  # noqa: D401
            self.service_field = service_field

        def emit(self, event) -> None:  # noqa: ANN001
            emitted.append(event.message)

    monkeypatch.setattr(runtime, "JournaldAdapter", RecordingJournald)
    monkeypatch.setattr(runtime.sys, "platform", "linux")

    result = runtime.logdemo(enable_journald=True)

    assert emitted
    assert result["backends"]["journald"] is True


def test_logdemo_can_enable_eventlog(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich.lib_log_rich as runtime

    emitted: list[str] = []

    class RecordingEventLog:
        def __init__(self, *, reporter=None, event_ids=None) -> None:  # noqa: D401
            self.reporter = reporter

        def emit(self, event) -> None:  # noqa: ANN001
            emitted.append(event.message)

    monkeypatch.setattr(runtime, "WindowsEventLogAdapter", RecordingEventLog)
    monkeypatch.setattr(runtime.sys, "platform", "win32")

    result = runtime.logdemo(enable_eventlog=True)

    assert emitted
    assert result["backends"]["eventlog"] is True


def test_init_disables_journald_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich.lib_log_rich as runtime

    created_journald: list[object] = []
    created_eventlog: list[object] = []

    class RecordingJournald:
        def __init__(self) -> None:
            created_journald.append(self)

        def emit(self, *args, **kwargs) -> None:  # noqa: D401, ANN001
            return None

    class RecordingEventLog:
        def __init__(self) -> None:
            created_eventlog.append(self)

        def emit(self, *args, **kwargs) -> None:  # noqa: D401, ANN001
            return None

    monkeypatch.setattr(runtime, "JournaldAdapter", RecordingJournald)
    monkeypatch.setattr(runtime, "WindowsEventLogAdapter", RecordingEventLog)
    monkeypatch.setattr(runtime.sys, "platform", "win32")

    runtime.init(
        service="svc",
        environment="env",
        enable_journald=True,
        enable_eventlog=True,
        queue_enabled=False,
        enable_graylog=False,
    )
    try:
        assert not created_journald
        assert created_eventlog
    finally:
        runtime.shutdown()


def test_init_disables_eventlog_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich.lib_log_rich as runtime

    created_eventlog: list[object] = []

    class RecordingEventLog:
        def __init__(self) -> None:
            created_eventlog.append(self)

        def emit(self, *args, **kwargs) -> None:  # noqa: D401, ANN001
            return None

    monkeypatch.setattr(runtime, "WindowsEventLogAdapter", RecordingEventLog)
    monkeypatch.setattr(runtime.sys, "platform", "linux")

    runtime.init(
        service="svc",
        environment="env",
        enable_eventlog=True,
        enable_journald=False,
        queue_enabled=False,
        enable_graylog=False,
    )
    try:
        assert not created_eventlog
    finally:
        runtime.shutdown()


def test_cli_logdemo(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import lib_log_rich.lib_log_rich as runtime

    outputs: list[str] = []

    class RecordingConsole:
        def __init__(self, *, force_color: bool, no_color: bool, styles=None) -> None:  # noqa: D401
            self.force_color = force_color
            self.no_color = no_color
            self.styles = styles or {}

        def emit(self, event, *, colorize: bool) -> None:
            outputs.append(event.message)

    monkeypatch.setattr(runtime, "RichConsoleAdapter", RecordingConsole)

    graylog_events: list[str] = []

    class RecordingGraylog:
        def __init__(self, *, host: str, port: int, enabled: bool = True, timeout: float = 1.0, protocol: str = "tcp", use_tls: bool = False) -> None:
            self.host = host
            self.port = port
            self.protocol = protocol
            self.use_tls = use_tls

        def emit(self, event) -> None:  # noqa: ANN001
            graylog_events.append(event.message)

        async def flush(self) -> None:
            return None

    monkeypatch.setattr(runtime, "RichConsoleAdapter", RecordingConsole)
    monkeypatch.setattr(runtime, "GraylogAdapter", RecordingGraylog)

    runner = CliRunner()
    result = runner.invoke(
        cli_entry,
        [
            "logdemo",
            "--theme",
            "classic",
            "--dump-format",
            "json",
            "--dump-path",
            str(tmp_path / "dumps"),
            "--enable-graylog",
            "--graylog-endpoint",
            "127.0.0.1:19000",
        ],
    )
    assert result.exit_code == 0
    assert "=== Theme: classic ===" in result.stdout
    assert "graylog -> 127.0.0.1:19000 via TCP" in result.stdout
    assert outputs  # demo emitted sample logs
    assert graylog_events


def test_logger_proxy_emits_all_levels() -> None:
    init(service="svc", environment="test", queue_enabled=False, enable_graylog=False)
    with bind(job_id="job-levels"):
        logger = get("tests.levels")
        results = [
            logger.debug("debug"),
            logger.info("info"),
            logger.warning("warn"),
            logger.error("error"),
            logger.critical("crit"),
        ]
    assert all(result["ok"] for result in results)
    shutdown()


def test_init_with_queue_and_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    import lib_log_rich.lib_log_rich as runtime
    from lib_log_rich.domain.events import LogEvent

    created_console: list[RecordingConsole] = []
    created_journald: list[RecordingBackend] = []
    created_eventlog: list[RecordingBackend] = []
    created_graylog: list[RecordingGraylog] = []
    created_queue: list[RecordingQueue] = []

    class RecordingBackend:
        def __init__(self) -> None:
            self.emitted: list[LogEvent] = []

        def emit(self, event: LogEvent) -> None:
            self.emitted.append(event)

    class RecordingConsole:
        def __init__(self, *, force_color: bool, no_color: bool, styles=None) -> None:  # noqa: D401, ANN001
            self.force_color = force_color
            self.no_color = no_color
            self.styles = styles or {}
            self.events: list[tuple[LogEvent, bool]] = []
            created_console.append(self)

        def emit(self, event: LogEvent, *, colorize: bool = True) -> None:
            self.events.append((event, colorize))

    class RecordingGraylog:
        def __init__(self, *, host: str, port: int, enabled: bool = True) -> None:
            self.host = host
            self.port = port
            self.enabled = enabled
            self.events: list[LogEvent] = []
            self.flushed = False
            created_graylog.append(self)

        def emit(self, event: LogEvent) -> None:
            self.events.append(event)

        async def flush(self) -> None:
            self.flushed = True

    class RecordingQueue:
        def __init__(self, worker):
            self.worker = worker
            self.started = False
            self.stopped: bool | None = None
            self.events: list[LogEvent] = []
            created_queue.append(self)

        def start(self) -> None:
            self.started = True

        def put(self, event: LogEvent) -> None:
            self.events.append(event)
            self.worker(event)

        def stop(self, *, drain: bool = True) -> None:
            self.stopped = drain

    def make_backend(store: list[RecordingBackend]) -> RecordingBackend:
        backend = RecordingBackend()
        store.append(backend)
        return backend

    monkeypatch.setattr(runtime, "RichConsoleAdapter", RecordingConsole)
    monkeypatch.setattr(runtime, "JournaldAdapter", lambda: make_backend(created_journald))
    monkeypatch.setattr(runtime, "WindowsEventLogAdapter", lambda: make_backend(created_eventlog))

    def _graylog_factory(*, host: str, port: int, enabled: bool = True, protocol: str = "tcp", use_tls: bool = False, timeout: float = 1.0) -> RecordingGraylog:  # noqa: ARG001
        instance = RecordingGraylog(host=host, port=port, enabled=enabled)
        instance.protocol = protocol
        instance.use_tls = use_tls
        instance.timeout = timeout
        return instance

    monkeypatch.setattr(runtime, "GraylogAdapter", _graylog_factory)
    monkeypatch.setattr(runtime, "QueueAdapter", RecordingQueue)
    monkeypatch.setenv("LOG_RATE_LIMIT", "5/2.5")
    monkeypatch.setenv("LOG_GRAYLOG_ENDPOINT", "gray.example:12201")

    runtime.init(
        service="svc",
        environment="prod",
        enable_journald=True,
        enable_eventlog=True,
        enable_graylog=True,
        queue_enabled=True,
        scrub_patterns={"secret": r".+"},
        console_styles={"INFO": "bright_white", "ERROR": "bold red"},
    )

    with runtime.bind(job_id="job-queue", request_id="req-1"):
        logger = runtime.get("tests.queue")
        result = logger.error("boom", extra={"secret": "do-not-leak", "user": "alice"})

    assert result["queued"] is True

    console_adapter = created_console[0]
    assert console_adapter.events
    event_from_console = console_adapter.events[0][0]
    assert event_from_console.extra["secret"] != "do-not-leak"
    assert console_adapter.styles["INFO"] == "bright_white"
    assert console_adapter.styles["ERROR"] == "bold red"

    journald_adapter = created_journald[0]
    assert journald_adapter.emitted
    if runtime.sys.platform.startswith("win"):
        if not created_eventlog:
            pytest.skip("Windows Event Log adapter unavailable on this runner")
        eventlog_adapter = created_eventlog[0]
        assert eventlog_adapter.emitted
    else:
        assert not created_eventlog

    queue = created_queue[0]
    assert queue.started is True
    assert queue.events

    graylog_adapter = created_graylog[0]
    assert graylog_adapter.events

    runtime.shutdown()
    assert queue.stopped is True
    assert graylog_adapter.flushed is True


def test_shutdown_runs_coroutine(monkeypatch: pytest.MonkeyPatch) -> None:
    from lib_log_rich.domain import ContextBinder
    from lib_log_rich.domain.levels import LogLevel
    from lib_log_rich import lib_log_rich as runtime

    events: list[str] = []

    class StubQueue:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self, *, drain: bool = True) -> None:
            events.append("queue_stopped")
            self.stopped = drain

    async def shutdown_async() -> None:
        events.append("shutdown")

    runtime._STATE = runtime.LoggingRuntime(
        binder=ContextBinder(),
        process=lambda **_: {"ok": True},
        capture_dump=lambda **_: "",
        shutdown_async=lambda: shutdown_async(),
        queue=StubQueue(),
        service="svc",
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
    )

    runtime.shutdown()
    assert events == ["queue_stopped", "shutdown"]
    assert runtime._STATE is None


def test_graylog_endpoint_invalid_string() -> None:
    from lib_log_rich import lib_log_rich as runtime

    result = runtime._coerce_graylog_endpoint("no-port", ("fallback", 12201))
    assert result == ("fallback", 12201)


def test_shutdown_with_sync_closure() -> None:
    from lib_log_rich.domain import ContextBinder
    from lib_log_rich.domain.levels import LogLevel
    from lib_log_rich import lib_log_rich as runtime

    events: list[str] = []

    class StubQueue:
        def __init__(self) -> None:
            self.stopped = False

        def stop(self, *, drain: bool = True) -> None:
            events.append("queue_stopped")
            self.stopped = drain

    def shutdown_sync() -> None:
        events.append("sync_shutdown")

    runtime._STATE = runtime.LoggingRuntime(
        binder=ContextBinder(),
        process=lambda **_: {"ok": True},
        capture_dump=lambda **_: "",
        shutdown_async=lambda: shutdown_sync(),
        queue=StubQueue(),
        service="svc",
        environment="test",
        console_level=LogLevel.INFO,
        backend_level=LogLevel.WARNING,
    )

    runtime.shutdown()
    assert events == ["queue_stopped", "sync_shutdown"]
    assert runtime._STATE is None
