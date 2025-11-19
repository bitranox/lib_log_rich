"""Factory utilities supporting runtime composition.

Purpose
-------
Provide reusable builders for clocks, binders, adapters, and thresholds so the
composition root can remain declarative.

Contents
--------
* Port implementations used during runtime assembly (`SystemClock`,
  `UuidProvider`, `SystemIdentityProvider`, etc.).
* Factory functions that instantiate ring buffers, consoles, rate limiters, and
  dump renderers.
* Level coercion helpers shared by CLI entry points and runtime setup.

System Role
-----------
Maps configuration data (``RuntimeSettings``) onto concrete collaborators in
accordance with ``docs/systemdesign/module_reference.md`` while keeping the
application layer agnostic of specific adapter implementations.
"""

from __future__ import annotations

import sys
import traceback
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import TracebackType
from typing import IO, Any, Callable, Mapping, MutableMapping, Optional, cast

from lib_log_rich.adapters import (
    DumpAdapter,
    GraylogAdapter,
    JournaldAdapter,
    RegexScrubber,
    RichConsoleAdapter,
    SlidingWindowRateLimiter,
    WindowsEventLogAdapter,
)
from lib_log_rich.application.ports import (
    ClockPort,
    ConsolePort,
    IdProvider,
    RateLimiterPort,
    StructuredBackendPort,
    SystemIdentityPort,
)
from lib_log_rich.application.use_cases.dump import create_capture_dump
from lib_log_rich.domain import (
    ContextBinder,
    DumpFilter,
    DumpFormat,
    LogContext,
    LogEvent,
    LogLevel,
    RingBuffer,
)
from lib_log_rich.domain.identity import SystemIdentity

from ._settings import ConsoleAppearance, DumpDefaults, FeatureFlags, GraylogSettings, RuntimeSettings
from .settings.models import DEFAULT_RING_BUFFER_FALLBACK

try:
    import getpass
    import os
    import socket
except ImportError:  # pragma: no cover - limited runtimes may omit these modules
    getpass = None  # type: ignore[assignment]
    socket = None  # type: ignore[assignment]
    os = None  # type: ignore[assignment]


