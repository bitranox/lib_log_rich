"""Payload sanitization utilities for the logging pipeline."""

from __future__ import annotations

import json
from collections import OrderedDict
from typing import MutableMapping
from typing import Any, Callable, Mapping, Protocol, cast

from lib_log_rich.domain.context import LogContext

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


class PayloadSanitizer:
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
        exc_info: Any | None = None,
        stack_info: Any | None = None,
    ) -> tuple[dict[str, Any], str | None, str | None]:
        ordered: MutableMapping[str, Any] = OrderedDict()
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
                ordered[key_str] = value
        sanitized, _ = self._sanitize_mapping(
            ordered,
            max_keys=self._limits.extra_max_keys,
            max_value_chars=self._limits.extra_max_value_chars,
            max_depth=self._limits.extra_max_depth,
            total_bytes=self._limits.extra_max_total_bytes,
            event_prefix="extra",
            event_id=event_id,
            logger_name=logger_name,
            depth=0,
        )
        exc_info_text = self._compact_traceback(
            exc_info_raw,
            event_id=event_id,
            logger_name=logger_name,
            event_name="exc_info_truncated",
            key="exc_info",
        )
        stack_info_text = self._compact_traceback(
            stack_info_raw,
            event_id=event_id,
            logger_name=logger_name,
            event_name="stack_info_truncated",
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
        context_extra: MutableMapping[str, Any] = dict(context.extra)
        sanitized_extra, changed = self._sanitize_mapping(
            context_extra,
            max_keys=self._limits.context_max_keys,
            max_value_chars=self._limits.context_max_value_chars,
            max_depth=self._limits.extra_max_depth,
            total_bytes=None,
            event_prefix="context_extra",
            event_id=event_id,
            logger_name=logger_name,
            depth=0,
        )
        if not changed:
            return context, False
        return context.replace(extra=sanitized_extra), True

    def _sanitize_key(self, original_key: Any) -> tuple[str, bool]:
        """Sanitize a mapping key, returning normalized key and change flag."""
        key_str = str(original_key)
        changed = key_str != original_key
        return key_str, changed

    def _process_mapping_entry(
        self,
        key_str: str,
        value: Any,
        depth: int,
        max_depth: int,
        max_value_chars: int,
        event_prefix: str,
        event_id: str,
        logger_name: str,
    ) -> tuple[Any, int, bool]:
        """Process a single mapping entry, returning sanitized value, size, and change flag."""
        sanitized_value, value_changed = self._normalise_value(
            value,
            depth=depth + 1,
            max_depth=max_depth,
            max_chars=max_value_chars,
            event_name=f"{event_prefix}_value_truncated",
            event_id=event_id,
            logger_name=logger_name,
            key_path=key_str,
        )
        entry_length = self._encoded_entry_length(key_str, sanitized_value)
        return sanitized_value, entry_length, value_changed

    def _trim_mapping_by_size(
        self,
        sanitized: OrderedDict[str, Any],
        encoded_sizes: OrderedDict[str, int],
        encoded_total: int,
        total_bytes: int,
    ) -> tuple[int, list[str]]:
        """Trim mapping to fit within total_bytes limit, returning new total and removed keys."""
        removed_keys: list[str] = []
        while encoded_total > total_bytes and sanitized:
            removed_key, removed_length = encoded_sizes.popitem()
            sanitized.popitem()
            removed_keys.append(removed_key)
            encoded_total -= removed_length
        return encoded_total, removed_keys

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
        depth: int,
    ) -> tuple[dict[str, Any], bool]:
        sanitized: OrderedDict[str, Any] = OrderedDict()
        encoded_sizes: OrderedDict[str, int] = OrderedDict()
        encoded_total = 0
        changed = False
        kept = 0
        dropped_keys: list[str] = []

        # Process each key-value pair
        for original_key, value in data.items():
            key_str, key_changed = self._sanitize_key(original_key)
            changed = changed or key_changed

            if kept >= max_keys:
                dropped_keys.append(key_str)
                changed = True
                continue

            sanitized_value, entry_length, value_changed = self._process_mapping_entry(
                key_str, value, depth, max_depth, max_value_chars, event_prefix, event_id, logger_name
            )

            sanitized[key_str] = sanitized_value
            # Update size tracking
            previous_length = encoded_sizes.get(key_str)
            if previous_length is not None:
                encoded_total -= previous_length
            encoded_sizes[key_str] = entry_length
            encoded_total += entry_length

            changed = changed or value_changed
            kept += 1

        # Diagnose dropped keys
        if dropped_keys:
            self._diagnose(
                f"{event_prefix}_keys_dropped",
                event_id,
                logger_name,
                dropped_keys=dropped_keys,
                limit=max_keys,
            )

        # Trim by total size if needed
        removed_for_size: list[str] = []
        if total_bytes is not None and encoded_total > total_bytes:
            encoded_total, removed_for_size = self._trim_mapping_by_size(sanitized, encoded_sizes, encoded_total, total_bytes)
            changed = True

        # Diagnose size trimming
        if removed_for_size:
            self._diagnose(
                f"{event_prefix}_total_trimmed",
                event_id,
                logger_name,
                removed=removed_for_size,
                limit=total_bytes,
            )

        return dict(sanitized), changed

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
        if depth > max_depth:
            truncated = self._truncate_text(
                self._coerce_to_text(value),
                limit=max_chars,
                event_name=event_name,
                event_id=event_id,
                logger_name=logger_name,
                reason="depth",
                key=key_path,
            )
            return truncated, True
        if isinstance(value, Mapping):
            nested, nested_changed = self._sanitize_mapping(
                cast(Mapping[str, Any], value),
                max_keys=self._limits.extra_max_keys,
                max_value_chars=max_chars,
                max_depth=max_depth,
                total_bytes=None,
                event_prefix=event_name,
                event_id=event_id,
                logger_name=logger_name,
                depth=depth,
            )
            if nested_changed:
                self._diagnose(
                    f"{event_name}_depth_collapsed",
                    event_id,
                    logger_name,
                    key=key_path,
                    depth=depth,
                )
            return nested, True
        text = self._coerce_to_text(value)
        if len(text) <= max_chars:
            return value, False
        truncated = self._truncate_text(
            text,
            limit=max_chars,
            event_name=event_name,
            event_id=event_id,
            logger_name=logger_name,
            reason="length",
            key=key_path,
        )
        return truncated, True

    def _compact_traceback(
        self,
        value: Any,
        *,
        event_id: str,
        logger_name: str,
        event_name: str,
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
                    event_name=event_name,
                    event_id=event_id,
                    logger_name=logger_name,
                    reason="length",
                    key=key,
                )
            return text
        trimmed = len(frames) - (limit * 2)
        compacted = frames[:limit] + [f"... truncated {trimmed} frame(s) ..."] + frames[-limit:]
        compacted_text = "\n".join(compacted)
        self._diagnose(event_name, event_id, logger_name, frames_removed=trimmed)
        if len(compacted_text) > self._limits.extra_max_value_chars:
            compacted_text = self._truncate_text(
                compacted_text,
                limit=self._limits.extra_max_value_chars,
                event_name=event_name,
                event_id=event_id,
                logger_name=logger_name,
                reason="length",
                key=key,
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

    def _encoded_entry_length(self, key: str, value: Any) -> int:
        return len(json.dumps({key: value}, ensure_ascii=False, default=str).encode("utf-8"))

    def _diagnose(self, event_name: str, event_id: str, logger_name: str, **payload: Any) -> None:
        if self._diagnostic is None:
            return
        base = {"event_id": event_id, "logger": logger_name}
        base.update(payload)
        self._diagnostic(event_name, base)
