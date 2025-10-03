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

import json
import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol, cast
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


TRUNCATION_SUFFIX = "…[truncated]"


class PayloadLimitsProtocol(Protocol):
    """Structural contract for payload limit configuration."""

    truncate_message: bool
    message_max_chars: int
    extra_max_keys: int
    extra_max_value_chars: int
    extra_max_depth: int
    extra_max_total_bytes: int | None
    context_max_keys: int
    context_max_value_chars: int
    stacktrace_max_frames: int


class _PayloadSanitizer:
    """Clamp log payloads according to configured limits."""

    def __init__(self, limits: PayloadLimitsProtocol, diagnostic: Callable[[str, dict[str, Any]], None] | None) -> None:
        self._limits = limits
        self._diagnostic = diagnostic

    def sanitize_message(self, message: str, *, event_id: str, logger_name: str) -> str:
        limit = self._limits.message_max_chars
        if len(message) <= limit:
            return message
        if not self._limits.truncate_message:
            raise ValueError(f"log message length {len(message)} exceeds configured limit {limit}")
        return self._truncate_text(
            message,
            limit=limit,
            event_name="message_truncated",
            event_id=event_id,
            logger_name=logger_name,
            reason="message",
            key=None,
        )

    def sanitize_extra(
        self,
        extra: Mapping[str, Any],
        *,
        event_id: str,
        logger_name: str,
    ) -> tuple[dict[str, Any], str | None]:
        if not extra:
            return {}, None
        ordered: "OrderedDict[Any, Any]" = OrderedDict()
        exc_info_raw: Any = None
        for key, value in extra.items():
            if str(key) == "exc_info":
                exc_info_raw = value
                continue
            ordered[key] = value
        sanitized, _ = self._sanitize_mapping(
            ordered,
            max_keys=self._limits.extra_max_keys,
            max_value_chars=self._limits.extra_max_value_chars,
            max_depth=self._limits.extra_max_depth,
            total_bytes=self._limits.extra_max_total_bytes,
            event_prefix="extra",
            event_id=event_id,
            logger_name=logger_name,
        )
        exc_info = self._compact_traceback(
            exc_info_raw,
            event_id=event_id,
            logger_name=logger_name,
        )
        return sanitized, exc_info

    def sanitize_context(
        self,
        context: LogContext,
        *,
        event_id: str,
        logger_name: str,
    ) -> tuple[LogContext, bool]:
        sanitized_extra, changed = self._sanitize_mapping(
            context.extra,
            max_keys=self._limits.context_max_keys,
            max_value_chars=self._limits.context_max_value_chars,
            max_depth=self._limits.extra_max_depth,
            total_bytes=None,
            event_prefix="context_extra",
            event_id=event_id,
            logger_name=logger_name,
        )
        if not changed:
            return context, False
        return context.replace(extra=sanitized_extra), True

    def _sanitize_mapping(
        self,
        data: Mapping[Any, Any],
        *,
        max_keys: int,
        max_value_chars: int,
        max_depth: int,
        total_bytes: int | None,
        event_prefix: str,
        event_id: str,
        logger_name: str,
    ) -> tuple[dict[str, Any], bool]:
        sanitized: OrderedDict[str, Any] = OrderedDict()
        changed = False
        kept = 0
        dropped_keys: list[str] = []
        for original_key, value in data.items():
            key_str = str(original_key)
            if key_str != original_key:
                changed = True
            if kept >= max_keys:
                dropped_keys.append(key_str)
                changed = True
                continue
            sanitized_value, value_changed = self._normalise_value(
                value,
                depth=0,
                max_depth=max_depth,
                max_chars=max_value_chars,
                event_name=f"{event_prefix}_value_truncated",
                event_id=event_id,
                logger_name=logger_name,
                key_path=key_str,
            )
            sanitized[key_str] = sanitized_value
            changed = changed or value_changed
            kept += 1
        if dropped_keys:
            self._diagnose(
                f"{event_prefix}_keys_dropped",
                event_id,
                logger_name,
                dropped_keys=dropped_keys,
                limit=max_keys,
            )
        removed_for_size: list[str] = []
        if total_bytes is not None:
            encoded = self._encoded_length(sanitized)
            while encoded > total_bytes and sanitized:
                removed_key, _ = sanitized.popitem()
                removed_for_size.append(removed_key)
                changed = True
                encoded = self._encoded_length(sanitized)
        if removed_for_size:
            self._diagnose(
                f"{event_prefix}_total_trimmed",
                event_id,
                logger_name,
                removed_keys=removed_for_size,
                limit=total_bytes,
            )
        changed_out = changed or bool(dropped_keys) or bool(removed_for_size)
        return dict(sanitized), changed_out

    def _normalise_value(
        self,
        value: Any,
        *,
        depth: int,
        max_depth: int,
        max_chars: int,
        event_name: str,
        event_id: str,
        logger_name: str,
        key_path: str,
    ) -> tuple[Any, bool]:
        if depth >= max_depth:
            coerced = self._coerce_to_text(value)
            truncated = self._truncate_text(
                coerced,
                limit=max_chars,
                event_name=event_name,
                event_id=event_id,
                logger_name=logger_name,
                reason="depth",
                key=key_path,
            )
            return truncated, True
        if isinstance(value, Mapping):
            mapping_value = cast(Mapping[Any, Any], value)
            child: OrderedDict[str, Any] = OrderedDict()
            changed = False
            for child_key_obj, child_value in mapping_value.items():
                if not isinstance(child_key_obj, str):
                    changed = True
                key_str = str(child_key_obj)
                child_result, child_changed = self._normalise_value(
                    child_value,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_chars=max_chars,
                    event_name=event_name,
                    event_id=event_id,
                    logger_name=logger_name,
                    key_path=f"{key_path}.{key_str}",
                )
                child[key_str] = child_result
                changed = changed or child_changed
            return dict(child), changed
        if isinstance(value, (list, tuple)):
            sequence: list[Any] = list(cast(Sequence[Any], value))
            result_list: list[Any] = []
            changed = not isinstance(value, list)
            for index, item in enumerate(sequence):
                sanitized_item, item_changed = self._normalise_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_chars=max_chars,
                    event_name=event_name,
                    event_id=event_id,
                    logger_name=logger_name,
                    key_path=f"{key_path}[{index}]",
                )
                result_list.append(sanitized_item)
                changed = changed or item_changed
            return result_list, changed
        if isinstance(value, (set, frozenset)):
            sorted_items: list[Any] = sorted(list(cast(Iterable[Any], value)), key=str)
            result_list: list[Any] = []
            for index, item in enumerate(sorted_items):
                sanitized_item, _ = self._normalise_value(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_chars=max_chars,
                    event_name=event_name,
                    event_id=event_id,
                    logger_name=logger_name,
                    key_path=f"{key_path}[{index}]",
                )
                result_list.append(sanitized_item)
            return result_list, True
        if isinstance(value, str):
            truncated = self._truncate_text(
                value,
                limit=max_chars,
                event_name=event_name,
                event_id=event_id,
                logger_name=logger_name,
                reason="value",
                key=key_path,
            )
            return truncated, truncated != value
        if isinstance(value, (int, float, bool)) or value is None:
            text = str(value)
            if len(text) > max_chars:
                truncated = self._truncate_text(
                    text,
                    limit=max_chars,
                    event_name=event_name,
                    event_id=event_id,
                    logger_name=logger_name,
                    reason="value",
                    key=key_path,
                )
                return truncated, True
            return value, False
        coerced = self._coerce_to_text(value)
        truncated = self._truncate_text(
            coerced,
            limit=max_chars,
            event_name=event_name,
            event_id=event_id,
            logger_name=logger_name,
            reason="stringified",
            key=key_path,
        )
        return truncated, True

    def _compact_traceback(
        self,
        value: Any,
        *,
        event_id: str,
        logger_name: str,
    ) -> str | None:
        if value is None:
            return None
        text = self._coerce_to_text(value)
        frames = text.splitlines()
        limit = self._limits.stacktrace_max_frames
        if limit <= 0 or len(frames) <= limit * 2:
            return (
                self._truncate_text(
                    text,
                    limit=self._limits.extra_max_value_chars,
                    event_name="exc_info_truncated",
                    event_id=event_id,
                    logger_name=logger_name,
                    reason="length",
                    key="exc_info",
                )
                if len(text) > self._limits.extra_max_value_chars
                else text
            )
        trimmed = len(frames) - (limit * 2)
        compacted = frames[:limit] + [f"... truncated {trimmed} frame(s) ..."] + frames[-limit:]
        compacted_text = "\n".join(compacted)
        self._diagnose(
            "exc_info_truncated",
            event_id,
            logger_name,
            frames_removed=trimmed,
        )
        if len(compacted_text) > self._limits.extra_max_value_chars:
            compacted_text = self._truncate_text(
                compacted_text,
                limit=self._limits.extra_max_value_chars,
                event_name="exc_info_truncated",
                event_id=event_id,
                logger_name=logger_name,
                reason="length",
                key="exc_info",
            )
        return compacted_text

    def _truncate_text(
        self,
        text: str,
        *,
        limit: int,
        event_name: str,
        event_id: str,
        logger_name: str,
        reason: str,
        key: str | None,
    ) -> str:
        if len(text) <= limit:
            return text
        suffix = TRUNCATION_SUFFIX
        if limit <= len(suffix):
            truncated = suffix[:limit]
        else:
            truncated = text[: limit - len(suffix)] + suffix
        payload: dict[str, Any] = {
            "reason": reason,
            "original_length": len(text),
            "new_length": len(truncated),
        }
        if key is not None:
            payload["key"] = key
        self._diagnose(event_name, event_id, logger_name, **payload)
        return truncated

    def _coerce_to_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            return str(value)

    def _encoded_length(self, mapping: Mapping[str, Any]) -> int:
        return len(json.dumps(mapping, ensure_ascii=False, default=str).encode("utf-8"))

    def _diagnose(self, event_name: str, event_id: str, logger_name: str, **payload: Any) -> None:
        if self._diagnostic is None:
            return
        base = {"event_id": event_id, "logger": logger_name}
        base.update(payload)
        self._diagnostic(event_name, base)


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
    limits: PayloadLimitsProtocol,
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
    limits:
        Boundaries applied to messages, extras, context metadata, and stack traces.

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
    >>> class DummyLimits:
    ...     truncate_message = True
    ...     message_max_chars = 4096
    ...     extra_max_keys = 25
    ...     extra_max_value_chars = 512
    ...     extra_max_depth = 3
    ...     extra_max_total_bytes = 8192
    ...     context_max_keys = 20
    ...     context_max_value_chars = 256
    ...     stacktrace_max_frames = 10
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
    ...         limits=DummyLimits(),
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
        extra: Mapping[str, Any] | None = None,
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

        sanitizer = _PayloadSanitizer(limits, _diagnostic)
        event_id = id_provider()
        if extra is None:
            raw_extra: Mapping[str, Any] = {}
        else:
            try:
                raw_extra = dict(extra)
            except Exception:  # pragma: no cover - defensive guard
                _diagnostic("extra_invalid", {"event_id": event_id, "logger": logger_name})
                raw_extra = {}
        sanitized_message = sanitizer.sanitize_message(message, event_id=event_id, logger_name=logger_name)
        sanitized_extra, exc_info = sanitizer.sanitize_extra(raw_extra, event_id=event_id, logger_name=logger_name)
        context = _refresh_context(context_binder)
        context, context_changed = sanitizer.sanitize_context(context, event_id=event_id, logger_name=logger_name)
        if context_changed:
            context_binder.replace_top(context)
        event = LogEvent(
            event_id=event_id,
            timestamp=clock.now(),
            logger_name=logger_name,
            level=level,
            message=sanitized_message,
            context=context,
            extra=sanitized_extra,
            exc_info=exc_info,
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
