"""Render a Pydantic ``ValidationError`` as ``field: reason`` lines for a refusal.

Purpose
-------
Every boundary that refuses input through a Pydantic model (runtime settings,
the JSONL checkpoint) reports the same way, from one renderer, so the two
cannot drift apart.

System Role
-----------
Pydantic's own rendering carries model names, an echo of the caller's input
and a versioned documentation URL, none of which tells a caller what to change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import ValidationError
    from pydantic_core import ErrorDetails


def validation_error_lines(error: ValidationError) -> list[str]:
    """Return one ``<dotted location>: <reason>`` line per refused value.

    A validator's own message is shown exactly as it was raised; an error on
    the whole input (no location) is just its reason.

    Example:
        >>> from pydantic import BaseModel, ValidationError, field_validator
        >>> class Probe(BaseModel):
        ...     size: int
        ...     name: str
        ...     @field_validator("size")
        ...     @classmethod
        ...     def _positive(cls, value: int) -> int:
        ...         if value <= 0:
        ...             raise ValueError("size must be positive")
        ...         return value
        >>> try:
        ...     Probe.model_validate({"size": 0})
        ... except ValidationError as exc:
        ...     validation_error_lines(exc)
        ['size: size must be positive', 'name: Field required']
    """
    return [_describe_error_line(line) for line in error.errors()]


def describe_validation_error(error: ValidationError) -> str:
    """Render a validation error as ``field: reason`` clauses joined by ``; ``.

    Example:
        >>> from pydantic import BaseModel, ValidationError
        >>> class Probe(BaseModel):
        ...     size: int
        >>> try:
        ...     Probe.model_validate({"size": "big"})
        ... except ValidationError as exc:
        ...     print(describe_validation_error(exc))
        size: Input should be a valid integer, unable to parse string as an integer
    """
    return "; ".join(validation_error_lines(error))


def _describe_error_line(line: ErrorDetails) -> str:
    """Render one entry of ``ValidationError.errors()``."""
    cause = line.get("ctx", {}).get("error")
    reason = str(cause) if cause is not None else str(line["msg"])
    location = ".".join(str(part) for part in line["loc"])
    return f"{location}: {reason}" if location else reason


__all__ = ["describe_validation_error", "validation_error_lines"]
