"""Logging façade that wires domain, application, and adapter layers together.

Purpose
-------
Expose a minimal, ergonomic API for host applications to configure and use the
Rich-backed logging runtime described in :mod:`docs.systemdesign.konzept`. This
module is the single composition point that translates keyword arguments and
environment overrides into the Clean Architecture wiring.

Contents
--------
* Dataclasses: :class:`LoggingRuntime` – captures live runtime wiring.
* Public API: :func:`init`, :func:`get`, :func:`bind`, :func:`dump`,
  :func:`shutdown`, :func:`logdemo`, plus compatibility helpers.
* Support helpers: colour themes, environment/level coercion, diagnostic
  utilities, and console-style parsing.

System Role
-----------
Bridges the domain (:mod:`lib_log_rich.domain.*`) and application
(:mod:`lib_log_rich.application.*`) layers with concrete adapters, ensuring all
policy decisions remain in the inner layers while orchestration, configuration,
and I/O live here at the edge of the system.
"""

from __future__ import annotations

import asyncio
import json
import os
import getpass
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Optional
import sys

from .adapters import (
    DumpAdapter,
    GraylogAdapter,
    JournaldAdapter,
    QueueAdapter,
    RegexScrubber,
    RichConsoleAdapter,
    SlidingWindowRateLimiter,
    WindowsEventLogAdapter,
)
from .application.ports import ClockPort, ConsolePort, IdProvider, RateLimiterPort, StructuredBackendPort
from .application.use_cases.dump import create_capture_dump
from .application.use_cases.process_event import create_process_log_event
from .application.use_cases.shutdown import create_shutdown
from .domain import ContextBinder, DumpFormat, LogContext, LogEvent, LogLevel, RingBuffer


CONSOLE_STYLE_THEMES: dict[str, dict[str, str]] = {
    "classic": {
        "DEBUG": "dim",
        "INFO": "cyan",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold red",
    },
    "dark": {
        "DEBUG": "grey42",
        "INFO": "bright_white",
        "WARNING": "bold gold3",
        "ERROR": "bold red3",
        "CRITICAL": "bold white on red3",
    },
    "neon": {
        "DEBUG": "#00ffd5",
        "INFO": "#39ff14",
        "WARNING": "#fff700",
        "ERROR": "#ff073a",
        "CRITICAL": "bold #ff00ff on black",
    },
    "pastel": {
        "DEBUG": "aquamarine1",
        "INFO": "light_sky_blue1",
        "WARNING": "khaki1",
        "ERROR": "light_salmon1",
        "CRITICAL": "bold plum1",
    },
}
"""Built-in console palettes keyed by theme name.

Themes are consumed by :func:`init` (via the ``console_styles`` parameter) and
:func:`logdemo`, letting documentation stay in sync with the colour options
referenced in :doc:`CONSOLESTYLES`.
"""


@dataclass(slots=True)
class LoggingRuntime:
    """Aggregate of live collaborators created by :func:`init`.

    Parameters
    ----------
    binder:
        Context stack manager that tracks request/job frames for the current
        runtime.
    process:
        Callable returned by :func:`create_process_log_event` that performs the
        full policy pipeline for each event.
    capture_dump:
        Callable produced by :func:`create_capture_dump` for ring-buffer
        materialisation.
    shutdown_async:
        Awaitable or callable produced by :func:`create_shutdown` responsible
        for draining queues and flushing adapters.
    queue:
        Optional :class:`QueueAdapter` when asynchronous fan-out is enabled;
        ``None`` when events are processed inline.
    service / environment:
        Normalised identifiers recorded on every event for observability.
    console_level / backend_level:
        Calculated severity thresholds for console and structured sinks.
    """

    binder: ContextBinder
    process: Callable[..., dict[str, Any]]
    capture_dump: Callable[..., str]
    shutdown_async: Callable[[], asyncio.Future | Any]
    queue: QueueAdapter | None
    service: str
    environment: str
    console_level: LogLevel
    backend_level: LogLevel


_STATE: LoggingRuntime | None = None


class _SystemClock(ClockPort):
    """Concrete clock port returning timezone-aware UTC timestamps."""

    def now(self) -> datetime:
        """Return the current UTC timestamp with timezone info."""
        return datetime.now(timezone.utc)


