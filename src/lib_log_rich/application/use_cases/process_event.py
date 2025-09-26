"""Use case orchestrating the processing pipeline for a single log event.

Purpose
-------
Tie together context binding, ring buffer persistence, scrubbing, rate limiting,
and adapter fan-out as described in ``konzept_architecture_plan.md``.

Contents
--------
* Helper functions for context management and fan-out.
* :func:`create_process_log_event` factory returning the runtime callable.

System Role
-----------
Application-layer orchestrator invoked by :func:`lib_log_rich.init` to turn the
configured dependencies into a callable logging pipeline.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
import getpass
import os
import socket

from lib_log_rich.domain import ContextBinder, LogEvent, LogLevel, RingBuffer
from lib_log_rich.domain.context import LogContext

_MAX_PID_CHAIN = 8

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


def _require_context(binder: ContextBinder) -> LogContext:
    """Return the current context frame or raise when none is bound."""
    context = binder.current()
    if context is None:
        raise RuntimeError("No logging context bound; call ContextBinder.bind() before logging")
    return context


def _refresh_context(binder: ContextBinder) -> LogContext:
    """Refresh PID/user/hostname information and update the binder if needed."""
    context = _require_context(binder)
    current_pid = os.getpid()
    host_value = socket.gethostname() or ""
    hostname = host_value.split(".", 1)[0] if host_value else None
    try:
        user_name = getpass.getuser()
    except Exception:  # pragma: no cover - environment dependent
        user_name = os.getenv("USER") or os.getenv("USERNAME")

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
) -> Callable[[str, LogLevel, str, dict[str, Any] | None], dict[str, Any]]:
    """Build the orchestrator capturing the current dependency wiring."""

    def process(
        *,
        logger_name: str,
        level: LogLevel,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Process a log invocation end-to-end."""
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
            queue.put(event)
            _diagnostic("queued", {"event_id": event.event_id, "logger": logger_name})
            return {"ok": True, "event_id": event.event_id, "queued": True}

        _fan_out(event)
        _diagnostic("emitted", {"event_id": event.event_id, "logger": logger_name, "level": level.name})
        return {"ok": True, "event_id": event.event_id}

    def _fan_out(event: LogEvent) -> None:
        """Dispatch ``event`` to console, structured backends, and Graylog."""
        if event.level.value >= console_level.value:
            console.emit(event, colorize=colorize_console)

        if event.level.value >= backend_level.value:
            for backend in structured_backends:
                backend.emit(event)

        if graylog is not None and event.level.value >= graylog_level.value:
            graylog.emit(event)

    def _diagnostic(event_name: str, payload: dict[str, Any]) -> None:
        """Invoke the diagnostic hook if provided, swallowing exceptions."""
        if diagnostic is None:
            return
        try:
            diagnostic(event_name, payload)
        except Exception:  # pragma: no cover
            pass

    setattr(process, "fan_out", _fan_out)
    return process


__all__ = ["create_process_log_event"]
