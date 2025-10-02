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

from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Iterator
import contextvars


def _new_extra_dict() -> dict[str, Any]:
    """Return a new mutable mapping for context extras."""

    return {}


_REQUIRED_FIELDS = ("service", "environment", "job_id")


def _validate_not_blank(name: str, value: str | None) -> str | None:
    """Validate that mandatory context fields contain meaningful data.

    Why
    ----
    The system design requires `service`, `environment`, and `job_id` to be
    present on every log event so downstream aggregators can group streams by
    tenant. Empty strings would still satisfy type hints but break the
    invariants described in ``module_reference.md``.

    Parameters
    ----------
    name:
        Human-readable name for the field, used when raising validation
        errors.
    value:
        Raw value provided by the caller.

    Returns
    -------
    str | None
        ``None`` when ``value`` is ``None``; otherwise the original string when
        it contains non-whitespace characters.

    Raises
    ------
    ValueError
        If a non-``None`` value consists only of whitespace.

    Examples
    --------
    >>> _validate_not_blank("service", "checkout-api")
    'checkout-api'
    >>> _validate_not_blank("service", None) is None
    True
    >>> _validate_not_blank("service", "   ")
    Traceback (most recent call last):
    ...
    ValueError: service must not be empty
    """

    if value is None:
        return None
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


@dataclass(slots=True, frozen=True)
class LogContext:
    """Immutable context propagated alongside each log event.

    Why
    ---
    ``LogContext`` encodes the observability contract described in the system
    design documents: every event must identify service and environment, and
    optional tracing/user metadata should survive across threads and
    subprocesses.

    What
    ----
    The dataclass provides value semantics for structured fields and keeps a
    shallow copy of arbitrary ``extra`` metadata. Validation and normalisation
    happen in :meth:`__post_init__`, ensuring downstream ports can rely on
    canonical shapes.

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

    Examples
    --------
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

        Side Effects
        ------------
        Mutates internal dataclass state via ``object.__setattr__`` because the
        dataclass is frozen. This keeps callers from mutating shared references
        to ``extra`` or providing invalid identifiers.
        """

        object.__setattr__(self, "service", _validate_not_blank("service", self.service) or "")
        object.__setattr__(self, "environment", _validate_not_blank("environment", self.environment) or "")
        object.__setattr__(self, "job_id", _validate_not_blank("job_id", self.job_id) or "")
        object.__setattr__(self, "extra", dict(self.extra))
        chain = tuple(int(pid) for pid in (self.process_id_chain or ()))
        object.__setattr__(self, "process_id_chain", chain)

    def to_dict(self, *, include_none: bool = False) -> dict[str, Any]:
        """Serialize the context to a dictionary understood by adapters.

        Why
        ---
        Dump adapters and queue serialisation rely on deterministic JSON-ready
        structures. Providing one canonical representation avoids scattering the
        mapping logic throughout the codebase.

        Parameters
        ----------
        include_none:
            When ``True`` preserves ``None`` fields (for round-tripping); when
            ``False`` prunes empty values for cleaner payloads.

        Returns
        -------
        dict[str, Any]
            Context fields ready for JSON encoding.

        Examples
        --------
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

    def merge(self, **overrides: Any) -> "LogContext":
        """Return a new context with ``overrides`` applied.

        Why
        ---
        Child scopes frequently need to enrich the context without mutating the
        parent frame. ``merge`` performs that copy in a single place to preserve
        invariants.

        Returns
        -------
        LogContext
            A new instance carrying the merged values.

        Examples
        --------
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

    def replace(self, **overrides: Any) -> "LogContext":
        """Alias to :func:`dataclasses.replace` for readability in tests.

        Examples
        --------
        >>> ctx = LogContext(service="svc", environment="staging", job_id="job")
        >>> ctx.replace(environment="prod").environment
        'prod'
        """

        return replace(self, **overrides)


class ContextBinder:
    """Manage :class:`LogContext` instances bound to the current execution flow.

    Why
    ---
    The Clean Architecture plan mandates context propagation across async tasks
    and subprocesses. ``ContextBinder`` centralises that stack handling so the
    rest of the codebase depends on a single abstraction.

    System Interaction
    ------------------
    Used by :func:`lib_log_rich.bind` and the application layer whenever a new
    logging scope starts.
    """

    _stack_var: contextvars.ContextVar[tuple[LogContext, ...]]

    def __init__(self) -> None:
        """Initialise the binder with an empty :mod:`contextvars` stack.

        Side Effects
        ------------
        Registers a context variable used to track the stack across async tasks.
        """

        self._stack_var = contextvars.ContextVar("lib_log_rich_context_stack", default=())

    @contextmanager
    def bind(self, **fields: Any) -> Iterator[LogContext]:
        """Bind a new context to the current scope.

        Why
        ---
        New requests, jobs, or background tasks need fresh metadata while still
        inheriting parent fields where appropriate.

        Parameters
        ----------
        **fields:
            Partial context supplied by the caller. ``service``,
            ``environment``, and ``job_id`` are mandatory when no parent
            context exists.

        Yields
        ------
        Iterator[LogContext]
            The newly bound context instance.

        Raises
        ------
        ValueError
            If mandatory fields are missing for the first context frame.

        Examples
        --------
        >>> binder = ContextBinder()
        >>> with binder.bind(service="svc", environment="prod", job_id="1") as ctx:
        ...     ctx.service
        'svc'
        >>> binder.current() is None
        True
        """

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
        """Return the context bound to the current scope, if any.

        Examples
        --------
        >>> binder = ContextBinder()
        >>> binder.current() is None
        True
        >>> with binder.bind(service="svc", environment="prod", job_id="1"):
        ...     isinstance(binder.current(), LogContext)
        True
        >>> binder.current() is None
        True
        """

        stack = self._stack_var.get()
        return stack[-1] if stack else None

    def serialize(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the context stack.

        Why
        ---
        Serialisation allows context propagation to worker processes as outlined
        in the multiprocessing section of the system design.

        Returns
        -------
        dict[str, Any]
            Payload containing a version marker for forwards compatibility.

        Examples
        --------
        >>> binder = ContextBinder()
        >>> with binder.bind(service="svc", environment="prod", job_id="1"):
        ...     payload = binder.serialize()
        >>> payload["version"]
        1
        >>> isinstance(payload["stack"], list)
        True
        """

        stack = [ctx.to_dict(include_none=True) for ctx in self._stack_var.get()]
        return {"version": 1, "stack": stack}

    def deserialize(self, payload: dict[str, Any]) -> None:
        """Restore contexts from :meth:`serialize` output.

        Side Effects
        ------------
        Replaces the current :mod:`contextvars` stack, typically in child
        processes that received a payload from :meth:`serialize`.

        Examples
        --------
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
        self._stack_var.set(stack)

    def replace_top(self, context: LogContext) -> None:
        """Replace the most recent context frame with ``context``.

        Why
        ---
        Context refresh logic (e.g., PID or hostname changes) requires
        atomically swapping the top frame without disturbing parent scopes.

        Parameters
        ----------
        context:
            New context instance that should replace the head of the stack.

        Raises
        ------
        RuntimeError
            If no context is bound when replacement is attempted.

        Examples
        --------
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

        stack = list(self._stack_var.get())
        if not stack:
            raise RuntimeError("No context is currently bound")
        stack[-1] = context
        self._stack_var.set(tuple(stack))


__all__ = ["LogContext", "ContextBinder"]
