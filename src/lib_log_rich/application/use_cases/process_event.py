"""Use case orchestrating the processing pipeline for a single log event.

Purpose
-------
Tie together context binding, ring buffer persistence, scrubbing, rate limiting,
and adapter fan-out as described in ``concept_architecture_plan.md``.

Contents
--------
* Helper functions for context management and fan-out.
* :func:`create_process_log_event` factory returning the runtime callable.

System Role
-----------
Application-layer orchestrator invoked by :func:`lib_log_rich.init` to turn the
configured dependencies into a callable logging pipeline.

Alignment Notes
---------------
Terminology and diagnostics align with ``docs/systemdesign/module_reference.md``
so that emitted payloads and observability hooks remain traceable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any
import getpass
import os
import socket

from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer
from lib_log_rich.domain.context import LogContext

from lib_log_rich.application.ports import (
    ClockPort,
    ConsolePort,
    GraylogPort,
    IdProvider,
    QueuePort,
    RateLimiterPort,
    ScrubberPort,
    StructuredBackendPort,
)

# Preserve up to eight lineage entries to bound context size while retaining ancestry.
_MAX_PID_CHAIN = 8
logger = logging.getLogger(__name__)


def _require_context(binder: ContextBinder) -> LogContext:
    """Return the current context frame or raise when none is bound.

    Why
    ---
    Logging without a context would breach the guarantees about mandatory
    fields (`service`, `environment`, `job_id`) captured in the system design.

    Parameters
    ----------
    binder:
        Global context manager used by the runtime.

    Returns
    -------
    LogContext
        Top-of-stack context.

    Raises
    ------
    RuntimeError
        If no context is currently bound.

    Examples
    --------
    >>> binder = ContextBinder()
    >>> with binder.bind(service='svc', environment='prod', job_id='1'):
    ...     isinstance(_require_context(binder), LogContext)
    True
    >>> binder.current() is None
    True
    >>> _require_context(binder)
    Traceback (most recent call last):
    ...
    RuntimeError: No logging context bound; call ContextBinder.bind() before logging
    """

    context = binder.current()
    if context is None:
        raise RuntimeError("No logging context bound; call ContextBinder.bind() before logging")
    return context


def _refresh_context(binder: ContextBinder) -> LogContext:
    """Refresh PID/user/hostname fields and update the binder when needed.

    Why
    ---
    Subprocesses or threads may change OS-level metadata. Refreshing at emit
    time ensures each event records accurate lineage without leaking mutable
    state out of the binder.

    Parameters
    ----------
    binder:
        Context manager tracking per-execution scopes.

    Returns
    -------
    LogContext
        Potentially updated context reflecting the current environment.

    Side Effects
    ------------
    May call :meth:`ContextBinder.replace_top` when metadata has changed.

    Examples
    --------
    >>> binder = ContextBinder()
    >>> with binder.bind(service='svc', environment='prod', job_id='1'):
    ...     isinstance(_refresh_context(binder), LogContext)
    True
    >>> binder.current() is None
    True
    """

    context = _require_context(binder)
    current_pid = os.getpid()

    hostname = context.hostname
    user_name = context.user_name

    requires_hostname = hostname is None or context.process_id != current_pid
    requires_user = user_name is None or context.process_id != current_pid

    if requires_hostname:
        host_value = socket.gethostname() or ""
        hostname = host_value.split(".", 1)[0] if host_value else None

    if requires_user:
        try:
            user_candidate = getpass.getuser()
        except Exception:  # pragma: no cover - environment dependent
            user_candidate = os.getenv("USER") or os.getenv("USERNAME")
        user_name = user_candidate or user_name

    chain = context.process_id_chain or ()
    if not chain:
        new_chain = (current_pid,)
    elif chain[-1] != current_pid:
        new_chain = (*chain, current_pid)
        if len(new_chain) > _MAX_PID_CHAIN:
            new_chain = new_chain[-_MAX_PID_CHAIN:]
    else:
        new_chain = chain

    updated = context
    changed = False
    if context.process_id != current_pid:
        changed = True
    if context.hostname is None and hostname:
        changed = True
    if context.user_name is None and user_name:
        changed = True
    if new_chain != chain:
        changed = True

    if changed:
        updated = context.replace(
            process_id=current_pid,
            hostname=hostname or context.hostname,
            user_name=user_name or context.user_name,
            process_id_chain=new_chain,
        )
        binder.replace_top(updated)
    return updated


def create_process_log_event(
    *,
    context_binder: ContextBinder,
    ring_buffer: RingBuffer,
    console: ConsolePort,
    console_level: LogLevel,
    structured_backends: Sequence[StructuredBackendPort],
    backend_level: LogLevel,
    graylog: GraylogPort | None,
    graylog_level: LogLevel,
    scrubber: ScrubberPort,
    rate_limiter: RateLimiterPort,
    clock: ClockPort,
    id_provider: IdProvider,
    queue: QueuePort | None,
    colorize_console: bool = True,
    diagnostic: Callable[[str, dict[str, Any]], None] | None = None,
) -> Callable[..., dict[str, Any]]:
    """Build the orchestrator capturing the current dependency wiring.

    Why
    ---
    The composition root assembles a different set of adapters depending on
    configuration (e.g., queue vs. inline mode). This factory freezes those
    decisions into an efficient callable executed for every log event.

    Parameters
    ----------
    context_binder:
        Shared :class:`ContextBinder` supplying contextual metadata.
    ring_buffer:
        :class:`RingBuffer` capturing recent events for dumps.
    console:
        Console adapter implementing :class:`ConsolePort`.
    console_level:
        Minimum level required for console emission.
    structured_backends:
        Sequence of adapters emitting to journald/EventLog/etc.
    backend_level:
        Minimum level required for structured backends.
    graylog:
        Optional Graylog adapter; ``None`` disables Graylog fan-out.
    graylog_level:
        Minimum level for Graylog emission.
    scrubber:
        Adapter implementing :class:`ScrubberPort` for sensitive-field masking.
    rate_limiter:
        Adapter controlling throughput before fan-out.
    clock:
        Provider of timezone-aware timestamps.
    id_provider:
        Callable returning unique event identifiers.
    queue:
        Optional :class:`QueuePort` enabling asynchronous fan-out.
    colorize_console:
        When ``False`` the console adapter renders without colour.
    diagnostic:
        Optional callback invoked with pipeline milestones.

    Returns
    -------
    Callable[[str, LogLevel, str, dict[str, Any] | None], dict[str, Any]]
        Function accepting ``logger_name``, ``level``, ``message``, and optional
        ``extra`` metadata, returning a diagnostic dictionary.

    Examples
    --------
    >>> class DummyConsole(ConsolePort):
    ...     def __init__(self):
    ...         self.events = []
    ...     def emit(self, event: LogEvent, *, colorize: bool) -> None:
    ...         self.events.append((event.logger_name, colorize))
    >>> class DummyBackend(StructuredBackendPort):
    ...     def __init__(self):
    ...         self.events = []
    ...     def emit(self, event: LogEvent) -> None:
    ...         self.events.append(event.logger_name)
    >>> class DummyQueue(QueuePort):
    ...     def __init__(self):
    ...         self.events = []
    ...     def put(self, event: LogEvent) -> None:
    ...         self.events.append(event.logger_name)
    >>> class DummyClock(ClockPort):
    ...     def now(self):
    ...         from datetime import datetime, timezone
    ...         return datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc)
    >>> class DummyId(IdProvider):
    ...     def __call__(self) -> str:
    ...         return 'event-1'
    >>> class DummyScrubber(ScrubberPort):
    ...     def scrub(self, event: LogEvent) -> LogEvent:
    ...         return event
    >>> class DummyLimiter(RateLimiterPort):
    ...     def allow(self, event: LogEvent) -> bool:
    ...         return True
    >>> binder = ContextBinder()
    >>> ring = RingBuffer(max_events=10)
    >>> console_adapter = DummyConsole()
    >>> backend_adapter = DummyBackend()
    >>> with binder.bind(service='svc', environment='prod', job_id='1'):
    ...     process = create_process_log_event(
    ...         context_binder=binder,
    ...         ring_buffer=ring,
    ...         console=console_adapter,
    ...         console_level=LogLevel.DEBUG,
    ...         structured_backends=[backend_adapter],
    ...         backend_level=LogLevel.INFO,
    ...         graylog=None,
    ...         graylog_level=LogLevel.ERROR,
    ...         scrubber=DummyScrubber(),
    ...         rate_limiter=DummyLimiter(),
    ...         clock=DummyClock(),
    ...         id_provider=DummyId(),
    ...         queue=None,
    ...         colorize_console=True,
    ...         diagnostic=None,
    ...     )
    ...     result = process(logger_name='svc.worker', level=LogLevel.INFO, message='hello', extra=None)
    >>> result['ok'] and result['event_id'] == 'event-1'
    True
    >>> len(ring)
    1
    >>> console_adapter.events[0][0]
    'svc.worker'
    >>> backend_adapter.events[0]
    'svc.worker'
    """

    def process(
        *,
        logger_name: str,
        level: LogLevel,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Process a log invocation end-to-end.

        Returns
        -------
        dict[str, Any]
            Diagnostic payload describing queueing state or rejection reason.

        Side Effects
        ------------
        May enqueue events, emit to adapters, and mutate the ring buffer.
        """

        context = _refresh_context(context_binder)
        event = LogEvent(
            event_id=id_provider(),
            timestamp=clock.now(),
            logger_name=logger_name,
            level=level,
            message=message,
            context=context,
            extra=extra or {},
        )

        event = scrubber.scrub(event)

        if not rate_limiter.allow(event):
            _diagnostic("rate_limited", {"event_id": event.event_id, "logger": logger_name, "level": level.name})
            return {"ok": False, "reason": "rate_limited"}
        ring_buffer.append(event)

        if queue is not None:
            queued = queue.put(event)
            if not queued:
                _diagnostic("queue_full", {"event_id": event.event_id, "logger": logger_name, "level": level.name})
                return {"ok": False, "reason": "queue_full"}
            _diagnostic("queued", {"event_id": event.event_id, "logger": logger_name})
            return {"ok": True, "event_id": event.event_id, "queued": True}

        failed_adapters = _fan_out(event)
        if failed_adapters:
            _diagnostic(
                "adapter_error",
                {
                    "event_id": event.event_id,
                    "logger": logger_name,
                    "level": level.name,
                    "adapters": failed_adapters,
                },
            )
            return {
                "ok": False,
                "reason": "adapter_error",
                "event_id": event.event_id,
                "failed_adapters": failed_adapters,
            }
        _diagnostic("emitted", {"event_id": event.event_id, "logger": logger_name, "level": level.name})
        return {"ok": True, "event_id": event.event_id}

    def _fan_out(event: LogEvent) -> list[str]:
        """Dispatch ``event`` to console, structured backends, and Graylog."""

        failed: list[str] = []

        def _safe_emit(callable_: Callable[[], None], adapter_name: str) -> None:
            try:
                callable_()
            except Exception as exc:  # pragma: no cover - exercised via tests
                logger.error(
                    "Adapter %s failed while emitting event %s: %s",
                    adapter_name,
                    event.event_id,
                    exc,
                    exc_info=True,
                )
                failed.append(adapter_name)
                _diagnostic(
                    "adapter_error",
                    {
                        "adapter": adapter_name,
                        "event_id": event.event_id,
                        "logger": event.logger_name,
                        "level": event.level.name,
                        "error": str(exc),
                    },
                )

        if event.level.value >= console_level.value:
            _safe_emit(lambda: console.emit(event, colorize=colorize_console), console.__class__.__name__)

        if event.level.value >= backend_level.value:
            for backend in structured_backends:
                _safe_emit(lambda backend=backend: backend.emit(event), backend.__class__.__name__)

        if graylog is not None and event.level.value >= graylog_level.value:
            graylog_adapter = graylog
            _safe_emit(lambda: graylog_adapter.emit(event), graylog_adapter.__class__.__name__)

        return failed

    def _diagnostic(event_name: str, payload: dict[str, Any]) -> None:
        """Invoke the diagnostic hook if provided, swallowing exceptions.

        Why
        ---
        Diagnostics should never break production logging. Failures are ignored
        intentionally, matching the resilience requirements in the system plan.
        """

        if diagnostic is None:
            return
        try:
            diagnostic(event_name, payload)
        except Exception:  # pragma: no cover
            pass

    setattr(process, "fan_out", _fan_out)
    return process


__all__ = ["create_process_log_event"]