class _UuidProvider(IdProvider):
    """Generate stable hexadecimal identifiers for log events."""

    def __call__(self) -> str:
        """Return a random UUID4 value encoded as a lowercase hex string."""
        from uuid import uuid4

        return uuid4().hex


class _AllowAllRateLimiter(RateLimiterPort):
    """Fallback rate limiter that never throttles events."""

    def allow(self, event: LogEvent) -> bool:
        """Always grant permission; used when rate limiting is disabled."""
        return True


class LoggerProxy:
    """Lightweight facade for structured logging calls.

    The proxy keeps host code decoupled from the underlying use case function,
    while providing ergonomic level-specific helpers that return diagnostic
    dictionaries (success, event identifiers, queue state).
    """

    def __init__(self, name: str, process: Callable[..., dict[str, Any]]) -> None:
        """Bind a logger name to the runtime's process function.

        Parameters
        ----------
        name:
            Fully-qualified logger identifier (e.g. ``"app.http"``).
        process:
            Callable produced by :func:`create_process_log_event`.
        """
        self._name = name
        self._process = process

    def debug(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        """Emit a ``DEBUG`` message enriched with optional context.

        See :meth:`_log` for return semantics.
        """
        return self._log(LogLevel.DEBUG, message, extra)

    def info(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        """Emit an ``INFO`` message with optional event metadata.

        See :meth:`_log` for return semantics.
        """
        return self._log(LogLevel.INFO, message, extra)

    def warning(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        """Emit a ``WARNING`` message for notable but non-fatal conditions.

        See :meth:`_log` for return semantics.
        """
        return self._log(LogLevel.WARNING, message, extra)

    def error(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        """Emit an ``ERROR`` message signalling user-visible failures.

        See :meth:`_log` for return semantics.
        """
        return self._log(LogLevel.ERROR, message, extra)

    def critical(self, message: str, *, extra: Optional[MutableMapping[str, Any]] = None) -> dict[str, Any]:
        """Emit a ``CRITICAL`` message for unrecoverable failures.

        See :meth:`_log` for return semantics.
        """
        return self._log(LogLevel.CRITICAL, message, extra)

    def _log(self, level: LogLevel, message: str, extra: Optional[MutableMapping[str, Any]]) -> dict[str, Any]:
        """Delegate to the process use case and normalise payloads.

        Parameters
        ----------
        level:
            Severity to attach to the log event.
        message:
            Human-readable description of the event.
        extra:
            Mutable mapping of additional context merged into the log event's
            ``extra`` dictionary. ``None`` is treated as an empty mapping.

        Returns
        -------
        dict[str, Any]
            Diagnostic payload from :func:`create_process_log_event`, including
            the generated ``event_id`` and queue state flags.
        """
        payload = extra if extra is not None else {}
        return self._process(logger_name=self._name, level=level, message=message, extra=payload)


def init(
    *,
    service: str,
    environment: str,
    console_level: str | LogLevel = LogLevel.INFO,
    backend_level: str | LogLevel = LogLevel.WARNING,
    graylog_endpoint: tuple[str, int] | None = None,
    enable_ring_buffer: bool = True,
    ring_buffer_size: int = 25_000,
    enable_journald: bool = False,
    enable_eventlog: bool = False,
    enable_graylog: bool = False,
    graylog_protocol: str = "tcp",
    graylog_tls: bool = False,
    queue_enabled: bool = True,
    force_color: bool = False,
    no_color: bool = False,
    console_styles: Mapping[str, str] | None = None,
    scrub_patterns: Optional[dict[str, str]] = None,
    text_format: str | None = None,
    rate_limit: Optional[tuple[int, float]] = None,
    diagnostic_hook: Optional[Callable[[str, dict[str, Any]], None]] = None,
) -> None:
    """Compose the logging runtime according to configuration inputs.

    Parameters
    ----------
    service, environment:
        Required identifiers recorded on every log event and used for adapter
        fan-out. Environment variables ``LOG_SERVICE``/``LOG_ENVIRONMENT`` take
        precedence.
    console_level, backend_level:
        Severity thresholds (name or :class:`LogLevel`) for the Rich console and
        structured sinks. Honour ``LOG_CONSOLE_LEVEL``/``LOG_BACKEND_LEVEL`` when
        supplied.
    graylog_endpoint:
        Optional ``(host, port)`` tuple enabling the Graylog adapter when
        combined with ``enable_graylog=True``. The value can also be supplied via
        ``LOG_GRAYLOG_ENDPOINT`` (``host:port``).
    enable_* switches:
        Toggle optional adapters (ring buffer, journald, Windows Event Log,
        Graylog). Platform guards auto-disable unsupported adapters.
    ring_buffer_size:
        Maximum number of events retained in memory; defaults to 25_000 and can
        be overridden via ``LOG_RING_BUFFER_SIZE``.
    graylog_protocol, graylog_tls:
        Select TCP/UDP transport and TLS requirement for Graylog. Invalid
        combinations (UDP with TLS) raise :class:`ValueError`.
    queue_enabled:
        When ``True`` (default) events are delivered to a background thread for
        fan-out. Disabling processes events inline for simpler scripts.
    force_color, no_color:
        Console colour overrides; mirror ``LOG_FORCE_COLOR`` and ``LOG_NO_COLOR``.
    console_styles:
        Optional Rich style overrides keyed by level name. Values merge with
        ``LOG_CONSOLE_STYLES`` (comma-separated ``LEVEL=style``).
    scrub_patterns:
        Mapping from field name to regex pattern for redaction. Merged with the
        defaults and ``LOG_SCRUB_PATTERNS`` when present.
    text_format:
        Default template for text dumps (``LOG_DUMP_TEXT_FORMAT`` override).
        ``None`` keeps the built-in template.
    rate_limit:
        Optional ``(max_events, window_seconds)`` tuple controlling the
        sliding-window rate limiter. Also parseable via ``LOG_RATE_LIMIT``.
    diagnostic_hook:
        Callback invoked with internal telemetry events (``queued``, ``emitted``,
        ``rate_limited``). Exceptions raised by the hook are swallowed.

    Side Effects
    ------------
    * Creates adapter instances and starts the queue worker when enabled.
    * Binds a bootstrap :class:`LogContext` containing system identity fields.
    * Stores runtime state in a module-level singleton for subsequent API calls.

    Raises
    ------
    ValueError
        When incompatible Graylog configuration is supplied.

    Examples
    --------
    >>> import lib_log_rich as log  # doctest: +SKIP
    >>> log.init(service="svc", environment="dev", queue_enabled=False)  # doctest: +SKIP
    >>> with log.bind(job_id="doc"):  # doctest: +SKIP
    ...     _ = log.get("docs").info("ready")  # doctest: +SKIP
    >>> log.shutdown()  # doctest: +SKIP
    """

    global _STATE

    service = os.getenv("LOG_SERVICE", service)
    environment = os.getenv("LOG_ENVIRONMENT", environment)

    console_level = os.getenv("LOG_CONSOLE_LEVEL", console_level)  # type: ignore[assignment]
    backend_level = os.getenv("LOG_BACKEND_LEVEL", backend_level)  # type: ignore[assignment]

    force_color = _env_bool("LOG_FORCE_COLOR", force_color)
    no_color = _env_bool("LOG_NO_COLOR", no_color)
    queue_enabled = _env_bool("LOG_QUEUE_ENABLED", queue_enabled)
    enable_ring_buffer = _env_bool("LOG_RING_BUFFER_ENABLED", enable_ring_buffer)
    ring_buffer_size = int(os.getenv("LOG_RING_BUFFER_SIZE", ring_buffer_size))
    enable_journald = _env_bool("LOG_ENABLE_JOURNALD", enable_journald)
    enable_eventlog = _env_bool("LOG_ENABLE_EVENTLOG", enable_eventlog)
    enable_graylog = _env_bool("LOG_ENABLE_GRAYLOG", enable_graylog)
    env_console_styles = _parse_console_styles(os.getenv("LOG_CONSOLE_STYLES"))
    merged_console_styles = _merge_console_styles(console_styles, env_console_styles)

    graylog_protocol = os.getenv("LOG_GRAYLOG_PROTOCOL", graylog_protocol).lower()
    graylog_tls = _env_bool("LOG_GRAYLOG_TLS", graylog_tls)

    rate_limit = _coerce_rate_limit(os.getenv("LOG_RATE_LIMIT"), rate_limit)
    graylog_endpoint = _coerce_graylog_endpoint(os.getenv("LOG_GRAYLOG_ENDPOINT"), graylog_endpoint)
    env_scrub_patterns = _parse_scrub_patterns(os.getenv("LOG_SCRUB_PATTERNS"))
    default_text_template = os.getenv("LOG_DUMP_TEXT_FORMAT", text_format)

    is_windows = sys.platform.startswith("win")
    if enable_journald and is_windows:
        enable_journald = False
    if enable_eventlog and not is_windows:
        enable_eventlog = False

    try:
        user_name = getpass.getuser()
    except Exception:  # pragma: no cover - platform specific
        user_name = os.getenv("USER") or os.getenv("USERNAME")
    hostname_value = socket.gethostname() or ""
    hostname = hostname_value.split(".", 1)[0] if hostname_value else None
    process_id = os.getpid()

    binder = ContextBinder()
    base_context = LogContext(
        service=service,
        environment=environment,
        job_id="bootstrap",
        user_name=user_name,
        hostname=hostname,
        process_id=process_id,
        process_id_chain=(process_id,),
    )
    binder.deserialize({"version": 1, "stack": [base_context.to_dict(include_none=True)]})

    ring_buffer = RingBuffer(max_events=ring_buffer_size if enable_ring_buffer else 1024)

    console: ConsolePort = RichConsoleAdapter(
        force_color=force_color,
        no_color=no_color,
        styles=merged_console_styles,
    )

    structured_backends: list[StructuredBackendPort] = []
    if enable_journald:
        structured_backends.append(JournaldAdapter())
    if enable_eventlog:
        structured_backends.append(WindowsEventLogAdapter())

    graylog = None
    if enable_graylog and graylog_endpoint is not None:
        host, port = graylog_endpoint
        graylog = GraylogAdapter(
            host=host,
            port=port,
            enabled=True,
            protocol=graylog_protocol,
            use_tls=graylog_tls,
        )

    base_patterns = {"password": r".+", "secret": r".+", "token": r".+"}
    patterns = dict(base_patterns)
    if scrub_patterns:
        patterns.update(scrub_patterns)
    if env_scrub_patterns:
        patterns.update(env_scrub_patterns)
    scrubber = RegexScrubber(patterns=patterns)

    if rate_limit:
        max_events, interval_seconds = rate_limit
        limiter: RateLimiterPort = SlidingWindowRateLimiter(max_events=max_events, interval=timedelta(seconds=interval_seconds))
    else:
        limiter = _AllowAllRateLimiter()

    clock: ClockPort = _SystemClock()
    id_provider: IdProvider = _UuidProvider()

    console_threshold = _coerce_level(console_level)
    backend_threshold = _coerce_level(backend_level)
    graylog_threshold = LogLevel.WARNING if graylog else LogLevel.CRITICAL

    process_use_case = create_process_log_event(
        context_binder=binder,
        ring_buffer=ring_buffer,
        console=console,
        console_level=console_threshold,
        structured_backends=structured_backends,
        backend_level=backend_threshold,
        graylog=graylog,
        graylog_level=graylog_threshold,
        scrubber=scrubber,
        rate_limiter=limiter,
        clock=clock,
        id_provider=id_provider,
        queue=None,
        diagnostic=diagnostic_hook,
    )

    fan_out = getattr(process_use_case, "fan_out")  # type: ignore[attr-defined]

    queue: QueueAdapter | None = None
    if queue_enabled:
        queue = QueueAdapter(worker=fan_out)
        queue.start()
        process_use_case = create_process_log_event(
            context_binder=binder,
            ring_buffer=ring_buffer,
            console=console,
            console_level=console_threshold,
            structured_backends=structured_backends,
            backend_level=backend_threshold,
            graylog=graylog,
            graylog_level=graylog_threshold,
            scrubber=scrubber,
            rate_limiter=limiter,
            clock=clock,
            id_provider=id_provider,
            queue=queue,
            diagnostic=diagnostic_hook,
        )
        fan_out = getattr(process_use_case, "fan_out")  # type: ignore[attr-defined]

    capture_dump = create_capture_dump(ring_buffer=ring_buffer, dump_port=DumpAdapter(), default_template=default_text_template)
    shutdown_async = create_shutdown(queue=queue, graylog=graylog, ring_buffer=ring_buffer if enable_ring_buffer else None)

    _STATE = LoggingRuntime(
        binder=binder,
        process=process_use_case,
        capture_dump=capture_dump,
        shutdown_async=shutdown_async,
        queue=queue,
        service=service,
        environment=environment,
        console_level=console_threshold,
        backend_level=backend_threshold,
    )


def get(name: str) -> LoggerProxy:
    """Return a logger proxy bound to the configured runtime.

    Parameters
    ----------
    name:
        Fully-qualified logger identifier to embed in emitted events.

    Returns
    -------
    LoggerProxy
        Facade exposing level-specific convenience methods.

    Raises
    ------
    RuntimeError
        If :func:`init` has not been called yet.
    """
    runtime = _require_state()
    return LoggerProxy(name, runtime.process)


@contextmanager
def bind(**fields: Any):
    """Push a new :class:`LogContext` frame onto the runtime stack.

    Parameters
    ----------
    **fields:
        Contextual metadata to merge with the current frame. When no parent
        context exists, ``service``, ``environment``, and ``job_id`` are
        required.

    Yields
    ------
    LogContext
        The active context after merging the supplied fields.

    Notes
    -----
    Use nested ``with`` blocks or helper functions to model units of work (job,
    request, task).
    """
    runtime = _require_state()
    with runtime.binder.bind(**fields) as ctx:
        yield ctx


def dump(
    *,
    dump_format: str | DumpFormat = "text",
    path: str | Path | None = None,
    level: str | LogLevel | None = None,
    text_format: str | None = None,
    color: bool = False,
) -> str:
    """Render the in-memory ring buffer into a textual artefact.

    Parameters
    ----------
    dump_format:
        Format identifier (``"text"``, ``"json"``, ``"html"``) or
        :class:`DumpFormat`.
    path:
        Optional filesystem location. When supplied the rendered dump is written
        to disk and still returned as a string. Parent directories are created as
        needed.
    level:
        Minimum severity filter (name or :class:`LogLevel`). ``None`` keeps all
        events.
    text_format:
        Custom template for text dumps using ``str.format`` placeholders.
    color:
        When ``True`` colourises text dumps using ANSI escape sequences. Has no
        effect on JSON/HTML exports.

    Returns
    -------
    str
        Rendered dump in the requested format.

    Raises
    ------
    RuntimeError
        If :func:`init` has not been called yet.
    ValueError
        When ``dump_format`` cannot be parsed or ``text_format`` references an
        unknown placeholder.
    """
    runtime = _require_state()
    fmt = dump_format if isinstance(dump_format, DumpFormat) else DumpFormat.from_name(dump_format)
    target = Path(path) if path is not None else None
    min_level = _coerce_level(level) if level is not None else None
    return runtime.capture_dump(
        dump_format=fmt,
        path=target,
        min_level=min_level,
        text_template=text_format,
        colorize=color,
    )


def shutdown() -> None:
    """Flush adapters, stop the queue, and clear runtime state.

    Ensures the queue drains, Graylog flushes, and the module-level runtime is
    reset so :func:`init` can be called again (useful in tests).
    """
    runtime = _require_state()
    if runtime.queue is not None:
        runtime.queue.stop()
    coro = runtime.shutdown_async()
    if asyncio.iscoroutine(coro):
        asyncio.run(coro)
    global _STATE
    _STATE = None


def hello_world() -> None:
    """Print the canonical smoke-test message for backwards compatibility."""
    print("Hello World")


def i_should_fail() -> None:
    """Intentionally raise ``RuntimeError`` to test error propagation paths.

    Why
        The CLI and integration tests need a deterministic failure scenario to
        ensure traceback toggling and exit-code mapping stay correct as the
        project evolves.

    What
        Raises ``RuntimeError`` with the message ``"I should fail"`` every time
        it is called.

    Side Effects
        None besides raising the exception.

    Raises
        RuntimeError: Always, so downstream adapters can verify their error
        handling branches.

    Examples
    --------
    >>> i_should_fail()
    Traceback (most recent call last):
    ...
    RuntimeError: I should fail
    """

    raise RuntimeError("I should fail")


def summary_info() -> str:
    """Return the metadata banner used by the CLI entry point."""
    from . import __init__conf__

    lines: list[str] = []

    def _capture(text: str) -> None:
        lines.append(text)

    __init__conf__.print_info(writer=_capture)
    return "".join(lines)


def _coerce_level(level: str | LogLevel) -> LogLevel:
    """Normalise level inputs (string or enum) into :class:`LogLevel`."""
    if isinstance(level, LogLevel):
        return level
    return LogLevel.from_name(level)


def _env_bool(name: str, default: bool) -> bool:
    """Return the boolean value of an environment variable with fallback."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _coerce_rate_limit(value: str | None, fallback: Optional[tuple[int, float]]) -> Optional[tuple[int, float]]:
    """Parse ``count/window`` strings into rate-limit tuples."""
    if value is None:
        return fallback
    try:
        count_str, window_str = value.split("/", maxsplit=1)
        return int(count_str), float(window_str)
    except (ValueError, TypeError):  # pragma: no cover - invalid env input falls back gracefully
        return fallback


def _coerce_graylog_endpoint(value: str | None, fallback: tuple[str, int] | None) -> tuple[str, int] | None:
    """Parse ``host:port`` strings into socket endpoint tuples."""
    if value is None:
        return fallback
    host, _, port_str = value.partition(":")
    if not host or not port_str.isdigit():
        return fallback
    return host, int(port_str)


def _parse_console_styles(raw: str | None) -> dict[str, str]:
    """Convert ``LEVEL=style`` comma-separated strings into a dictionary."""
    if not raw:
        return {}
    result: dict[str, str] = {}
    for chunk in raw.split(","):
        if not chunk.strip():
            continue
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        result[key] = value
    return result


def _parse_scrub_patterns(raw: str | None) -> dict[str, str]:
    """Parse ``field=regex`` comma-separated strings for the scrubber."""
    if not raw:
        return {}
    result: dict[str, str] = {}
    for chunk in raw.split(","):
        if not chunk.strip():
            continue
        if "=" not in chunk:
            continue
        key, pattern = chunk.split("=", 1)
        key = key.strip()
        pattern = pattern.strip()
        if not key or not pattern:
            continue
        result[key] = pattern
    return result


def _merge_console_styles(
    explicit: Mapping[str, str] | None,
    env_styles: Mapping[str, str],
) -> dict[str, str]:
    """Combine code-supplied console styles with environment overrides."""
    merged: dict[str, str] = {}

    def _normalise_key(key: str | LogLevel) -> str:
        if isinstance(key, LogLevel):
            return key.name
        return key.strip().upper()

    if explicit:
        for key, value in explicit.items():
            norm = _normalise_key(key)
            if norm:
                merged[norm] = value

    for key, value in env_styles.items():
        norm = key.strip().upper()
        if norm:
            merged[norm] = value

    return merged


def _require_state() -> LoggingRuntime:
    """Return the current runtime or raise when uninitialised."""
    if _STATE is None:
        raise RuntimeError("lib_log_rich.init() must be called before using the logging API")
    return _STATE


__all__ = [
    "LoggerProxy",
    "bind",
    "dump",
    "get",
    "hello_world",
    "i_should_fail",
    "init",
    "shutdown",
    "summary_info",
    "logdemo",
]


def logdemo(
    *,
    theme: str = "classic",
    service: str | None = None,
    environment: str | None = None,
    dump_format: str | DumpFormat | None = None,
    dump_path: str | Path | None = None,
    color: bool | None = None,
    enable_graylog: bool = False,
    graylog_endpoint: tuple[str, int] | None = None,
    graylog_protocol: str = "tcp",
    graylog_tls: bool = False,
    enable_journald: bool = False,
    enable_eventlog: bool = False,
) -> dict[str, Any]:
    """Emit sample log entries and optionally hit real backends.

    Parameters
    ----------
    theme:
        Palette name defined in :data:`CONSOLE_STYLE_THEMES` (case-insensitive).
    service, environment:
        Optional overrides for the temporary runtime initialised during the
        demonstration.
    dump_format:
        Optional format name (``"text"``, ``"json"``, ``"html"``) or
        :class:`DumpFormat`. When provided a dump is rendered and included in
        the returned payload (in addition to any file written via ``dump_path``).
    dump_path:
        Optional filesystem path used when persisting the dump. Callers may
        provide theme-specific filenames when invoking :func:`logdemo`
        repeatedly.
    color:
        Overrides whether text dumps include ANSI colour codes. Defaults to
        coloured output when the dump format is text.
    enable_graylog:
        When ``True`` the demo initialises the Graylog adapter so events are
        transmitted to a GELF endpoint.
    graylog_endpoint:
        Optional ``(host, port)`` tuple consumed when ``enable_graylog`` is true.
        Defaults to ``("127.0.0.1", 12201)`` if omitted.
    graylog_protocol:
        Transport name passed to :func:`init` (``"tcp"`` or ``"udp"``).
    graylog_tls:
        Enables TLS for TCP Graylog connections.
    enable_journald:
        When ``True`` the Journald adapter is initialised (no-op on non-Linux
        hosts).
    enable_eventlog:
        When ``True`` the Windows Event Log adapter is initialised (no-op on
        non-Windows hosts).

    Returns
    -------
    dict[str, Any]
        Contains the normalised theme name, styles, per-event result
        dictionaries, optional dump string, resolved service/environment, and
        which backends were requested.
    """

    if _STATE is not None:
        raise RuntimeError("logdemo() requires lib_log_rich to be uninitialised. Call shutdown() first.")

    key = theme.strip().lower()
    try:
        styles = CONSOLE_STYLE_THEMES[key]
    except KeyError as exc:
        raise ValueError(f"Unknown console theme: {theme!r}") from exc

    resolved_service = service or "logdemo"
    resolved_environment = environment or f"demo-{key}"
    resolved_graylog_endpoint = None
    if enable_graylog:
        resolved_graylog_endpoint = graylog_endpoint or ("127.0.0.1", 12201)

    init(
        service=resolved_service,
        environment=resolved_environment,
        console_level=LogLevel.DEBUG,
        backend_level=LogLevel.CRITICAL,
        enable_ring_buffer=False,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        enable_graylog=enable_graylog,
        graylog_endpoint=resolved_graylog_endpoint,
        graylog_protocol=graylog_protocol,
        graylog_tls=graylog_tls,
        queue_enabled=False,
        force_color=True,
        console_styles=styles,
    )

    results: list[dict[str, Any]] = []
    dump_payload: str | None = None
    try:
        with bind(job_id=f"logdemo-{key}", request_id="demo"):
            logger = get("logdemo")
            samples = [
                (LogLevel.DEBUG, "Debug message"),
                (LogLevel.INFO, "Information message"),
                (LogLevel.WARNING, "Warning message"),
                (LogLevel.ERROR, "Error message"),
                (LogLevel.CRITICAL, "Critical message"),
            ]
            for level, message in samples:
                log_method = {
                    LogLevel.DEBUG: logger.debug,
                    LogLevel.INFO: logger.info,
                    LogLevel.WARNING: logger.warning,
                    LogLevel.ERROR: logger.error,
                    LogLevel.CRITICAL: logger.critical,
                }[level]
                result = log_method(
                    f"[{theme}] {message}",
                    extra={"theme": theme, "level": level.severity},
                )
                results.append(result)
        if dump_format is not None:
            fmt = dump_format if isinstance(dump_format, DumpFormat) else DumpFormat.from_name(str(dump_format))
            target = Path(dump_path) if dump_path is not None else None
            colorize = color if color is not None else fmt is DumpFormat.TEXT
            dump_payload = dump(dump_format=fmt, path=target, color=colorize)
    finally:
        shutdown()

    return {
        "theme": key,
        "styles": dict(styles),
        "events": results,
        "dump": dump_payload,
        "service": resolved_service,
        "environment": resolved_environment,
        "backends": {
            "graylog": enable_graylog and resolved_graylog_endpoint is not None,
            "journald": enable_journald,
            "eventlog": enable_eventlog,
        },
    }
