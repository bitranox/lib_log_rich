from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from lib_log_rich import bind, dump, get, init, logdemo, shutdown
from lib_log_rich import runtime
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


def _ensure_asyncio_plugin() -> None:
    try:
        __import__("pytest_asyncio")
    except ModuleNotFoundError as exc:
        raise RuntimeError("pytest-asyncio must be installed; run pip install pytest-asyncio") from exc


_ensure_asyncio_plugin()


@pytest.fixture(autouse=True)
def cradle_runtime() -> None:
    try:
        yield
    finally:
        try:
            shutdown()
        except RuntimeError:
            pass


def record_json_event(message: str, *, extra: dict[str, object] | None = None) -> dict[str, object]:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse", request_id="r1"):
        get("poet.muse").info(message, extra=extra or {})
    return json.loads(dump(dump_format="json"))[0]


def configure_runtime_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_SERVICE", "env-service")
    monkeypatch.setenv("LOG_ENVIRONMENT", "env-stage")
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "error")
    monkeypatch.setenv("LOG_QUEUE_ENABLED", "0")
    init(service="ignored", environment="ignored", queue_enabled=True, enable_graylog=False)


class RecordingConsole:
    def __init__(self, *, force_color: bool, no_color: bool, styles=None, format_preset=None, format_template=None) -> None:  # noqa: ANN001,D401
        self.styles = dict(styles or {})

    def emit(self, event, *, colorize: bool) -> None:  # noqa: ANN001, D401, ARG002
        return None


class RecordingScrubber:
    def __init__(self, *, patterns: dict[str, str]) -> None:  # noqa: ANN001, D401
        self.patterns = dict(patterns)

    def scrub(self, event):  # noqa: ANN001, D401, ARG002
        return event


def test_log_event_records_message() -> None:
    entry = record_json_event("hello world")
    assert entry["message"] == "hello world"


def test_log_event_records_extra_fields() -> None:
    entry = record_json_event("hello world", extra={"tone": "warm"})
    assert entry["extra"]["tone"] == "warm"


def test_text_dump_respects_template() -> None:
    init(
        service="ode",
        environment="stage",
        queue_enabled=False,
        enable_graylog=False,
        dump_format_template="{logger_name}:{message}",
    )
    with bind(job_id="verse"):
        get("poet.muse").warning("caution")

    first_line = dump(dump_format="text", color=False).splitlines()[0]
    assert first_line.startswith("poet.muse:caution")


def test_html_dump_contains_table_markup() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse"):
        get("poet.muse").error("alarm")

    html = dump(dump_format="html_table")
    assert "<table>" in html


def test_html_dump_contains_message_text() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse"):
        get("poet.muse").error("alarm")

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


def test_console_palette_honours_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_STYLES", "INFO=bright_white")
    monkeypatch.setattr(runtime, "RichConsoleAdapter", lambda **kwargs: RecordingConsole(**kwargs))

    runtime.init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    snapshot = runtime.inspect_runtime()
    assert snapshot.console_styles is not None
    assert snapshot.console_styles["INFO"] == "bright_white"


def test_console_palette_honours_code_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_STYLES", "ERROR=bold red")
    monkeypatch.setattr(runtime, "RichConsoleAdapter", lambda **kwargs: RecordingConsole(**kwargs))

    runtime.init(service="svc", environment="env", queue_enabled=False, enable_graylog=False, console_styles={"ERROR": "bold red"})
    snapshot = runtime.inspect_runtime()
    assert snapshot.console_styles is not None
    assert snapshot.console_styles["ERROR"] == "bold red"


def test_scrubber_patterns_merge_code_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_SCRUB_PATTERNS", r"secret=MASK,token=\d+")
    holder: RecordingScrubber | None = None

    def capture_scrubber(**kwargs):
        nonlocal holder
        holder = RecordingScrubber(**kwargs)
        return holder

    monkeypatch.setattr(runtime, "RegexScrubber", capture_scrubber)

    runtime.init(
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
    assert outcome["theme"] == "classic"


def test_logdemo_reports_backend_choices(tmp_path: Path) -> None:
    outcome = logdemo(
        theme="classic",
        enable_graylog=False,
        enable_journald=False,
        enable_eventlog=False,
        dump_format="text",
        dump_path=tmp_path / "demo-log.txt",
    )
    assert outcome["backends"] == {"graylog": False, "journald": False, "eventlog": False}


def test_get_before_init_raises_runtime_error() -> None:
    with pytest.raises(RuntimeError):
        get("poet.muse")


def test_graylog_level_follows_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_GRAYLOG_LEVEL", "error")

    runtime.init(
        service="svc",
        environment="env",
        queue_enabled=False,
        enable_graylog=True,
        graylog_endpoint=("localhost", 12201),
    )
    snapshot = runtime.inspect_runtime()
    assert snapshot.graylog_level is LogLevel.ERROR


def test_console_theme_is_stored_on_runtime() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        get("poet.muse").info("coloured line")

    snapshot = runtime.inspect_runtime()
    assert snapshot.theme == "classic"


def test_init_twice_requires_shutdown() -> None:
    init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    with pytest.raises(RuntimeError, match=r"shutdown\(\)"):
        init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    shutdown()


def test_console_theme_colours_text_dump() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        get("poet.muse").info("coloured line")

    payload = dump(dump_format="text", color=True)
    assert "[36m" in payload


def test_html_txt_dump_includes_markup() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        get("poet.muse").info("coloured line")

    payload = dump(dump_format="html_txt", color=True)
    assert "<span" in payload


def test_html_txt_dump_includes_message() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        get("poet.muse").info("coloured line")

    payload = dump(dump_format="html_txt", color=True)
    assert "coloured line" in payload


@pytest.mark.asyncio
async def test_shutdown_async_available_inside_running_loop():
    init(service="svc", environment="async", queue_enabled=False, enable_graylog=False)
    with pytest.raises(RuntimeError, match="await lib_log_rich.shutdown_async"):
        runtime.shutdown()
    await runtime.shutdown_async()


def test_queue_survives_adapter_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostics: list[tuple[str, dict[str, object]]] = []
    flushed = threading.Event()

    class RaisingConsole:
        def __init__(self, *, force_color: bool, no_color: bool, styles=None, format_preset=None, format_template=None) -> None:  # noqa: ANN001, D401
            self.styles = styles

        def emit(self, event, *, colorize: bool) -> None:  # noqa: ANN001, D401, ARG002
            raise RuntimeError("console boom")

    def diagnostic_hook(name: str, payload: dict[str, object]) -> None:
        diagnostics.append((name, payload))
        if name == "adapter_error":
            flushed.set()

    monkeypatch.setattr(runtime, "RichConsoleAdapter", RaisingConsole)
    monkeypatch.setattr(runtime._factories, "RichConsoleAdapter", RaisingConsole)

    init(
        service="svc",
        environment="env",
        queue_enabled=True,
        enable_graylog=False,
        diagnostic_hook=diagnostic_hook,
    )

    try:
        with bind(job_id="job", request_id="req"):
            get("tests.logger").info("message")
        assert flushed.wait(timeout=1.0)
        shutdown()
    finally:
        try:
            shutdown()
        except RuntimeError:
            pass

    assert any(name == "adapter_error" for name, _ in diagnostics)
