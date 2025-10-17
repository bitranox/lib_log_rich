"""Helpers supporting the log event processing pipeline."""

from __future__ import annotations

from contextlib import suppress
from typing import Any, Callable, Mapping

from lib_log_rich.domain import LogEvent, LogLevel
from lib_log_rich.domain.context import ContextBinder, LogContext

from lib_log_rich.application.ports import ClockPort, SystemIdentityPort

from ._payload_sanitizer import PayloadSanitizer

DiagnosticEmitter = Callable[[str, dict[str, Any]], None]
_MAX_PID_CHAIN = 8


def build_diagnostic_emitter(callback: Callable[[str, dict[str, Any]], None] | None) -> DiagnosticEmitter:
    """Return a safe diagnostic hook that never interrupts the pipeline."""

    def emit(event_name: str, payload: dict[str, Any]) -> None:
        if callback is None:
            return
        with suppress(Exception):  # defensive: diagnostics must not raise
            callback(event_name, payload)

    return emit


def coerce_extra_mapping(
    extra: Mapping[str, Any] | None,
    *,
    event_id: str,
    logger_name: str,
    emit: DiagnosticEmitter,
) -> Mapping[str, Any]:
    """Return a dictionary derived from ``extra`` while reporting failures."""

    if extra is None:
        return {}
    try:
        return dict(extra)
    except Exception:
        emit("extra_invalid", {"event_id": event_id, "logger": logger_name})
        return {}


def _render_message(
    message: object,
    args: tuple[object, ...],
    *,
    event_id: str,
    logger_name: str,
    emit: DiagnosticEmitter,
) -> str:
    base = str(message)
    if not args:
        return base
    try:
        return base % args
    except Exception as exc:  # pragma: no cover - defensive guard
        emit(
            "message_format_failed",
            {
                "event_id": event_id,
                "logger": logger_name,
                "error": repr(exc),
                "message": base,
                "args": tuple(repr(arg) for arg in args),
            },
        )
        return f"{base} [formatting failed: {exc}]"


def _require_context(binder: ContextBinder) -> LogContext:
    """Return the active context frame or raise when none is bound."""

    context = binder.current()
    if context is None:
        raise RuntimeError("No logging context bound; call ContextBinder.bind() before logging")
    return context


def refresh_context(
    binder: ContextBinder,
    identity: SystemIdentityPort,
) -> LogContext:
    """Refresh host metadata while enforcing the process ID chain bound."""

    context = _require_context(binder)
    identity_snapshot = identity.resolve_identity()
    current_pid = identity_snapshot.process_id

    hostname = context.hostname or identity_snapshot.hostname
    user_name = context.user_name or identity_snapshot.user_name

    chain = context.process_id_chain or ()
    if not chain:
        new_chain = (current_pid,)
    elif chain[-1] != current_pid:
        new_chain = (*chain, current_pid)
        if len(new_chain) > _MAX_PID_CHAIN:
            new_chain = new_chain[-_MAX_PID_CHAIN:]
    else:
        new_chain = chain

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
    return context


def prepare_event(
    *,
    event_id: str,
    logger_name: str,
    level: LogLevel,
    message: object,
    args: tuple[object, ...],
    exc_info: object | None,
    stack_info: object | None,
    stacklevel: int,
    extra: Mapping[str, Any] | None,
    context_binder: ContextBinder,
    identity: SystemIdentityPort,
    sanitizer: PayloadSanitizer,
    clock: ClockPort,
    emit: DiagnosticEmitter,
) -> LogEvent:
    """Build a sanitised :class:`LogEvent` ready for downstream adapters."""

    _ = stacklevel  # API parity with logging.Logger; currently unused.

    raw_extra = coerce_extra_mapping(extra, event_id=event_id, logger_name=logger_name, emit=emit)
    rendered_message = _render_message(message, args, event_id=event_id, logger_name=logger_name, emit=emit)
    sanitized_message = sanitizer.sanitize_message(rendered_message, event_id=event_id, logger_name=logger_name)
    sanitized_extra, sanitized_exc_info, sanitized_stack = sanitizer.sanitize_extra(
        raw_extra,
        event_id=event_id,
        logger_name=logger_name,
        exc_info=exc_info,
        stack_info=stack_info,
    )

    context = refresh_context(context_binder, identity)
    context, context_changed = sanitizer.sanitize_context(context, event_id=event_id, logger_name=logger_name)
    if context_changed:
        context_binder.replace_top(context)

    return LogEvent(
        event_id=event_id,
        timestamp=clock.now(),
        logger_name=logger_name,
        level=level,
        message=sanitized_message,
        context=context,
        extra=sanitized_extra,
        exc_info=sanitized_exc_info,
        stack_info=sanitized_stack,
    )


__all__ = [
    "DiagnosticEmitter",
    "build_diagnostic_emitter",
    "coerce_extra_mapping",
    "refresh_context",
    "prepare_event",
]
