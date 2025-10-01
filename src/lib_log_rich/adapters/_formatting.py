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

from typing import Any

from lib_log_rich.domain.events import LogEvent


def _normalise_process_chain(values: Any) -> str:
    if not values:
        return ""
    if isinstance(values, (list, tuple)):
        return ">".join(str(item) for item in values)
    return str(values)


def build_format_payload(event: LogEvent) -> dict[str, Any]:
    """Return the mapping of placeholders exposed to format templates."""

    context_dict = event.context.to_dict(include_none=True)
    extra_dict = dict(event.extra)

    merged_pairs = {key: value for key, value in {**context_dict, **extra_dict}.items() if value not in (None, {})}
    context_fields = ""
    if merged_pairs:
        context_fields = " " + " ".join(f"{key}={value}" for key, value in sorted(merged_pairs.items()))

    chain_values = context_dict.get("process_id_chain") or ()

    level_text = event.level.severity.upper()

    payload: dict[str, Any] = {
        "timestamp": event.timestamp.isoformat(),
        "YYYY": f"{event.timestamp.year:04d}",
        "MM": f"{event.timestamp.month:02d}",
        "DD": f"{event.timestamp.day:02d}",
        "hh": f"{event.timestamp.hour:02d}",
        "mm": f"{event.timestamp.minute:02d}",
        "ss": f"{event.timestamp.second:02d}",
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
        "hostname": context_dict.get("hostname"),
        "process_id": context_dict.get("process_id"),
        "process_id_chain": _normalise_process_chain(chain_values),
    }

    # Provide dotted aliases used by legacy templates.
    payload["level.icon"] = payload["level_icon"]  # type: ignore[index]
    payload["level.severity"] = payload["LEVEL"]  # type: ignore[index]

    return payload


__all__ = ["build_format_payload"]
