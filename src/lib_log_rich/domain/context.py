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
Anchors the context requirements from ``concept_architecture.md`` by providing a
small, testable abstraction the application layer can rely on when emitting log
events.

Alignment Notes
---------------
Terminology and field semantics mirror the "Context & Field Management" section
in ``docs/systemdesign/concept_architecture.md`` so that documentation, runtime
behaviour, and operator expectations stay in sync.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any


def _new_extra_dict() -> dict[str, Any]:
    """Return a new mutable mapping for context extras."""
    return {}


_REQUIRED_FIELDS = ("service", "environment", "job_id")


def _validate_not_blank(name: str, value: str | None) -> str:
    """Validate that mandatory context fields contain meaningful data.

    The system design requires `service`, `environment`, and `job_id` to be
    present on every log event so downstream aggregators can group streams by
    tenant. Empty strings or ``None`` would still satisfy type hints but break
    the invariants described in ``module_reference.md``.

    Args:
        name: Human-readable name for the field, used when raising validation
            errors.
        value: Raw value provided by the caller.

    Returns:
        The original string when it contains non-whitespace characters.

    Raises:
        ValueError: If ``value`` is ``None`` or consists only of whitespace.

    Example:
        >>> _validate_not_blank("service", "checkout-api")
        'checkout-api'
        >>> _validate_not_blank("service", None)
        Traceback (most recent call last):
        ...
        ValueError: service must not be empty
        >>> _validate_not_blank("service", "   ")
        Traceback (most recent call last):
        ...
        ValueError: service must not be empty

    """
    if value is None:
        raise ValueError(f"{name} must not be empty")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


