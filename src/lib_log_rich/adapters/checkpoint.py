"""Newline-delimited JSON checkpoint for the ring buffer.

Purpose
-------
Persist ring-buffer events to a JSONL file and read them back, validating every
stored line into a typed record before a :class:`LogEvent` is built from it.

Contents
--------
* :class:`JsonlCheckpoint` - implementation of
  :class:`lib_log_rich.domain.ring_buffer.EventCheckpoint`.
* :class:`CheckpointRecord` / :class:`CheckpointContext` - the boundary models
  for one stored line.

System Role
-----------
The file is external input: it may be truncated by a crash, edited by hand or
written by another version. Parsing it here keeps the domain free of I/O and of
raw dictionaries, and turns a bad line into one refusal naming the line and the
field instead of a ``KeyError`` from deep inside event construction.

Alignment Notes
---------------
One line is :meth:`LogEvent.to_json` byte for byte: sorted keys, an ISO-8601
``+00:00`` timestamp, the level as its lowercase severity, and ``None`` / empty
context fields left out. Files written by lib_log_rich 6.x therefore load, and
files written here load in 6.x.
"""

from __future__ import annotations

from datetime import timezone
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    field_serializer,
    model_serializer,
)

from lib_log_rich.domain import json_codec
from lib_log_rich.domain.context import LogContext
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel

from ._validation import validation_error_lines

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path


def _not_blank(value: str) -> str:
    """Refuse text holding only whitespace, as LogContext and LogEvent do."""
    if not value.strip():
        raise ValueError("must not be empty")
    return value


_SEVERITY_NAMES = ", ".join(level.severity for level in LogLevel)


def _level_from_severity(value: object) -> LogLevel:
    """Turn a stored severity name (``"warning"``) into its :class:`LogLevel`.

    The format only ever stores the name, so a number or a boolean is refused
    here, naming the valid names, rather than reaching strict enum validation:
    that would accept the bare value ``20`` and word a refusal in the enum's
    internal integers. A :class:`LogLevel` member passes: that is the write
    path, building a record from an event.
    """
    if isinstance(value, LogLevel):
        return value
    if not isinstance(value, str):
        raise ValueError(f"expected a severity name ({_SEVERITY_NAMES})")
    return LogLevel.from_name(value)


def _in_utc(value: datetime) -> datetime:
    """Convert an aware timestamp to UTC, as LogEvent does, refusing an overflow.

    An offset at the edge of the datetime range ("0001-01-01T00:00:00+01:00")
    parses but cannot be expressed in UTC; LogEvent would raise OverflowError,
    which is not a ValueError and would escape the line-numbered refusal.
    """
    try:
        return value.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError("out of range once converted to UTC") from exc


def _new_extra() -> dict[str, Any]:
    """Return a fresh mapping for an absent ``extra`` field."""
    return {}


_NonBlank = Annotated[str, AfterValidator(_not_blank)]
_StoredLevel = Annotated[LogLevel, BeforeValidator(_level_from_severity)]
_StoredTimestamp = Annotated[AwareDatetime, AfterValidator(_in_utc)]

# Context fields LogContext.to_dict() leaves out when they hold no value.
_EMPTY_CONTEXT_VALUES: tuple[object, ...] = (None, {}, ())
# Event fields LogEvent.to_dict() leaves out when they are None.
_OPTIONAL_EVENT_FIELDS = ("exc_info", "stack_info")


