"""JSON text form of a :class:`ContextSnapshot`, for process boundaries that carry only text.

Purpose
-------
Render a binder's context stack as JSON text and parse such text back,
validating every field into a typed model before any domain object is built.

Contents
--------
* :func:`dump_context_snapshot` - snapshot to JSON text.
* :func:`load_context_snapshot` - JSON text to snapshot, refusing malformed input.

System Role
-----------
The text arrives from outside the process (an environment variable, a pipe, a
file), so it is boundary input: parsing it here keeps the domain free of JSON
and of raw dictionaries, and turns a bad document into one refusal naming each
field instead of a ``KeyError`` or ``TypeError`` from deep inside context
construction. Where the snapshot object itself can travel (``multiprocessing``
arguments pickle it), no text form is needed.

Alignment Notes
---------------
The text is ``json.dumps`` output with every context field present, ``None``
fields as ``null``, the PID chain as an array and ``extra`` as an object, in
:class:`LogContext` field order. The reader is stricter than the writer needs:
it refuses unknown keys, booleans where integers belong and ``null`` for the
PID chain or ``extra``, so only what :func:`dump_context_snapshot` could have
written is accepted.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from lib_log_rich.domain.context import CONTEXT_SNAPSHOT_VERSION, ContextSnapshot, LogContext

from ._validation import validation_error_lines


def _not_blank(value: str) -> str:
    """Refuse text holding only whitespace, as LogContext does for its required fields."""
    if not value.strip():
        raise ValueError("must not be empty")
    return value


def _supported_version(value: int) -> int:
    """Refuse a format version this release cannot read."""
    if value != CONTEXT_SNAPSHOT_VERSION:
        raise ValueError(f"unsupported version {value}; expected {CONTEXT_SNAPSHOT_VERSION}")
    return value


def _new_extra() -> dict[str, Any]:
    """Return a fresh mapping for an absent ``extra`` field."""
    return {}


_NonBlank = Annotated[str, AfterValidator(_not_blank)]
_Version = Annotated[int, AfterValidator(_supported_version)]


class _SnapshotFrame(BaseModel):
    """One frame of the stack: a :class:`LogContext` with every field spelled out."""

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

    _context: LogContext = PrivateAttr()

    @model_validator(mode="after")
    def _build_context(self) -> _SnapshotFrame:
        # Built during validation so a refusal LogContext adds beyond the field
        # rules above surfaces as a located validation error, rendered like
        # every other one, instead of escaping as a bare ValueError.
        self._context = LogContext(
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
        return self

    @classmethod
    def from_context(cls, context: LogContext) -> _SnapshotFrame:
        """Build the text form of a domain context."""
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

    @property
    def context(self) -> LogContext:
        """Return the domain context this frame describes."""
        return self._context


class _SnapshotDocument(BaseModel):
    """The whole JSON document: format version plus frames, outermost first."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: _Version
    stack: tuple[_SnapshotFrame, ...]


def dump_context_snapshot(snapshot: ContextSnapshot) -> str:
    """Render ``snapshot`` as JSON text that :func:`load_context_snapshot` reads back.

    Args:
        snapshot: The stack to render.

    Returns:
        The JSON document.

    Raises:
        TypeError: If a frame's ``extra`` holds a value JSON cannot encode.

    Example:
        >>> dump_context_snapshot(ContextSnapshot())
        '{"version": 1, "stack": []}'

    """
    document = _SnapshotDocument(
        version=snapshot.version,
        stack=tuple(_SnapshotFrame.from_context(context) for context in snapshot.stack),
    )
    # json.dumps of the python-mode dump, not model_dump_json: it keeps the
    # separators and ASCII escaping 6.x wrote, and it refuses an unencodable
    # ``extra`` value with TypeError instead of pydantic's own fallback.
    return json.dumps(document.model_dump())


def load_context_snapshot(text: str) -> ContextSnapshot:
    """Parse JSON text written by :func:`dump_context_snapshot`, validating every field.

    Absent optional fields take their :class:`LogContext` defaults; ``version``,
    ``stack`` and each frame's ``service``, ``environment`` and ``job_id`` are
    required.

    Args:
        text: The JSON document.

    Returns:
        The snapshot it describes.

    Raises:
        ValueError: If ``text`` is not JSON or a field is missing, unknown or
            of the wrong type; the message names each refused field by its
            dotted location (``stack.0.process_id``) without echoing its value.

    Example:
        >>> load_context_snapshot('{"version": 1, "stack": [{"service": "svc", "environment": "prod", "job_id": "1"}]}').stack[0].job_id
        '1'
        >>> load_context_snapshot('{"version": 1, "stack": [{"service": "svc", "environment": "prod", "job_id": ""}]}')
        Traceback (most recent call last):
        ...
        ValueError: Invalid context snapshot: stack.0.job_id: must not be empty

    """
    try:
        document = _SnapshotDocument.model_validate_json(text)
    except ValidationError as exc:
        raise ValueError("Invalid context snapshot: " + "; ".join(validation_error_lines(exc))) from exc
    return ContextSnapshot(stack=tuple(frame.context for frame in document.stack), version=document.version)


__all__ = ["dump_context_snapshot", "load_context_snapshot"]
