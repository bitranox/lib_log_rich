"""Pydantic payload schemas for adapter serialisation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


def _new_int_list() -> list[int]:
    return []


def _new_str_dict() -> dict[str, Any]:
    return {}


class LogContextPayload(BaseModel):
    """Shape of context metadata included in JSON dumps."""

    service: str
    environment: str
    job_id: str
    request_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    hostname: str | None = None
    process_id: int | None = None
    process_id_chain: list[int] = Field(default_factory=_new_int_list)
    trace_id: str | None = None
    span_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=_new_str_dict)

    model_config = ConfigDict(from_attributes=True, frozen=True)

    @field_validator("process_id_chain", mode="before")
    @classmethod
    def _coerce_chain(cls, value: Any) -> list[int]:
        if value in (None, ""):
            return []
        if isinstance(value, (list, tuple)):
            result_list: list[int] = []
            for item in cast(Iterable[Any], value):
                result_list.append(int(item))
            return result_list
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
            result_iter: list[int] = []
            for item in cast(Iterable[Any], value):
                result_iter.append(int(item))
            return result_iter
        return [int(value)]

    @field_validator("extra", mode="before")
    @classmethod
    def _dict_copy(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            mapping = cast(Mapping[Any, Any], value)
            for key, val in mapping.items():
                result[str(key)] = val
            return result
        raise TypeError("extra metadata must be a mapping")

    @classmethod
    def from_context(cls, context: LogContext) -> "LogContextPayload":
        """Construct a payload from a domain context object."""

        return cls.model_validate(context, from_attributes=True)


class LogEventPayload(BaseModel):
    """JSON-ready representation of :class:`LogEvent`."""

    event_id: str
    timestamp: datetime
    logger_name: str
    level: str
    level_name: str
    level_value: int
    level_code: str
    level_icon: str
    message: str
    context: LogContextPayload
    extra: dict[str, Any] = Field(default_factory=_new_str_dict)
    exc_info: str | None = None

    model_config = ConfigDict(from_attributes=True, frozen=True)

    @field_validator("extra", mode="before")
    @classmethod
    def _copy_extra(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            mapping = cast(Mapping[Any, Any], value)
            for key, val in mapping.items():
                result[str(key)] = val
            return result
        raise TypeError("event extras must be a mapping")

    @classmethod
    def from_event(cls, event: LogEvent) -> "LogEventPayload":
        """Create a payload from a domain event."""

        level: LogLevel = event.level
        context_payload = LogContextPayload.from_context(event.context)
        return cls(
            event_id=event.event_id,
            timestamp=event.timestamp,
            logger_name=event.logger_name,
            level=level.severity,
            level_name=level.name,
            level_value=level.value,
            level_code=level.code,
            level_icon=level.icon,
            message=event.message,
            context=context_payload,
            extra=dict(event.extra),
            exc_info=event.exc_info,
        )


__all__ = ["LogContextPayload", "LogEventPayload"]
