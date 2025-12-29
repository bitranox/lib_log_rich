"""Utilities that normalise log events into template-friendly dictionaries.

Why
---
Console output and text dumps accept the same ``str.format`` placeholders. By
producing the payload in one place we ensure both adapters stay in sync and
documentation remains authoritative.

Contents
--------
* :func:`build_format_payload` – generate placeholder values for a log event.

System Role
-----------
Bridges the domain model with presentation adapters described in
``docs/systemdesign/module_reference.md`` so that presets, custom templates, and
doctested examples all rely on the same data contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel

ChainInput = Iterable[int | str] | int | str | None

# Context field names for iteration-based merging (reduces cyclomatic complexity)
_CONTEXT_FIELDS: tuple[str, ...] = (
    "service",
    "environment",
    "job_id",
    "request_id",
    "user_id",
    "user_name",
    "hostname",
    "trace_id",
    "span_id",
)


@dataclass(slots=True, frozen=True)
class TimestampFields:
    """Timestamp-related fields for template rendering.

    Provides multiple timestamp variants (UTC, local, trimmed, naive) as
    pre-formatted ISO strings for use in log templates.
    """

    timestamp: str
    timestamp_trimmed: str
    timestamp_no_us: str
    timestamp_trimmed_naive: str
    timestamp_loc: str
    timestamp_trimmed_loc: str
    timestamp_trimmed_naive_loc: str
    YYYY: str
    MM: str
    DD: str
    hh: str
    mm: str
    ss: str
    YYYY_loc: str
    MM_loc: str
    DD_loc: str
    hh_loc: str
    mm_loc: str
    ss_loc: str

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary for template expansion."""
        return {
            "timestamp": self.timestamp,
            "timestamp_trimmed": self.timestamp_trimmed,
            "timestamp_no_us": self.timestamp_no_us,
            "timestamp_trimmed_naive": self.timestamp_trimmed_naive,
            "timestamp_loc": self.timestamp_loc,
            "timestamp_trimmed_loc": self.timestamp_trimmed_loc,
            "timestamp_trimmed_naive_loc": self.timestamp_trimmed_naive_loc,
            "YYYY": self.YYYY,
            "MM": self.MM,
            "DD": self.DD,
            "hh": self.hh,
            "mm": self.mm,
            "ss": self.ss,
            "YYYY_loc": self.YYYY_loc,
            "MM_loc": self.MM_loc,
            "DD_loc": self.DD_loc,
            "hh_loc": self.hh_loc,
            "mm_loc": self.mm_loc,
            "ss_loc": self.ss_loc,
        }