@dataclass(slots=True, frozen=True)
class LogContext:
    """Immutable context propagated alongside each log event.

    ``LogContext`` encodes the observability contract described in the system
    design documents: every event must identify service and environment, and
    optional tracing/user metadata should survive across threads and
    subprocesses. The dataclass provides value semantics for structured fields
    and keeps a shallow copy of arbitrary ``extra`` metadata.

    Attributes:
        service: Required service identifier that scopes log streams.
        environment: Required environment identifier (prod, staging, etc.).
        job_id: Required job identifier for grouping related events.
        request_id: Optional correlation identifier for tracing.
        user_id: Optional user identifier for auditing.
        user_name: Automatically populated system metadata.
        hostname: Automatically populated hostname.
        process_id: PID that produced the log entry.
        process_id_chain: Tuple capturing parent/child PID lineage.
        trace_id: Optional distributed tracing identifier.
        span_id: Optional span identifier for distributed tracing.
        extra: Mutable copy of caller-supplied metadata bound to the context.

    Example:
        >>> ctx = LogContext(service="checkout", environment="prod", job_id="job-1")
        >>> ctx.service, ctx.environment, ctx.job_id
        ('checkout', 'prod', 'job-1')
        >>> ctx.extra == {}
        True

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
    extra: dict[str, Any] = field(default_factory=_new_extra_dict)

    def __post_init__(self) -> None:
        """Normalise mandatory fields and enforce defensive copies.

        Mutates internal dataclass state via ``object.__setattr__`` because the
        dataclass is frozen. This keeps callers from mutating shared references
        to ``extra`` or providing invalid identifiers.
        """
        object.__setattr__(self, "service", _validate_not_blank("service", self.service))
        object.__setattr__(self, "environment", _validate_not_blank("environment", self.environment))
        object.__setattr__(self, "job_id", _validate_not_blank("job_id", self.job_id))
        object.__setattr__(self, "extra", dict(self.extra))
        chain = tuple(int(pid) for pid in (self.process_id_chain or ()))
        object.__setattr__(self, "process_id_chain", chain)

    def to_dict(self, *, include_none: bool = False) -> dict[str, Any]:
        """Serialize the context to a dictionary understood by adapters.

        Dump adapters and queue serialisation rely on deterministic JSON-ready
        structures. Providing one canonical representation avoids scattering the
        mapping logic throughout the codebase.

        Args:
            include_none: When ``True`` preserves ``None`` fields (for
                round-tripping); when ``False`` prunes empty values for cleaner
                payloads.

        Returns:
            Context fields ready for JSON encoding.

        Example:
            >>> ctx = LogContext(service="checkout", environment="prod", job_id="job-9")
            >>> sorted(ctx.to_dict().keys())
            ['environment', 'job_id', 'service']
            >>> ctx.to_dict(include_none=True)["process_id_chain"]
            []

        """
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

    def merge(self, **overrides: Any) -> LogContext:
        """Return a new context with ``overrides`` applied.

        Child scopes frequently need to enrich the context without mutating the
        parent frame. ``merge`` performs that copy in a single place to preserve
        invariants.

        Args:
            **overrides: Field values to override in the new context.

        Returns:
            A new instance carrying the merged values.

        Example:
            >>> parent = LogContext(service="core", environment="prod", job_id="root")
            >>> child = parent.merge(request_id="req-1")
            >>> (parent.request_id, child.request_id)
            (None, 'req-1')

        """
        data = self.to_dict(include_none=True)
        data.update({k: v for k, v in overrides.items() if v is not None})
        if overrides.get("extra") is not None:
            data["extra"] = dict(overrides["extra"])
        return LogContext(**data)

    def replace(self, **overrides: Any) -> LogContext:
        """Alias to :func:`dataclasses.replace` for readability in tests.

        Args:
            **overrides: Field values to replace in the new context.

        Returns:
            A new LogContext instance with the specified fields replaced.

        Example:
            >>> ctx = LogContext(service="svc", environment="staging", job_id="job")
            >>> ctx.replace(environment="prod").environment
            'prod'

        """
        return replace(self, **overrides)


class ContextBinder:
    """Manage :class:`LogContext` instances bound to the current execution flow.

    The Clean Architecture plan mandates context propagation across async tasks
    and subprocesses. ``ContextBinder`` centralises that stack handling so the
    rest of the codebase depends on a single abstraction. Used by
    :func:`lib_log_rich.bind` and the application layer whenever a new
    logging scope starts.
    """

    _stack_var: contextvars.ContextVar[tuple[LogContext, ...]]

    def __init__(self) -> None:
        """Initialise the binder with an empty :mod:`contextvars` stack.

        Note:
            Registers a context variable used to track the stack across async
            tasks.

        """
        self._stack_var = contextvars.ContextVar("lib_log_rich_context_stack", default=())
        self._bootstrap_stack: tuple[LogContext, ...] = ()

    def _stack(self) -> tuple[LogContext, ...]:
        """Return the active stack, restoring the bootstrap frame when missing."""
        stack = self._stack_var.get()
        if stack:
            return stack
        if self._bootstrap_stack:
            self._stack_var.set(self._bootstrap_stack)
            return self._bootstrap_stack
        return ()

    def _create_root_context(self, fields: dict[str, Any]) -> LogContext:
        """Create a new root context from fields, validating required fields."""
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
        return self._ensure_process_chain(context)

    def _create_child_context(self, base: LogContext, fields: dict[str, Any]) -> LogContext:
        """Create a child context by merging fields into base."""
        overrides = {key: value for key, value in fields.items() if value is not None}
        context = base.merge(**overrides)
        return self._ensure_process_chain(context)

    @staticmethod
    def _ensure_process_chain(context: LogContext) -> LogContext:
        """Ensure process_id_chain is set if process_id exists."""
        if context.process_id is not None and not context.process_id_chain:
            return context.replace(process_id_chain=(int(context.process_id),))
        return context

    @contextmanager
    def bind(self, **fields: Any) -> Iterator[LogContext]:
        """Bind a new context to the current scope.

        New requests, jobs, or background tasks need fresh metadata while still
        inheriting parent fields where appropriate.

        Args:
            **fields: Context fields to bind (service, environment, job_id, etc.).

        Yields:
            The newly bound LogContext instance.

        Example:
            >>> binder = ContextBinder()
            >>> with binder.bind(service="svc", environment="prod", job_id="1") as ctx:
            ...     ctx.service
            'svc'
            >>> binder.current() is None
            True

        """
        stack = self._stack()
        base = stack[-1] if stack else None

        context = self._create_root_context(fields) if base is None else self._create_child_context(base, fields)

        token = self._stack_var.set(stack + (context,))
        try:
            yield context
        finally:
            self._stack_var.reset(token)

    def current(self) -> LogContext | None:
        """Return the context bound to the current scope, if any.

        Returns:
            The current LogContext or None if no context is bound.

        Example:
            >>> binder = ContextBinder()
            >>> binder.current() is None
            True
            >>> with binder.bind(service="svc", environment="prod", job_id="1"):
            ...     isinstance(binder.current(), LogContext)
            True
            >>> binder.current() is None
            True

        """
        stack = self._stack()
        return stack[-1] if stack else None

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the context stack.

        Serialisation allows context propagation to worker processes as outlined
        in the multiprocessing section of the system design.

        Returns:
            Payload containing a version marker for forwards compatibility.

        Example:
            >>> binder = ContextBinder()
            >>> with binder.bind(service="svc", environment="prod", job_id="1"):
            ...     payload = binder.serialize()
            >>> payload["version"]
            1
            >>> isinstance(payload["stack"], list)
            True

        """
        stack = [ctx.to_dict(include_none=True) for ctx in self._stack()]
        return {"version": 1, "stack": stack}

    def deserialize(self, payload: dict[str, Any]) -> None:
        """Restore contexts from :meth:`serialize` output.

        Replaces the current :mod:`contextvars` stack, typically in child
        processes that received a payload from :meth:`serialize`.

        Args:
            payload: Serialized context stack from :meth:`serialize`.

        Example:
            >>> binder = ContextBinder()
            >>> binder.deserialize({"version": 1, "stack": [{
            ...     "service": "svc",
            ...     "environment": "prod",
            ...     "job_id": "1",
            ...     "extra": {},
            ...     "process_id_chain": []
            ... }]})
            >>> isinstance(binder.current(), LogContext)
            True

        """
        stack_data = payload.get("stack", [])
        stack = tuple(LogContext(**data) for data in stack_data)
        self._bootstrap_stack = stack
        self._stack_var.set(stack)

    def replace_top(self, context: LogContext) -> None:
        """Replace the most recent context frame with ``context``.

        Context refresh logic (e.g., PID or hostname changes) requires
        atomically swapping the top frame without disturbing parent scopes.

        Args:
            context: New context instance that should replace the head of the
                stack.

        Raises:
            RuntimeError: If no context is bound when replacement is attempted.

        Example:
            >>> binder = ContextBinder()
            >>> try:
            ...     binder.replace_top(LogContext(service="svc", environment="env", job_id="1"))
            ... except RuntimeError:
            ...     pass
            >>> with binder.bind(service="svc", environment="env", job_id="1"):
            ...     new_ctx = LogContext(service="svc", environment="env", job_id="1", request_id="req")
            ...     binder.replace_top(new_ctx)
            ...     binder.current().request_id
            'req'

        """
        stack = list(self._stack())
        if not stack:
            raise RuntimeError("No context is currently bound")
        stack[-1] = context
        self._stack_var.set(tuple(stack))


__all__ = ["LogContext", "ContextBinder"]
