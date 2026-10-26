"""Payload sanitization utilities for the logging pipeline."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from lib_log_rich.domain import json_codec
from lib_log_rich.domain.enums import DiagnosticEvent, StrValueEnum

if TYPE_CHECKING:
    from lib_log_rich.domain.context import LogContext

    from ._types import DiagnosticEmitter, PayloadLimitsProtocol

TRUNCATION_SUFFIX = "…[truncated]"

# Pre-compute type checks for hot path optimization
# Using direct type comparison is ~10x faster than isinstance() with ABCs
_DICT_TYPE: type = dict
_STR_TYPE: type = str
# Primitive types that have predictable JSON size (no need for full serialization)
_PRIMITIVE_TYPES: tuple[type, ...] = (str, int, float, bool, type(None))


class _TruncationReason(StrValueEnum):
    """Why a text was cut; its value is the ``reason`` field of the diagnostic payload."""

    MESSAGE = "message"
    DEPTH = "depth"
    LENGTH = "length"


@dataclass(frozen=True, slots=True)
class _MappingEvents:
    """The diagnostic events one sanitised mapping (``extra`` or context ``extra``) reports.

    Nested mappings report with their top-level mapping's events: the nesting
    level is carried in the payload (``depth``), so the event name stays one of
    the documented ones however deep the value sits.
    """

    value_truncated: DiagnosticEvent
    depth_collapsed: DiagnosticEvent
    keys_dropped: DiagnosticEvent
    total_trimmed: DiagnosticEvent


_EXTRA_EVENTS = _MappingEvents(
    value_truncated=DiagnosticEvent.EXTRA_VALUE_TRUNCATED,
    depth_collapsed=DiagnosticEvent.EXTRA_VALUE_TRUNCATED_DEPTH_COLLAPSED,
    keys_dropped=DiagnosticEvent.EXTRA_KEYS_DROPPED,
    total_trimmed=DiagnosticEvent.EXTRA_TOTAL_TRIMMED,
)
_CONTEXT_EXTRA_EVENTS = _MappingEvents(
    value_truncated=DiagnosticEvent.CONTEXT_EXTRA_VALUE_TRUNCATED,
    depth_collapsed=DiagnosticEvent.CONTEXT_EXTRA_VALUE_TRUNCATED_DEPTH_COLLAPSED,
    keys_dropped=DiagnosticEvent.CONTEXT_EXTRA_KEYS_DROPPED,
    total_trimmed=DiagnosticEvent.CONTEXT_EXTRA_TOTAL_TRIMMED,
)


class _JsonEncoder:
    """Encoder over the shared JSON codec, with a JSONEncoder-compatible interface."""

    def encode(self, value: Any) -> str:
        """Encode value to a JSON string with the interpreter's codec."""
        try:
            return json_codec.dumps(value)
        except TypeError:
            return str(value)


# Reusable encoder instance to match previous JSONEncoder pattern
_shared_encoder: _JsonEncoder = _JsonEncoder()


def get_shared_encoder() -> _JsonEncoder:
    """Return the shared encoder instance (for testing)."""
    return _shared_encoder


def set_shared_encoder(encoder: _JsonEncoder) -> None:
    """Replace the shared encoder instance (for testing)."""
    global _shared_encoder  # noqa: PLW0603 - module-level test seam for the shared encoder
    _shared_encoder = encoder


def _encoded_json_size(key: str, value: Any) -> int:
    """Return the UTF-8 byte size of ``{key: value}`` as JSON.

    A value JSON cannot encode (bytes, an arbitrary object) is measured as its
    ``str()`` text inside a JSON string, escapes included - the form it is
    emitted in. Measuring Python's repr of the entry instead undercounts every
    escaped character, so ``extra_max_total_bytes`` could be exceeded.

    Example:
        >>> _encoded_json_size("k", "v")
        9
        >>> _encoded_json_size("k", b"\\x00") == len('{"k":"b\\'\\\\\\\\x00\\'"}')
        True
    """
    try:
        return len(json_codec.dumps({key: value}, default=str).encode())
    except TypeError:
        # orjson refuses some values even with a default (an int beyond 64 bits):
        # measure the text the value would be coerced to.
        return len(json_codec.dumps({key: str(value)}).encode())


