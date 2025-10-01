from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib_log_rich import bind, dump, get, init, logdemo, shutdown
from lib_log_rich import lib_log_rich as runtime
from lib_log_rich.domain.levels import LogLevel


@pytest.fixture(autouse=True)
def cradle_runtime() -> None:
    """Ensure every test starts and ends with a clean logging cradle."""

    try:
        yield
    finally:
        try:
            shutdown()
        except RuntimeError:
            pass


def test_when_logger_whispers_we_bottle_the_memory() -> None:
    """Logging a message writes the story into the in-memory ledger."""

    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse", request_id="r1"):
        get("poet.muse").info("hello world", extra={"tone": "warm"})

    entry = json.loads(dump(dump_format="json"))[0]
    assert entry["message"] == "hello world"
    assert entry["extra"]["tone"] == "warm"


def test_when_text_dump_is_requested_the_lines_remain_plain() -> None:
    """Text dumps honour the template and stay uncoloured when asked."""

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


def test_when_html_dump_is_born_the_table_glows() -> None:
    """HTML dumps wrap the story in a familiar table shell."""

    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False)
    with bind(job_id="verse"):
        get("poet.muse").error("alarm")

    html = dump(dump_format="html_table")
    assert "<table>" in html and "alarm" in html


def test_when_environment_claims_authority_the_runtime_listens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables override init parameters for pivotal knobs."""

    monkeypatch.setenv("LOG_SERVICE", "env-service")
    monkeypatch.setenv("LOG_ENVIRONMENT", "env-stage")
    monkeypatch.setenv("LOG_CONSOLE_LEVEL", "error")
    monkeypatch.setenv("LOG_QUEUE_ENABLED", "0")

    init(service="ignored", environment="ignored", queue_enabled=True, enable_graylog=False)
    state = runtime._STATE  # type: ignore[attr-defined]
    assert state is not None and state.service == "env-service" and state.console_level is LogLevel.ERROR
    assert state.queue is None
    assert state.graylog_level is LogLevel.CRITICAL  # graylog disabled falls back to CRITICAL


def test_when_palette_overrides_arrive_the_console_receives_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """Console style overrides merge code and environment palettes."""

    seen_styles: dict[str, str] = {}

    class RecordingConsole:
        def __init__(
            self,
            *,
            force_color: bool,
            no_color: bool,
            styles=None,
            format_preset=None,
            format_template=None,
        ) -> None:  # noqa: ANN001,D401
            seen_styles.update(styles or {})

        def emit(self, event, *, colorize: bool) -> None:  # noqa: ANN001, D401, ARG002
            return None

    monkeypatch.setenv("LOG_CONSOLE_STYLES", "INFO=bright_white,ERROR=bold white on red")
    monkeypatch.setattr(runtime, "RichConsoleAdapter", RecordingConsole)

    runtime.init(service="svc", environment="env", queue_enabled=False, enable_graylog=False)
    assert seen_styles["INFO"] == "bright_white" and seen_styles["ERROR"] == "bold white on red"


def test_when_scrub_patterns_merge_every_secret_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scrubber patterns from code and environment blend gracefully."""

    recorded: dict[str, str] = {}

    class RecordingScrubber:
        def __init__(self, *, patterns: dict[str, str]) -> None:  # noqa: ANN001, D401
            recorded.update(patterns)

        def scrub(self, event):  # noqa: ANN001, D401, ARG002
            return event

    monkeypatch.setenv("LOG_SCRUB_PATTERNS", "secret=MASK,token=\\d+")
    monkeypatch.setattr(runtime, "RegexScrubber", RecordingScrubber)

    runtime.init(
        service="svc",
        environment="env",
        queue_enabled=False,
        enable_graylog=False,
        scrub_patterns={"password": r"pass.+"},
    )
    assert recorded == {"password": r"pass.+", "secret": "MASK", "token": r"\d+"}


def test_when_logdemo_celebates_it_reports_choices(tmp_path: Path) -> None:
    """The demo call summarises its actions for curious operators."""

    destination = tmp_path / "demo-log.txt"
    outcome = logdemo(
        theme="classic",
        enable_graylog=False,
        enable_journald=False,
        enable_eventlog=False,
        dump_format="text",
        dump_path=destination,
    )
    assert outcome["theme"] == "classic"
    assert outcome["backends"] == {"graylog": False, "journald": False, "eventlog": False}


def test_when_logger_is_sought_without_init_the_runtime_objects() -> None:
    """Calling `get` before `init` raises the documented reminder."""

    with pytest.raises(RuntimeError):
        get("poet.muse")


def test_when_graylog_level_is_configured_it_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_GRAYLOG_LEVEL", "error")

    runtime.init(
        service="svc",
        environment="env",
        queue_enabled=False,
        enable_graylog=True,
        graylog_endpoint=("localhost", 12201),
    )

    state = runtime._STATE  # type: ignore[attr-defined]
    assert state is not None and state.graylog_level is LogLevel.ERROR
    runtime.shutdown()


def test_when_console_theme_is_configured_dumps_use_it() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        get("poet.muse").info("coloured line")

    payload = dump(dump_format="text", color=True)
    state = runtime._STATE  # type: ignore[attr-defined]
    assert state is not None and state.theme == "classic"
    assert "[36m" in payload  # classic theme INFO is cyan


def test_when_html_txt_dump_colours_follow_theme() -> None:
    init(service="ode", environment="stage", queue_enabled=False, enable_graylog=False, console_theme="classic")
    with bind(job_id="verse"):
        get("poet.muse").info("coloured line")

    payload = dump(dump_format="html_txt", color=True)
    assert "<span" in payload and "coloured line" in payload
