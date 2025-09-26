"""Context handling utilities built atop :mod:`contextvars`.

Purpose
-------
Manage structured logging context stacks in a framework-agnostic manner,
ensuring the domain layer stays pure while the application layer can bind and
restore metadata across threads and subprocesses.

Contents
--------
* :class:`LogContext` – immutable dataclass capturing request/service metadata.
* :class:`ContextBinder` – stack manager providing bind/serialize/deserialize
  helpers for multi-process propagation.
* Utility helpers for validation and field normalisation.

System Role
-----------
Anchors the context requirements from ``konzept_architecture.md`` by providing a
small, testable abstraction the application layer can rely on when emitting log
events.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Iterator
import contextvars


_REQUIRED_FIELDS = ("service", "environment", "job_id")


def _validate_not_blank(name: str, value: str | None) -> str | None:
    """Ensure required string fields are present and not whitespace."""
    if value is None:
        return None
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


@dataclass(slots=True, frozen=True)
class LogContext:
    """Immutable context propagated alongside each log event.

    Attributes
    ----------
    service, environment, job_id:
        Required identifiers that scope log streams and satisfy the Clean
        Architecture requirement for explicit context.
    request_id, user_id:
        Optional correlation identifiers for tracing and auditing.
    user_name, hostname:
        Automatically populated system metadata (see :func:`lib_log_rich.init`).
    process_id:
        PID that produced the log entry; pairs with :attr:`process_id_chain`.
    process_id_chain:
        Tuple capturing parent/child PID lineage (bounded length).
    trace_id, span_id:
        Optional distributed tracing identifiers mapped from upstream systems.
    extra:
        Mutable copy of caller-supplied metadata bound to the context frame.
    """

    service: str
    environment: str
    job_id: str
    request_id: str | None = None
    user_id: str | None = None
    user_name: str | None = None
    hostname: str | None = None
    process_id: int | None = None
    process_id_chain: tuple[int, ...] = ()
    trace_id: str | None = None
    span_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "service", _validate_not_blank("service", self.service) or "")
        object.__setattr__(self, "environment", _validate_not_blank("environment", self.environment) or "")
        object.__setattr__(self, "job_id", _validate_not_blank("job_id", self.job_id) or "")
        object.__setattr__(self, "extra", dict(self.extra))
        chain = tuple(int(pid) for pid in (self.process_id_chain or ()))
        object.__setattr__(self, "process_id_chain", chain)

    def to_dict(self, *, include_none: bool = False) -> dict[str, Any]:
        """Serialize the context to a dictionary."""

        chain_list = list(self.process_id_chain)
        data = {
            "service": self.service,
            "environment": self.environment,
            "job_id": self.job_id,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "hostname": self.hostname,
            "process_id": self.process_id,
            "process_id_chain": chain_list if chain_list else None,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "extra": dict(self.extra),
        }
        if include_none:
            if data["process_id_chain"] is None:
                data["process_id_chain"] = []
            return data
        return {key: value for key, value in data.items() if value not in (None, {}, [])}

    def merge(self, **overrides: Any) -> "LogContext":
        """Return a new context with ``overrides`` applied."""

        data = self.to_dict(include_none=True)
        data.update({k: v for k, v in overrides.items() if v is not None})
        if overrides.get("extra") is not None:
            data["extra"] = dict(overrides["extra"])
        return LogContext(**data)

    def replace(self, **overrides: Any) -> "LogContext":
        """Alias to :func:`dataclasses.replace` for readability in tests."""

        return replace(self, **overrides)


class ContextBinder:
    """Manage :class:`LogContext` instances bound to the current execution flow."""

    _stack_var: contextvars.ContextVar[tuple[LogContext, ...]]

    def __init__(self) -> None:
        self._stack_var = contextvars.ContextVar("lib_log_rich_context_stack", default=())

    @contextmanager
    def bind(self, **fields: Any) -> Iterator[LogContext]:
        """Bind a new context to the current scope."""

        stack = self._stack_var.get()
        base = stack[-1] if stack else None

        if base is None:
            missing = [name for name in _REQUIRED_FIELDS if not fields.get(name)]
            if missing:
                raise ValueError("Missing required context fields when no parent context exists: " + ", ".join(missing))
            chain_source = fields.get("process_id_chain") or ()
            context = LogContext(
                service=fields["service"],
                environment=fields["environment"],
                job_id=fields["job_id"],
                request_id=fields.get("request_id"),
                user_id=fields.get("user_id"),
                user_name=fields.get("user_name"),
                hostname=fields.get("hostname"),
                process_id=fields.get("process_id"),
                process_id_chain=tuple(int(pid) for pid in chain_source),
                trace_id=fields.get("trace_id"),
                span_id=fields.get("span_id"),
                extra=dict(fields.get("extra", {})),
            )
            if not context.process_id_chain and context.process_id is not None:
                context = context.replace(process_id_chain=(int(context.process_id),))
        else:
            overrides = {key: value for key, value in fields.items() if value is not None}
            context = base.merge(**overrides)
            if context.process_id is not None and not context.process_id_chain:
                context = context.replace(process_id_chain=(int(context.process_id),))

        token = self._stack_var.set(stack + (context,))
        try:
            yield context
        finally:
            self._stack_var.reset(token)

    def current(self) -> LogContext | None:
        """Return the context bound to the current scope, if any."""

        stack = self._stack_var.get()
        return stack[-1] if stack else None

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the context stack."""

        stack = [ctx.to_dict(include_none=True) for ctx in self._stack_var.get()]
        return {"version": 1, "stack": stack}

    def deserialize(self, payload: dict[str, Any]) -> None:
        """Restore contexts from :meth:`serialize` output."""

        stack_data = payload.get("stack", [])
        stack = tuple(LogContext(**data) for data in stack_data)
        self._stack_var.set(stack)

    def replace_top(self, context: LogContext) -> None:
        """Replace the most recent context frame with ``context``."""

        stack = list(self._stack_var.get())
        if not stack:
            raise RuntimeError("No context is currently bound")
        stack[-1] = context
        self._stack_var.set(tuple(stack))

    def clear(self) -> None:
        """Remove all bound context information."""

        self._stack_var.set(())


__all__ = ["ContextBinder", "LogContext"]