class SystemClock(ClockPort):
    """Concrete clock port returning timezone-aware UTC timestamps."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class UuidProvider(IdProvider):
    """Generate stable hexadecimal identifiers for log events."""

    def __call__(self) -> str:
        from uuid import uuid4

        return uuid4().hex


class AllowAllRateLimiter(RateLimiterPort):
    """Fallback rate limiter that never throttles events."""

    def allow(self, event: LogEvent) -> bool:  # noqa: ARG002 - interface parity
        return True


class SystemIdentityProvider(SystemIdentityPort):
    """Resolve system identity details using standard library lookups."""

    def resolve_identity(self) -> SystemIdentity:
        user_name: str | None = None
        hostname: str | None = None
        process_id: int

        if getpass is not None:
            with suppress(Exception):  # pragma: no cover - environment dependent
                user_name = getpass.getuser()

        if user_name is None and os is not None:
            user_name = os.getenv("USER") or os.getenv("USERNAME")

        hostname_value = ""
        if socket is not None:
            with suppress(Exception):  # pragma: no cover - environment dependent
                hostname_value = socket.gethostname() or ""
        hostname = hostname_value.split(".", 1)[0] if hostname_value else None

        process_id = os.getpid() if os is not None else 0  # pragma: no cover - fallback for runtimes without os

        return SystemIdentity(user_name=user_name, hostname=hostname, process_id=process_id)


def _ensure_log_level(level: object) -> LogLevel:
    """Normalise caller-supplied level inputs to :class:`LogLevel`.

    Parameters
    ----------
    level:
        A domain enum value, stdlib logging integer, human-readable string, or
        any object to validate.

    Returns
    -------
    LogLevel
        The matching enum instance.

    Raises
    ------
    TypeError
        If ``level`` is neither an enum, string, nor integer.
    ValueError
        If conversion from string or integer fails.
    """

    if isinstance(level, LogLevel):
        return level
    if isinstance(level, str):
        return LogLevel.from_name(level)
    if isinstance(level, bool):  # bool is an ``int`` subclass; reject explicitly.
        raise TypeError("Unsupported level type: bool")
    if not isinstance(level, int):
        raise TypeError(f"Unsupported level type: {type(level)!r}")
    return LogLevel.from_numeric(level)


ExcInfoTuple = tuple[type[BaseException], BaseException, TracebackType | None]


class LoggerProxy:
    """Lightweight facade emulating :class:`logging.Logger` call signatures."""

    def __init__(self, name: str, process: Callable[..., dict[str, Any]]) -> None:
        self._name = name
        self._process = process
        self._level = LogLevel.DEBUG

    def debug(
        self,
        msg: object,
        *args: object,
        exc_info: object | None = None,
        stack_info: object | None = False,
        stacklevel: int = 1,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._log(LogLevel.DEBUG, msg, args, exc_info, stack_info, stacklevel, extra)

    def info(
        self,
        msg: object,
        *args: object,
        exc_info: object | None = None,
        stack_info: object | None = False,
        stacklevel: int = 1,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._log(LogLevel.INFO, msg, args, exc_info, stack_info, stacklevel, extra)

    def warning(
        self,
        msg: object,
        *args: object,
        exc_info: object | None = None,
        stack_info: object | None = False,
        stacklevel: int = 1,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._log(LogLevel.WARNING, msg, args, exc_info, stack_info, stacklevel, extra)

    def error(
        self,
        msg: object,
        *args: object,
        exc_info: object | None = None,
        stack_info: object | None = False,
        stacklevel: int = 1,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._log(LogLevel.ERROR, msg, args, exc_info, stack_info, stacklevel, extra)

    def critical(
        self,
        msg: object,
        *args: object,
        exc_info: object | None = None,
        stack_info: object | None = False,
        stacklevel: int = 1,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._log(LogLevel.CRITICAL, msg, args, exc_info, stack_info, stacklevel, extra)

    def exception(
        self,
        msg: object,
        *args: object,
        exc_info: object | None = True,
        stack_info: object | None = False,
        stacklevel: int = 1,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Log ``msg`` with level :class:`LogLevel.ERROR` capturing exception context.

        Why
        ---
        Mirrors :meth:`logging.Logger.exception` so callers can rely on familiar
        ergonomics while the runtime continues to handle payload sanitization and
        structured enrichment.
        """

        return self._log(LogLevel.ERROR, msg, args, exc_info, stack_info, stacklevel, extra)

    def log(
        self,
        level: LogLevel | str | int,
        msg: object,
        *args: object,
        exc_info: object | None = None,
        stack_info: object | None = False,
        stacklevel: int = 1,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Dispatch a message at ``level`` using automatic enum normalisation."""

        return self._log(level, msg, args, exc_info, stack_info, stacklevel, extra)

    def _log(
        self,
        level: LogLevel | str | int,
        message: object,
        args: tuple[object, ...],
        exc_info: object | None,
        stack_info: object | None,
        stacklevel: int,
        extra: Optional[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload: MutableMapping[str, Any] = {} if extra is None else dict(extra)
        normalised = _ensure_log_level(level)
        if normalised.value < self._level.value:
            return {
                "ok": False,
                "reason": "logger_level",
                "level": normalised.name,
            }
        resolved_exc_info = _normalise_exc_info(exc_info)
        resolved_stack_info = _normalise_stack_info(stack_info)
        return self._process(
            logger_name=self._name,
            level=normalised,
            message=message,
            args=args,
            exc_info=resolved_exc_info,
            stack_info=resolved_stack_info,
            stacklevel=stacklevel,
            extra=payload,
        )

    def setLevel(self, level: LogLevel | str | int) -> None:
        """Adjust the proxy-level threshold, mirroring :mod:`logging` semantics."""

        self._level = _ensure_log_level(level)


def _normalise_exc_info(exc_info: object | None) -> ExcInfoTuple | None:
    if exc_info is None or exc_info is False:
        return None
    if exc_info is True:
        current = sys.exc_info()
        if current == (None, None, None):
            return None
        return _coerce_exc_info_tuple(current)
    if isinstance(exc_info, BaseException):
        return (exc_info.__class__, exc_info, exc_info.__traceback__)
    if isinstance(exc_info, tuple):
        info_tuple = cast(tuple[object, ...], exc_info)
        if len(info_tuple) != 3:
            return None
        coerced = _coerce_exc_info_tuple(cast(tuple[object, object, object | None], info_tuple))
        if coerced is not None:
            return coerced
    return None


def _coerce_exc_info_tuple(value: tuple[object, object, object | None]) -> ExcInfoTuple | None:
    exc_type, exc_value, traceback_obj = value
    if not isinstance(exc_type, type) or not issubclass(exc_type, BaseException):
        return None
    if not isinstance(exc_value, BaseException):
        return None
    if traceback_obj is not None and not isinstance(traceback_obj, TracebackType):
        return None
    return (exc_type, exc_value, traceback_obj)


def _normalise_stack_info(stack_info: object | None) -> str | None:
    if not stack_info:
        return None
    if stack_info is True:
        return "\n".join(traceback.format_stack())
    if isinstance(stack_info, str):
        return stack_info
    return str(stack_info)


def create_dump_renderer(
    *,
    ring_buffer: RingBuffer,
    dump_defaults: DumpDefaults,
    theme: str | None,
    console_styles: Mapping[str, str] | None,
) -> Callable[
    [DumpFormat, Path | None, LogLevel | None, str | None, str | None, str | None, str | None, Mapping[str, str] | None, DumpFilter | None, bool],
    str,
]:
    """Bind dump collaborators into a callable for rendering ring buffer dumps.

    Examples
    --------
    >>> renderer = create_dump_renderer(ring_buffer=ring, dump_defaults=defaults, theme=None, console_styles=None)  # doctest: +SKIP
    >>> callable(renderer)  # doctest: +SKIP
    True
    """
    return create_capture_dump(
        ring_buffer=ring_buffer,
        dump_port=DumpAdapter(),
        default_template=dump_defaults.format_template,
        default_format_preset=dump_defaults.format_preset,
        default_theme=theme,
        default_console_styles=console_styles,
    )


def create_runtime_binder(service: str, environment: str, identity: SystemIdentityPort) -> ContextBinder:
    """Initialise :class:`ContextBinder` with base metadata.

    Why
    ---
    The runtime expects every event to carry service/environment identifiers plus
    host details. Seeding the binder ensures ``bind()`` contexts always inherit
    these values.

    Parameters
    ----------
    service:
        Service name configured by the host application.
    environment:
        Deployment environment (for example ``prod`` or ``staging``).
    identity:
        Port providing system identity information.

    Returns
    -------
    ContextBinder
        Binder primed with a bootstrap context frame.

    Examples
    --------
    >>> identity = SystemIdentity(user_name='dev', hostname='box', process_id=1234)
    >>> class DummyIdentity(SystemIdentityPort):
    ...     def resolve_identity(self) -> SystemIdentity:
    ...         return identity
    >>> binder = create_runtime_binder('svc', 'dev', DummyIdentity())
    >>> isinstance(binder.current(), LogContext)
    True
    """

    resolved = identity.resolve_identity()
    binder = ContextBinder()
    base = LogContext(
        service=service,
        environment=environment,
        job_id="bootstrap",
        user_name=resolved.user_name,
        hostname=resolved.hostname,
        process_id=resolved.process_id,
        process_id_chain=(resolved.process_id,),
    )
    binder.deserialize({"version": 1, "stack": [base.to_dict(include_none=True)]})
    return binder


def create_ring_buffer(enabled: bool, size: int) -> RingBuffer:
    """Construct the runtime ring buffer with sensible fallbacks.

    Why
    ---
    Even when retention is disabled we keep a small buffer for diagnostics (used
    by the CLI demos). Falling back to DEFAULT_RING_BUFFER_FALLBACK events mirrors
    the behaviour described in the system design documents.

    Parameters
    ----------
    enabled:
        Whether retention is requested.
    size:
        Maximum events to retain when enabled.

    Returns
    -------
    RingBuffer
        Instantiated buffer sized according to the configuration.

    Examples
    --------
    >>> create_ring_buffer(True, 50).max_events
    50
    >>> create_ring_buffer(False, 50).max_events
    1024
    """

    capacity = size if enabled else DEFAULT_RING_BUFFER_FALLBACK
    return RingBuffer(max_events=capacity)


def _resolve_stream_target(console: ConsoleAppearance) -> IO[str] | None:
    """Extract stream target from console config."""
    if console.stream == "custom" and console.stream_target is not None:
        return cast(IO[str], console.stream_target)
    return None


def _create_console_with_streams(console: ConsoleAppearance, target: IO[str] | None) -> ConsolePort:
    """Create console adapter with stream parameters."""
    return RichConsoleAdapter(
        force_color=console.force_color,
        no_color=console.no_color,
        styles=console.styles,
        format_preset=console.format_preset,
        format_template=console.format_template,
        stream=console.stream,
        stream_target=target,
    )


def _create_console_legacy(console: ConsoleAppearance) -> ConsolePort:
    """Create console adapter without stream parameters (backwards compatibility)."""
    return RichConsoleAdapter(
        force_color=console.force_color,
        no_color=console.no_color,
        styles=console.styles,
        format_preset=console.format_preset,
        format_template=console.format_template,
    )


def create_console(console: ConsoleAppearance) -> ConsolePort:
    """Instantiate the Rich console adapter from appearance settings.

    Examples
    --------
    >>> from lib_log_rich.runtime._settings import ConsoleAppearance
    >>> appearance = ConsoleAppearance(force_color=False, no_color=False, styles=None, format_preset='full', format_template=None)
    >>> adapter = create_console(appearance)
    >>> hasattr(adapter, 'emit')
    True
    """
    target = _resolve_stream_target(console)
    try:
        return _create_console_with_streams(console, target)
    except TypeError as exc:
        # Backwards compatibility: custom factories may not accept stream parameters
        if "stream" not in str(exc):
            raise
        return _create_console_legacy(console)


def create_structured_backends(flags: FeatureFlags) -> list[StructuredBackendPort]:
    """Select structured logging adapters based on feature flags.

    Why
    ---
    Structured sinks are optional. Centralising the logic avoids scattering the
    flag checks across the runtime composition.

    Parameters
    ----------
    flags:
        Feature flag snapshot describing which backends should be enabled.

    Returns
    -------
    list[StructuredBackendPort]
        Concrete adapters to be wired into the fan-out pipeline.

    Examples
    --------
    >>> from lib_log_rich.runtime._settings import FeatureFlags
    >>> create_structured_backends(FeatureFlags(queue=True, ring_buffer=True, journald=False, eventlog=False))
    []
    >>> create_structured_backends(FeatureFlags(queue=True, ring_buffer=True, journald=True, eventlog=False))  # doctest: +SKIP
    [JournaldAdapter(...)]
    """

    backends: list[StructuredBackendPort] = []
    if flags.journald:
        backends.append(JournaldAdapter())
    if flags.eventlog:
        backends.append(WindowsEventLogAdapter())
    return backends


def create_graylog_adapter(settings: GraylogSettings) -> GraylogAdapter | None:
    """Instantiate the Graylog adapter when configuration permits.

    Why
    ---
    Graylog is optional and may be misconfigured. Handling guard clauses here
    keeps the composition root lean and centralises validation.

    Parameters
    ----------
    settings:
        Graylog-specific configuration resolved from the user inputs.

    Returns
    -------
    GraylogAdapter | None
        Configured adapter when enabled, otherwise ``None``.

    Examples
    --------
    >>> from lib_log_rich.runtime._settings import GraylogSettings
    >>> adapter = create_graylog_adapter(GraylogSettings(enabled=True, endpoint=('host', 12201)))
    >>> adapter is not None
    True
    """

    if not settings.enabled or settings.endpoint is None:
        return None
    host, port = settings.endpoint
    return GraylogAdapter(
        host=host,
        port=port,
        enabled=True,
        protocol=settings.protocol,
        use_tls=settings.tls,
    )


def compute_thresholds(settings: RuntimeSettings, graylog: GraylogAdapter | None) -> tuple[LogLevel, LogLevel, LogLevel]:
    """Resolve logging thresholds per sink, applying safe defaults.

    Why
    ---
    Runtime configuration stores thresholds as strings. Adapters require
    :class:`LogLevel` values and Graylog should default to ``CRITICAL`` when the
    adapter is disabled.

    Parameters
    ----------
    settings:
        Normalised runtime settings including level strings.
    graylog:
        Graylog adapter instance or ``None`` when disabled.

    Returns
    -------
    tuple[LogLevel, LogLevel, LogLevel]
        Levels for console, structured backends, and Graylog respectively.

    Examples
    --------
    >>> from types import SimpleNamespace
    >>> settings = SimpleNamespace(console_level='INFO', backend_level='WARNING', graylog_level='ERROR')
    >>> compute_thresholds(settings, None)[0]
    <LogLevel.INFO: 20>
    """

    console_level = coerce_level(settings.console_level)
    backend_level = coerce_level(settings.backend_level)
    graylog_level = coerce_level(settings.graylog_level)
    if graylog is None:
        graylog_level = LogLevel.CRITICAL
    return console_level, backend_level, graylog_level


def create_scrubber(patterns: dict[str, str]) -> RegexScrubber:
    """Instantiate the configured scrubber class kept on the runtime module."""
    from lib_log_rich import runtime as runtime_module  # local import for monkeypatchability

    scrubber_cls = getattr(runtime_module, "RegexScrubber", RegexScrubber)
    return scrubber_cls(patterns=patterns)


def create_rate_limiter(rate_limit: Optional[tuple[int, float]]) -> RateLimiterPort:
    """Create the rate limiter adapter according to configuration.

    Why
    ---
    Hosts may disable rate limiting entirely. Centralising the decision avoids
    conditional logic in the application layer.

    Parameters
    ----------
    rate_limit:
        Tuple of ``(max_events, interval_seconds)`` or ``None`` to disable.

    Returns
    -------
    RateLimiterPort
        Adapter implementing the configured throttling behaviour.

    Examples
    --------
    >>> limiter = create_rate_limiter((10, 1.5))
    >>> isinstance(limiter, SlidingWindowRateLimiter)
    True
    >>> create_rate_limiter(None).allow  # doctest: +ELLIPSIS
    <bound method AllowAllRateLimiter.allow...
    """

    if rate_limit is None:
        return AllowAllRateLimiter()
    max_events, interval_seconds = rate_limit
    return SlidingWindowRateLimiter(max_events=max_events, interval=timedelta(seconds=interval_seconds))


def coerce_level(level: str | int | LogLevel) -> LogLevel:
    """Convert user-supplied level representations into :class:`LogLevel`.

    Why
    ---
    Configuration files and CLI flags provide strings, while adapters operate on
    the enum to access severity metadata (icons, numeric codes). Some callers
    pass through integers sourced from :mod:`logging` constants, so the helper
    also accepts numerics for parity with the stdlib API.

    Parameters
    ----------
    level:
        String name (case-insensitive), stdlib integer, or existing
        :class:`LogLevel`.

    Returns
    -------
    LogLevel
        Normalised level instance.

    Examples
    --------
    >>> import logging
    >>> coerce_level('warning')
    <LogLevel.WARNING: 30>
    >>> coerce_level(LogLevel.ERROR)
    <LogLevel.ERROR: 40>
    >>> coerce_level(logging.INFO)
    <LogLevel.INFO: 20>
    """

    return _ensure_log_level(level)


__all__ = [
    "AllowAllRateLimiter",
    "SystemIdentityProvider",
    "LoggerProxy",
    "SystemClock",
    "UuidProvider",
    "compute_thresholds",
    "coerce_level",
    "create_console",
    "create_dump_renderer",
    "create_graylog_adapter",
    "create_rate_limiter",
    "create_ring_buffer",
    "create_runtime_binder",
    "create_scrubber",
    "create_structured_backends",
]