@dataclass(slots=True, frozen=True)
class FormatPayload:
    """Complete payload for template-based log rendering.

    Combines timestamp fields, level metadata, and context/extra information
    into a single structure consumed by console and dump adapters.
    """

    # Timestamp fields
    timestamps: TimestampFields

    # Level fields
    level: str
    level_enum: LogLevel
    LEVEL: str
    level_name: str
    level_code: str
    level_icon: str

    # Event fields
    logger_name: str
    event_id: str
    message: str

    # Context fields
    context: LogContext
    extra: dict[str, Any]
    context_fields: str
    user_name: str | None
    hostname: str | None
    process_id: int | None
    process_id_chain: str

    # Extra fields from extra dict
    theme: Any
    pathname: Any
    lineno: Any
    funcName: Any  # noqa: N815 - matches stdlib logging.LogRecord attribute name

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for template expansion.

        Merges timestamp fields and core fields into a flat dictionary
        suitable for str.format() template expansion.
        """
        payload: dict[str, Any] = {
            **self.timestamps.to_dict(),
            "level": self.level,
            "level_enum": self.level_enum,
            "LEVEL": self.LEVEL,
            "level_name": self.level_name,
            "level_code": self.level_code,
            "level_icon": self.level_icon,
            "logger_name": self.logger_name,
            "event_id": self.event_id,
            "message": self.message,
            "context": self.context.to_dict(include_none=True),
            "extra": self.extra,
            "context_fields": self.context_fields,
            "user_name": self.user_name,
            "hostname": self.hostname,
            "process_id": self.process_id,
            "process_id_chain": self.process_id_chain,
            "theme": self.theme,
            "pathname": self.pathname,
            "lineno": self.lineno,
            "funcName": self.funcName,
        }
        # Aliases for backwards compatibility
        payload["level.icon"] = self.level_icon
        payload["level.severity"] = self.LEVEL
        return payload


def _normalise_process_chain(values: ChainInput) -> str:
    """Return a human-readable representation of PID ancestry chains.

    Args:
        values: Either an iterable of integers or a single value.

    Returns:
        Formatted PID chain joined with ``">"`` or an empty string when
        ``values`` is falsy.

    Example:
        >>> _normalise_process_chain([100, 200])
        '100>200'
        >>> _normalise_process_chain(None)
        ''

    """
    if not values:
        return ""
    if isinstance(values, Iterable) and not isinstance(values, (str, bytes)):
        return ">".join(str(item) for item in values)
    return str(values)


def _merge_context_and_extra(context: LogContext, extra: dict[str, Any]) -> str:
    """Build context_fields string from merged context and extra.

    Uses direct attribute access on LogContext dataclass instead of dict conversion.
    """
    merged_pairs: dict[str, Any] = {}

    # Collect non-None context fields via iteration
    for field_name in _CONTEXT_FIELDS:
        value = getattr(context, field_name)
        if value:
            merged_pairs[field_name] = value

    # Handle special case: process_id (can be 0, so check for None explicitly)
    if context.process_id is not None:
        merged_pairs["process_id"] = context.process_id
    if context.process_id_chain:
        merged_pairs["process_id_chain"] = context.process_id_chain
    if context.extra:
        merged_pairs.update(context.extra)

    # Add extra fields
    for key, value in extra.items():
        if value not in (None, {}):
            merged_pairs[key] = value

    if not merged_pairs:
        return ""
    return " " + " ".join(f"{key}={value}" for key, value in sorted(merged_pairs.items()))


def _format_process_chain_for_template(chain: tuple[int, ...]) -> ChainInput:
    """Normalize process_id_chain tuple to a format suitable for _normalise_process_chain."""
    if chain:
        return tuple(str(part) for part in chain)
    return None


def _build_timestamp_fields(
    timestamp: datetime, local_timestamp: datetime, trimmed_timestamp: datetime, trimmed_local: datetime, trimmed_naive: datetime, trimmed_local_naive: datetime
) -> TimestampFields:
    """Build all timestamp-related fields for the payload."""
    return TimestampFields(
        timestamp=timestamp.isoformat(),
        timestamp_trimmed=trimmed_timestamp.isoformat(),
        timestamp_no_us=trimmed_timestamp.isoformat(),
        timestamp_trimmed_naive=trimmed_naive.isoformat(),
        timestamp_loc=local_timestamp.isoformat(),
        timestamp_trimmed_loc=trimmed_local.isoformat(),
        timestamp_trimmed_naive_loc=trimmed_local_naive.isoformat(),
        YYYY=f"{timestamp.year:04d}",
        MM=f"{timestamp.month:02d}",
        DD=f"{timestamp.day:02d}",
        hh=f"{timestamp.hour:02d}",
        mm=f"{timestamp.minute:02d}",
        ss=f"{timestamp.second:02d}",
        YYYY_loc=f"{local_timestamp.year:04d}",
        MM_loc=f"{local_timestamp.month:02d}",
        DD_loc=f"{local_timestamp.day:02d}",
        hh_loc=f"{local_timestamp.hour:02d}",
        mm_loc=f"{local_timestamp.minute:02d}",
        ss_loc=f"{local_timestamp.second:02d}",
    )


def build_format_payload(event: LogEvent) -> FormatPayload:
    """Construct the FormatPayload consumed by console/text dump templates.

    Centralizes the placeholder contract for Rich console and dump adapters.
    Returns FormatPayload with timestamp variants, level metadata, context/extra fields.

    Uses direct attribute access on LogContext dataclass instead of dict conversion.
    """
    context = event.context
    extra = event.extra
    context_fields = _merge_context_and_extra(context, extra)
    formatted_chain = _format_process_chain_for_template(context.process_id_chain)

    timestamp = event.timestamp
    trimmed_timestamp = timestamp.replace(microsecond=0)
    local_timestamp = timestamp.astimezone()
    trimmed_local = local_timestamp.replace(microsecond=0)

    timestamp_fields = _build_timestamp_fields(
        timestamp, local_timestamp, trimmed_timestamp, trimmed_local, trimmed_timestamp.replace(tzinfo=None), trimmed_local.replace(tzinfo=None)
    )

    level_text = event.level.severity.upper()

    return FormatPayload(
        timestamps=timestamp_fields,
        level=level_text,
        level_enum=event.level,
        LEVEL=level_text,
        level_name=event.level.name,
        level_code=event.level.code,
        level_icon=event.level.icon,
        logger_name=event.logger_name,
        event_id=event.event_id,
        message=event.message,
        context=context,
        extra=extra,
        context_fields=context_fields,
        user_name=context.user_name,
        hostname=context.hostname,
        process_id=context.process_id,
        process_id_chain=_normalise_process_chain(formatted_chain),
        theme=extra.get("theme"),
        pathname=extra.get("pathname"),
        lineno=extra.get("lineno"),
        funcName=extra.get("funcName"),
    )


__all__ = ["build_format_payload", "FormatPayload", "TimestampFields"]