class CheckpointContext(BaseModel):
    """The ``context`` object of one stored line."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    service: _NonBlank
    environment: _NonBlank
    job_id: _NonBlank
    request_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    hostname: str | None = None
    process_id: int | None = None
    process_id_chain: tuple[int, ...] = ()
    trace_id: str | None = None
    span_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=_new_extra)

    @model_serializer(mode="wrap")
    def _leave_out_empty(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        return {key: value for key, value in data.items() if value not in _EMPTY_CONTEXT_VALUES}

    @classmethod
    def from_context(cls, context: LogContext) -> CheckpointContext:
        """Build the stored form of a domain context."""
        return cls(
            service=context.service,
            environment=context.environment,
            job_id=context.job_id,
            request_id=context.request_id,
            user_id=context.user_id,
            user_name=context.user_name,
            hostname=context.hostname,
            process_id=context.process_id,
            process_id_chain=context.process_id_chain,
            trace_id=context.trace_id,
            span_id=context.span_id,
            extra=dict(context.extra),
        )

    def to_context(self) -> LogContext:
        """Build the domain context this record stores."""
        return LogContext(
            service=self.service,
            environment=self.environment,
            job_id=self.job_id,
            request_id=self.request_id,
            user_id=self.user_id,
            user_name=self.user_name,
            hostname=self.hostname,
            process_id=self.process_id,
            process_id_chain=self.process_id_chain,
            trace_id=self.trace_id,
            span_id=self.span_id,
            extra=dict(self.extra),
        )


class CheckpointRecord(BaseModel):
    """One stored line: a :class:`LogEvent` in its persisted form."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    event_id: str = Field(min_length=1)
    timestamp: _StoredTimestamp
    logger_name: str
    level: _StoredLevel
    message: _NonBlank
    context: CheckpointContext
    extra: dict[str, Any] = Field(default_factory=_new_extra)
    exc_info: str | None = None
    stack_info: str | None = None

    @field_serializer("level")
    def _severity(self, level: LogLevel) -> str:
        return level.severity

    @field_serializer("timestamp")
    def _iso_timestamp(self, timestamp: datetime) -> str:
        # Pinned here, not left to the encoder: pydantic's JSON mode writes "Z",
        # which Python 3.10's datetime.fromisoformat cannot read, while 6.x
        # files and LogEvent.to_json() use "+00:00".
        return timestamp.isoformat()

    @model_serializer(mode="wrap")
    def _leave_out_unset(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for key in _OPTIONAL_EVENT_FIELDS:
            if data.get(key) is None:
                data.pop(key, None)
        return data

    @classmethod
    def from_event(cls, event: LogEvent) -> CheckpointRecord:
        """Build the stored form of a domain event."""
        return cls(
            event_id=event.event_id,
            timestamp=event.timestamp,
            logger_name=event.logger_name,
            level=event.level,
            message=event.message,
            context=CheckpointContext.from_context(event.context),
            extra=dict(event.extra),
            exc_info=event.exc_info,
            stack_info=event.stack_info,
        )

    def to_event(self) -> LogEvent:
        """Build the domain event this record stores."""
        return LogEvent(
            event_id=self.event_id,
            timestamp=self.timestamp,
            logger_name=self.logger_name,
            level=self.level,
            message=self.message,
            context=self.context.to_context(),
            extra=dict(self.extra),
            exc_info=self.exc_info,
            stack_info=self.stack_info,
        )

    def to_line(self) -> str:
        """Render the record as one JSONL line (without the newline)."""
        # The shared codec rather than model_dump_json: it sorts keys, and it encodes
        # the free-form ``extra`` values exactly as LogEvent.to_json() does.
        return json_codec.dumps(self.model_dump(), sort_keys=True)


class JsonlCheckpoint:
    """Store ring-buffer events in a newline-delimited JSON file.

    Args:
        path: File to read and append to; created (with its parents) on the
            first append.

    Example:
        >>> import tempfile
        >>> from datetime import datetime, timezone
        >>> from pathlib import Path
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('1', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'a', ctx)
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     checkpoint = JsonlCheckpoint(Path(tmp) / 'events.jsonl')
        ...     checkpoint.append([event])
        ...     [stored.event_id for stored in checkpoint.load()]
        ['1']

    """

    def __init__(self, path: Path) -> None:
        """Bind the checkpoint to ``path``; nothing is read until :meth:`load`."""
        self._path = path

    @property
    def path(self) -> Path:
        """Return the checkpoint file's path."""
        return self._path

    def load(self) -> list[LogEvent]:
        """Return the stored events, oldest first.

        A missing file is an empty checkpoint and blank lines are skipped.

        Raises:
            ValueError: For the first line that is not a valid stored event,
                naming the file, the line number and each refused field.

        """
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                return [self._parse(number, line) for number, line in enumerate(handle, start=1) if line.strip()]
        except FileNotFoundError:
            return []

    def append(self, events: Sequence[LogEvent]) -> None:
        """Append ``events`` to the file, one line each."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(CheckpointRecord.from_event(event).to_line())
                handle.write("\n")

    def _parse(self, number: int, line: str) -> LogEvent:
        """Validate one non-blank line into an event."""
        # The event is built inside the same try so an invariant the domain
        # adds later, and the model does not yet mirror, is still refused with
        # the file and line rather than escaping bare.
        try:
            return CheckpointRecord.model_validate_json(line).to_event()
        except ValidationError as exc:
            details = "\n".join(f"  {entry}" for entry in validation_error_lines(exc))
            raise ValueError(f"Invalid checkpoint {self._path}, line {number}:\n{details}") from exc
        except ValueError as exc:
            raise ValueError(f"Invalid checkpoint {self._path}, line {number}:\n  {exc}") from exc


__all__ = ["CheckpointContext", "CheckpointRecord", "JsonlCheckpoint"]
