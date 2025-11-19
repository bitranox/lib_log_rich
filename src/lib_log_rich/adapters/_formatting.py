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

from collections.abc import Iterable, Sequence
from typing import Any, cast

from lib_log_rich.domain.events import LogEvent


ChainInput = Iterable[int | str] | int | str | None


def _normalise_process_chain(values: ChainInput) -> str:
    """Return a human-readable representation of PID ancestry chains.

    Parameters
    ----------
    values:
        Either an iterable of integers or a single value.

    Returns
    -------
    str
        Formatted PID chain joined with ``">"`` or an empty string when
        ``values`` is falsy.

    Examples
    --------
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


def _merge_context_and_extra(context_dict: dict[str, Any], extra_dict: dict[str, Any]) -> str:
    """Build context_fields string from merged context and extra."""
    merged_pairs = {key: value for key, value in {**context_dict, **extra_dict}.items() if value not in (None, {})}
    if not merged_pairs:
        return ""
    return " " + " ".join(f"{key}={value}" for key, value in sorted(merged_pairs.items()))


def _format_process_chain_for_template(chain_raw: Any) -> ChainInput:
    """Normalize process_id_chain to a format suitable for _normalise_process_chain."""
    if isinstance(chain_raw, (list, tuple)):
        chain_sequence = cast(Sequence[object], chain_raw)
        return tuple(str(part) for part in chain_sequence)
    if chain_raw is None:
        return None
    return str(chain_raw)


def _build_timestamp_fields(
    timestamp: Any, local_timestamp: Any, trimmed_timestamp: Any, trimmed_local: Any, trimmed_naive: Any, trimmed_local_naive: Any
) -> dict[str, str]:
    """Build all timestamp-related fields for the payload."""
    return {
        "timestamp": timestamp.isoformat(),
        "timestamp_trimmed": trimmed_timestamp.isoformat(),
        "timestamp_no_us": trimmed_timestamp.isoformat(),
        "timestamp_trimmed_naive": trimmed_naive.isoformat(),
        "timestamp_loc": local_timestamp.isoformat(),
        "timestamp_trimmed_loc": trimmed_local.isoformat(),
        "timestamp_trimmed_naive_loc": trimmed_local_naive.isoformat(),
        "YYYY": f"{timestamp.year:04d}",
        "MM": f"{timestamp.month:02d}",
        "DD": f"{timestamp.day:02d}",
        "hh": f"{timestamp.hour:02d}",
        "mm": f"{timestamp.minute:02d}",
        "ss": f"{timestamp.second:02d}",
        "YYYY_loc": f"{local_timestamp.year:04d}",
        "MM_loc": f"{local_timestamp.month:02d}",
        "DD_loc": f"{local_timestamp.day:02d}",
        "hh_loc": f"{local_timestamp.hour:02d}",
        "mm_loc": f"{local_timestamp.minute:02d}",
        "ss_loc": f"{local_timestamp.second:02d}",
    }


def _build_core_payload_fields(
    event: LogEvent, context_dict: dict[str, Any], extra_dict: dict[str, Any], context_fields: str, formatted_chain: ChainInput
) -> dict[str, Any]:
    """Build core payload fields (level, logger, context, etc.)."""
    level_text = event.level.severity.upper()
    return {
        "level": level_text,
        "level_enum": event.level,
        "LEVEL": level_text,
        "level_name": event.level.name,
        "level_code": event.level.code,
        "level_icon": event.level.icon,
        "logger_name": event.logger_name,
        "event_id": event.event_id,
        "message": event.message,
        "context": context_dict,
        "extra": extra_dict,
        "context_fields": context_fields,
        "user_name": context_dict.get("user_name"),
        "theme": extra_dict.get("theme"),
        "hostname": context_dict.get("hostname"),
        "process_id": context_dict.get("process_id"),
        "process_id_chain": _normalise_process_chain(formatted_chain),
        "pathname": extra_dict.get("pathname"),
        "lineno": extra_dict.get("lineno"),
        "funcName": extra_dict.get("funcName"),
    }


def build_format_payload(event: LogEvent) -> dict[str, Any]:
    """Construct the dictionary consumed by console/text dump templates.

    Centralizes the placeholder contract for Rich console and dump adapters.
    Returns dict with timestamp variants, level metadata, context/extra fields.
    """
    context_dict = event.context.to_dict(include_none=True)
    extra_dict = dict(event.extra)
    context_fields = _merge_context_and_extra(context_dict, extra_dict)
    formatted_chain = _format_process_chain_for_template(context_dict.get("process_id_chain"))

    timestamp = event.timestamp
    trimmed_timestamp = timestamp.replace(microsecond=0)
    local_timestamp = timestamp.astimezone()
    trimmed_local = local_timestamp.replace(microsecond=0)

    timestamp_fields = _build_timestamp_fields(
        timestamp, local_timestamp, trimmed_timestamp, trimmed_local, trimmed_timestamp.replace(tzinfo=None), trimmed_local.replace(tzinfo=None)
    )
    core_fields = _build_core_payload_fields(event, context_dict, extra_dict, context_fields, formatted_chain)

    payload = {**timestamp_fields, **core_fields}
    payload["level.icon"] = payload["level_icon"]  # type: ignore[index]
    payload["level.severity"] = payload["LEVEL"]  # type: ignore[index]
    return payload


__all__ = ["build_format_payload"]
