from __future__ import annotations

import threading
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from lib_log_rich import bind, dump, get, init, logdemo, shutdown
from lib_log_rich import runtime
from lib_log_rich.domain.levels import LogLevel
from tests.os_markers import OS_AGNOSTIC

pytestmark = [OS_AGNOSTIC]


JsonObject = dict[str, Any]


def _ensure_asyncio_plugin() -> None:
    try:
        __import__("pytest_asyncio")
    except ModuleNotFoundError as exc:
        raise RuntimeError("pytest-asyncio must be installed; run pip install pytest-asyncio") from exc


_ensure_asyncio_plugin()


@pytest.fixture(autouse=True)
def cradle_runtime() -> Iterator[None]:
    try:
        yield
    finally:
        try:
            shutdown()
        except RuntimeError:
            pass


def record_json_event(message: str, *, extra: dict[str, object] | None = None) -> JsonObject:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse", request_id="r1"):
        get("poet.muse").info(message, extra=extra or {})
    entries = cast(list[JsonObject], json.loads(dump(dump_format="json")))
    return entries[0]


def configure_runtime_with_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_SERVICE", "env-service")
    monkeypatch.setenv("LOG_ENVIRONMENT", "env-stage")
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "error")
    monkeypatch.setenv("LOG_QUEUE_ENABLED", "0")
    init(service="ignored", environment="ignored", queue_enabled=True, enable_graylog=False)


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
    """Factory matching ``RichConsoleAdapter`` signature for monkeypatching."""

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


def test_init_rejects_non_positive_ring_buffer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_RING_BUFFER_SIZE", "0")

    with pytest.raises(ValueError, match="LOG_RING_BUFFER_SIZE"):
        runtime.init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)


def test_init_rejects_non_positive_ring_buffer_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_RING_BUFFER_SIZE", raising=False)

    with pytest.raises(ValueError, match="ring_buffer_size"):
        runtime.init(
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

    runtime.init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    snapshot = runtime.inspect_runtime()
    assert snapshot.console_styles is not None
    assert snapshot.console_styles["INFO"] == "bright_white"


def test_console_palette_honours_code_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_CONSOLE_STYLES", "ERROR=bold red")
    monkeypatch.setattr("lib_log_rich.runtime.RichConsoleAdapter", create_recording_console)
    monkeypatch.setattr("lib_log_rich.runtime._factories.RichConsoleAdapter", create_recording_console)

    runtime.init(service="svc", environment="env", queue_enabled=False, enable_graylog=False, console_styles={"ERROR": "bold red"})
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


def test_dump_context_filter_exact() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="alpha"):
        get("poet.muse").info("alpha message")
    with bind(job_id="beta"):
        get("poet.muse").info("beta message")

    payload = dump(dump_format="json", context_filters={"job_id": "alpha"})
    entries = json.loads(payload)
    assert len(entries) == 1
    assert entries[0]["message"] == "alpha message"


def test_dump_extra_filter_icontains() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="alpha"):
        get("poet.muse").info("alpha", extra={"request": "ABC-123"})
        get("poet.muse").info("beta", extra={"request": "xyz-123"})

    payload = dump(dump_format="json", extra_filters={"request": {"icontains": "abc"}})
    entries = json.loads(payload)
    assert [entry["message"] for entry in entries] == ["alpha"]


def test_dump_regex_filter_requires_flag() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="alpha"):
        get("poet.muse").info("msg", extra={"request": "ABC-123"})

    with pytest.raises(ValueError):
        dump(dump_format="json", extra_filters={"request": {"pattern": "^ABC"}})


def test_dump_regex_filter_accepts_matches() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="alpha"):
        get("poet.muse").info("alpha", extra={"request": "ABC-123"})
    with bind(job_id="beta"):
        get("poet.muse").info("beta", extra={"request": "XYZ-555"})

    payload = dump(
        dump_format="json",
        extra_filters={"request": {"pattern": "^ABC", "regex": True}},
    )
    entries = json.loads(payload)
    assert len(entries) == 1
    assert entries[0]["message"] == "alpha"


def test_dump_creates_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "latest.txt"
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse"):
        get("poet.muse").info("line")
    payload = dump(dump_format="text", path=target)
    assert target.read_text(encoding="utf-8") == payload