class PayloadSanitizer:
    """Clamp log payloads according to configured limits."""

    def __init__(self, limits: PayloadLimitsProtocol, diagnostic: DiagnosticEmitter | None) -> None:
        """Initialize with payload limits and optional diagnostic callback."""
        self._limits = limits
        self._diagnostic = diagnostic

    def sanitize_message(self, message: str, *, event_id: str, logger_name: str) -> str:
        """Truncate message if it exceeds configured limits."""
        limit = self._limits.message_max_chars
        if len(message) <= limit:
            return message
        if not self._limits.truncate_message:
            raise ValueError(f"log message length {len(message)} exceeds configured limit {limit}")
        return self._truncate_text(
            message,
            limit=limit,
            event=DiagnosticEvent.MESSAGE_TRUNCATED,
            event_id=event_id,
            logger_name=logger_name,
            reason=_TruncationReason.MESSAGE,
            key=None,
        )

    def sanitize_extra(
        self,
        extra: Mapping[str, Any],
        *,
        event_id: str,
        logger_name: str,
        exc_info: Any | None = None,
        stack_info: Any | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Sanitize extra payload, extracting exc_info and stack_info."""
        # Use dict directly (Python 3.7+ maintains insertion order)
        filtered: dict[str, Any] = {}
        exc_info_raw: Any = exc_info
        stack_info_raw: Any = stack_info
        if extra:
            for key, value in extra.items():
                key_str = str(key)
                if key_str == "exc_info" and exc_info_raw is None:
                    exc_info_raw = value
                    continue
                if key_str == "stack_info" and stack_info_raw is None:
                    stack_info_raw = value
                    continue
                filtered[key_str] = value
        sanitized, _ = self._sanitize_mapping(
            filtered,
            max_keys=self._limits.extra_max_keys,
            max_value_chars=self._limits.extra_max_value_chars,
            max_depth=self._limits.extra_max_depth,
            total_bytes=self._limits.extra_max_total_bytes,
            events=_EXTRA_EVENTS,
            event_id=event_id,
            logger_name=logger_name,
            depth=0,
        )
        exc_info_text = self._compact_traceback(
            exc_info_raw,
            event_id=event_id,
            logger_name=logger_name,
            event=DiagnosticEvent.EXC_INFO_TRUNCATED,
            key="exc_info",
        )
        stack_info_text = self._compact_traceback(
            stack_info_raw,
            event_id=event_id,
            logger_name=logger_name,
            event=DiagnosticEvent.STACK_INFO_TRUNCATED,
            key="stack_info",
        )
        return sanitized, exc_info_text, stack_info_text

    def sanitize_context(
        self,
        context: LogContext,
        *,
        event_id: str,
        logger_name: str,
    ) -> tuple[LogContext, bool]:
        """Sanitize context extra fields, returning new context and change flag."""
        context_extra: MutableMapping[str, Any] = dict(context.extra)
        sanitized_extra, changed = self._sanitize_mapping(
            context_extra,
            max_keys=self._limits.context_max_keys,
            max_value_chars=self._limits.context_max_value_chars,
            max_depth=self._limits.extra_max_depth,
            total_bytes=None,
            events=_CONTEXT_EXTRA_EVENTS,
            event_id=event_id,
            logger_name=logger_name,
            depth=0,
        )
        if not changed:
            return context, False
        return context.replace(extra=sanitized_extra), True

    def _trim_mapping_by_size(
        self,
        sanitized: dict[str, Any],
        encoded_sizes: dict[str, int],
        encoded_total: int,
        total_bytes: int,
    ) -> tuple[int, list[str]]:
        """Trim mapping to fit within total_bytes limit, returning new total and removed keys."""
        removed_keys: list[str] = []
        # Remove from end (last inserted) to maintain LIFO behavior
        keys_to_check = list(sanitized.keys())
        while encoded_total > total_bytes and keys_to_check:
            removed_key = keys_to_check.pop()
            removed_length = encoded_sizes.pop(removed_key, 0)
            del sanitized[removed_key]
            removed_keys.append(removed_key)
            encoded_total -= removed_length
        return encoded_total, removed_keys

    def _diagnose_dropped_keys(
        self,
        dropped_keys: list[str],
        events: _MappingEvents,
        event_id: str,
        logger_name: str,
        max_keys: int,
    ) -> None:
        """Emit diagnostic for dropped keys if any."""
        if dropped_keys:
            self._diagnose(
                events.keys_dropped,
                event_id,
                logger_name,
                dropped_keys=dropped_keys,
                limit=max_keys,
            )

    def _diagnose_size_trimming(
        self,
        removed_for_size: list[str],
        events: _MappingEvents,
        event_id: str,
        logger_name: str,
        total_bytes: int | None,
    ) -> None:
        """Emit diagnostic for size trimming if any keys were removed."""
        if removed_for_size:
            self._diagnose(
                events.total_trimmed,
                event_id,
                logger_name,
                removed=removed_for_size,
                limit=total_bytes,
            )

    def _update_encoded_size(
        self,
        key: str,
        value: Any,
        sizes: dict[str, int],
        total: int,
    ) -> int:
        """Update encoded size tracking and return new total."""
        entry_length = _encoded_json_size(key, value)
        prev = sizes.get(key)
        if prev is not None:
            total -= prev
        sizes[key] = entry_length
        return total + entry_length

    def _sanitize_mapping(
        self,
        data: Mapping[Any, Any],
        *,
        max_keys: int,
        max_value_chars: int,
        max_depth: int,
        total_bytes: int | None,
        events: _MappingEvents,
        event_id: str,
        logger_name: str,
        depth: int,
    ) -> tuple[dict[str, Any], bool]:
        # Use dict directly (Python 3.7+ maintains insertion order)
        sanitized: dict[str, Any] = {}
        # Only track sizes when we have a byte limit (lazy size calculation)
        track_size = total_bytes is not None
        encoded_sizes: dict[str, int] = {}
        encoded_total = 0
        changed = False
        kept = 0
        dropped_keys: list[str] = []
        next_depth = depth + 1

        for original_key, value in data.items():
            key_str = str(original_key)
            changed = changed or (key_str != original_key)

            if kept >= max_keys:
                dropped_keys.append(key_str)
                changed = True
                continue

            sanitized_value, value_changed = self._normalise_value(
                value,
                depth=next_depth,
                max_depth=max_depth,
                max_chars=max_value_chars,
                events=events,
                event_id=event_id,
                logger_name=logger_name,
                key_path=key_str,
            )

            sanitized[key_str] = sanitized_value

            if track_size:
                encoded_total = self._update_encoded_size(key_str, sanitized_value, encoded_sizes, encoded_total)

            changed = changed or value_changed
            kept += 1

        self._diagnose_dropped_keys(dropped_keys, events, event_id, logger_name, max_keys)

        removed_for_size: list[str] = []
        if total_bytes is not None and encoded_total > total_bytes:
            encoded_total, removed_for_size = self._trim_mapping_by_size(
                sanitized,
                encoded_sizes,
                encoded_total,
                total_bytes,
            )
            changed = True

        self._diagnose_size_trimming(removed_for_size, events, event_id, logger_name, total_bytes)

        return sanitized, changed

    def _normalise_value(
        self,
        value: Any,
        *,
        depth: int,
        max_depth: int,
        max_chars: int,
        events: _MappingEvents,
        event_id: str,
        logger_name: str,
        key_path: str,
    ) -> tuple[Any, bool]:
        if depth > max_depth:
            truncated = self._truncate_text(
                self._coerce_to_text(value),
                limit=max_chars,
                event=events.value_truncated,
                event_id=event_id,
                logger_name=logger_name,
                reason=_TruncationReason.DEPTH,
                key=key_path,
            )
            return truncated, True
        # Fast path: check common dict type first, then fall back to ABC
        value_type = cast("type[Any]", type(value))
        is_mapping = value_type is _DICT_TYPE or (value_type not in (str, int, float, bool, type(None), list, tuple) and isinstance(value, Mapping))
        if is_mapping:
            nested, nested_changed = self._sanitize_mapping(
                cast("Mapping[str, Any]", value),
                max_keys=self._limits.extra_max_keys,
                max_value_chars=max_chars,
                max_depth=max_depth,
                total_bytes=None,
                events=events,
                event_id=event_id,
                logger_name=logger_name,
                depth=depth,
            )
            if nested_changed:
                self._diagnose(
                    events.depth_collapsed,
                    event_id,
                    logger_name,
                    key=key_path,
                    depth=depth,
                )
            return nested, True
        text = self._coerce_to_text(value)
        if len(text) <= max_chars:
            return cast("Any", value), False
        truncated = self._truncate_text(
            text,
            limit=max_chars,
            event=events.value_truncated,
            event_id=event_id,
            logger_name=logger_name,
            reason=_TruncationReason.LENGTH,
            key=key_path,
        )
        return truncated, True

    def _compact_traceback(
        self,
        value: Any,
        *,
        event_id: str,
        logger_name: str,
        event: DiagnosticEvent,
        key: str,
    ) -> str | None:
        if value is None:
            return None
        text = self._coerce_to_text(value)
        frames = text.splitlines()
        limit = self._limits.stacktrace_max_frames
        if limit <= 0 or len(frames) <= limit * 2:
            if len(text) > self._limits.extra_max_value_chars:
                return self._truncate_text(
                    text,
                    limit=self._limits.extra_max_value_chars,
                    event=event,
                    event_id=event_id,
                    logger_name=logger_name,
                    reason=_TruncationReason.LENGTH,
                    key=key,
                )
            return text
        trimmed = len(frames) - (limit * 2)
        compacted = [*frames[:limit], f"... truncated {trimmed} frame(s) ...", *frames[-limit:]]
        compacted_text = "\n".join(compacted)
        self._diagnose(event, event_id, logger_name, frames_removed=trimmed)
        if len(compacted_text) > self._limits.extra_max_value_chars:
            compacted_text = self._truncate_text(
                compacted_text,
                limit=self._limits.extra_max_value_chars,
                event=event,
                event_id=event_id,
                logger_name=logger_name,
                reason=_TruncationReason.LENGTH,
                key=key,
            )
        return compacted_text

    def _truncate_text(
        self,
        text: str,
        *,
        limit: int,
        event: DiagnosticEvent,
        event_id: str,
        logger_name: str,
        reason: _TruncationReason,
        key: str | None,
    ) -> str:
        if len(text) <= limit:
            return text
        suffix = TRUNCATION_SUFFIX
        truncated = suffix[:limit] if limit <= len(suffix) else text[: limit - len(suffix)] + suffix
        # The hook payload carries plain strings, as it did before the reason became an enum.
        payload: dict[str, Any] = {
            "reason": reason.value,
            "original_length": len(text),
            "new_length": len(truncated),
        }
        if key is not None:
            payload["key"] = key
        self._diagnose(event, event_id, logger_name, **payload)
        return truncated

    def _coerce_to_text(self, value: Any) -> str:
        # Fast path: direct type check avoids ABC overhead
        if type(value) is _STR_TYPE:
            return value
        try:
            return _shared_encoder.encode(value)
        except TypeError:
            return str(value)

    def _diagnose(self, event: DiagnosticEvent, event_id: str, logger_name: str, **payload: Any) -> None:
        if self._diagnostic is None:
            return
        base = {"event_id": event_id, "logger": logger_name}
        base.update(payload)
        self._diagnostic(event, base)
