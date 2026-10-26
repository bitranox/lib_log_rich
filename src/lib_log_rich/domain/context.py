"""Context handling utilities built atop :mod:`contextvars`.

Purpose
-------
Manage structured logging context stacks in a framework-agnostic manner,
ensuring the domain layer stays pure while the application layer can bind and
restore metadata across threads and subprocesses.

Contents
--------
* :class:`LogContext` - immutable dataclass capturing request/service metadata.
* :class:`ContextBinder` - stack manager providing bind/serialize/deserialize
  helpers for multi-process propagation.
* :class:`ContextSnapshot` - the binder's stack as a value for handing to
  another process; its JSON text form lives in
  :mod:`lib_log_rich.adapters.context_snapshot`.
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
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, TypeVar

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping, Sequence

_T = TypeVar("_T")


def _new_extra_dict() -> dict[str, Any]:
    """Return a new mutable mapping for context extras."""
    return {}


def _pick(override: _T | None, current: _T) -> _T:
    """Return ``override`` unless it is ``None``, in which case keep ``current``."""
    return current if override is None else override


class _Keep(Enum):
    """Default of :meth:`LogContext.replace` meaning "leave this field as it is".

    A dedicated marker rather than ``None``, because ``None`` is a value the
    optional fields can be replaced with.
    """

    KEEP = "keep"


_KEEP: Final = _Keep.KEEP


def _kept(override: _T | _Keep, current: _T) -> _T:
    """Return ``override`` unless it is the keep marker, in which case keep ``current``."""
    return current if isinstance(override, _Keep) else override


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

    def merge(
        self,
        *,
        service: str | None = None,
        environment: str | None = None,
        job_id: str | None = None,
        request_id: str | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
        hostname: str | None = None,
        process_id: int | None = None,
        process_id_chain: Sequence[int] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> LogContext:
        """Return a new context with every non-``None`` argument applied.

        Child scopes enrich the context without mutating the parent frame.
        ``None`` means "keep the parent's value", so a child cannot clear a
        field it inherits. A given ``extra`` replaces the parent's extras
        rather than being merged into them.

        Args:
            service: Service identifier override.
            environment: Environment identifier override.
            job_id: Job identifier override.
            request_id: Correlation identifier override.
            user_id: User identifier override.
            user_name: System user name override.
            hostname: Hostname override.
            process_id: Process id override.
            process_id_chain: PID lineage override.
            trace_id: Trace identifier override.
            span_id: Span identifier override.
            extra: Replacement for the extra metadata.

        Returns:
            A new instance carrying the merged values.

        Example:
            >>> parent = LogContext(service="core", environment="prod", job_id="root", extra={"a": 1})
            >>> child = parent.merge(request_id="req-1", extra={"b": 2})
            >>> (parent.request_id, child.request_id, child.extra)
            (None, 'req-1', {'b': 2})

        """
        return replace(
            self,
            service=_pick(service, self.service),
            environment=_pick(environment, self.environment),
            job_id=_pick(job_id, self.job_id),
            request_id=_pick(request_id, self.request_id),
            user_id=_pick(user_id, self.user_id),
            user_name=_pick(user_name, self.user_name),
            hostname=_pick(hostname, self.hostname),
            process_id=_pick(process_id, self.process_id),
            process_id_chain=_pick(process_id_chain, self.process_id_chain),
            trace_id=_pick(trace_id, self.trace_id),
            span_id=_pick(span_id, self.span_id),
            extra=_pick(extra, self.extra),
        )

    def replace(
        self,
        *,
        service: str | _Keep = _KEEP,
        environment: str | _Keep = _KEEP,
        job_id: str | _Keep = _KEEP,
        request_id: str | _Keep | None = _KEEP,
        user_id: str | _Keep | None = _KEEP,
        user_name: str | _Keep | None = _KEEP,
        hostname: str | _Keep | None = _KEEP,
        process_id: int | _Keep | None = _KEEP,
        process_id_chain: tuple[int, ...] | _Keep = _KEEP,
        trace_id: str | _Keep | None = _KEEP,
        span_id: str | _Keep | None = _KEEP,
        extra: dict[str, Any] | _Keep = _KEEP,
    ) -> LogContext:
        """Return a copy with the given fields replaced; an omitted field is kept.

        Unlike :meth:`merge`, ``None`` is applied as a value, so an optional
        field can be cleared. Every value passes the same validation as the
        constructor.

        Args:
            service: Replacement service identifier.
            environment: Replacement environment identifier.
            job_id: Replacement job identifier.
            request_id: Replacement correlation identifier, or ``None``.
            user_id: Replacement user identifier, or ``None``.
            user_name: Replacement system user name, or ``None``.
            hostname: Replacement hostname, or ``None``.
            process_id: Replacement process id, or ``None``.
            process_id_chain: Replacement PID lineage.
            trace_id: Replacement trace identifier, or ``None``.
            span_id: Replacement span identifier, or ``None``.
            extra: Replacement extra metadata (copied).

        Returns:
            A new LogContext instance with the specified fields replaced.

        Example:
            >>> ctx = LogContext(service="svc", environment="staging", job_id="job", request_id="req")
            >>> changed = ctx.replace(environment="prod", request_id=None)
            >>> (changed.environment, changed.request_id, changed.job_id)
            ('prod', None, 'job')

        """
        return replace(
            self,
            service=_kept(service, self.service),
            environment=_kept(environment, self.environment),
            job_id=_kept(job_id, self.job_id),
            request_id=_kept(request_id, self.request_id),
            user_id=_kept(user_id, self.user_id),
            user_name=_kept(user_name, self.user_name),
            hostname=_kept(hostname, self.hostname),
            process_id=_kept(process_id, self.process_id),
            process_id_chain=_kept(process_id_chain, self.process_id_chain),
            trace_id=_kept(trace_id, self.trace_id),
            span_id=_kept(span_id, self.span_id),
            extra=_kept(extra, self.extra),
        )


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

    @staticmethod
    def _root_frame(service: str | None, environment: str | None, job_id: str | None) -> LogContext:
        """Return the minimal frame a parentless ``bind()`` grows from.

        Raises:
            ValueError: Naming each required field that is missing or empty.
        """
        if not service or not environment or not job_id:
            required = (("service", service), ("environment", environment), ("job_id", job_id))
            missing = [name for name, value in required if not value]
            raise ValueError("Missing required context fields when no parent context exists: " + ", ".join(missing))
        return LogContext(service=service, environment=environment, job_id=job_id)

    @staticmethod
    def _ensure_process_chain(context: LogContext) -> LogContext:
        """Ensure process_id_chain is set if process_id exists."""
        if context.process_id is not None and not context.process_id_chain:
            return context.replace(process_id_chain=(int(context.process_id),))
        return context

    @contextmanager
    def bind(
        self,
        *,
        service: str | None = None,
        environment: str | None = None,
        job_id: str | None = None,
        request_id: str | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
        hostname: str | None = None,
        process_id: int | None = None,
        process_id_chain: Sequence[int] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> Generator[LogContext, None, None]:
        """Bind a new context to the current scope.

        New requests, jobs, or background tasks need fresh metadata while still
        inheriting parent fields where appropriate. Each argument is a
        :class:`LogContext` field; ``None`` inherits the parent's value. With no
        parent frame, ``service``, ``environment`` and ``job_id`` are required.

        Args:
            service: Service identifier.
            environment: Deployment environment.
            job_id: Job identifier.
            request_id: Correlation identifier.
            user_id: User identifier.
            user_name: System user name.
            hostname: Hostname.
            process_id: Process id; seeds ``process_id_chain`` when that is empty.
            process_id_chain: PID lineage.
            trace_id: Trace identifier.
            span_id: Span identifier.
            extra: Metadata replacing the parent's extras.

        Yields:
            The newly bound LogContext instance.

        Raises:
            ValueError: With no parent frame, naming each missing required field.

        Example:
            >>> binder = ContextBinder()
            >>> with binder.bind(service="svc", environment="prod", job_id="1") as ctx:
            ...     ctx.service
            'svc'
            >>> binder.current() is None
            True

        """
        stack = self._stack()
        base = stack[-1] if stack else self._root_frame(service, environment, job_id)
        merged = base.merge(
            service=service,
            environment=environment,
            job_id=job_id,
            request_id=request_id,
            user_id=user_id,
            user_name=user_name,
            hostname=hostname,
            process_id=process_id,
            process_id_chain=process_id_chain,
            trace_id=trace_id,
            span_id=span_id,
            extra=extra,
        )
        context = self._ensure_process_chain(merged)

        token = self._stack_var.set((*stack, context))
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

    def serialize(self) -> ContextSnapshot:
        """Return the context stack as a :class:`ContextSnapshot`.

        Serialisation allows context propagation to worker processes as outlined
        in the multiprocessing section of the system design. The snapshot
        pickles (``multiprocessing`` arguments) and renders as JSON text
        through :func:`lib_log_rich.adapters.context_snapshot.dump_context_snapshot`.

        Returns:
            The stack from outermost to innermost frame.

        Example:
            >>> binder = ContextBinder()
            >>> with binder.bind(service="svc", environment="prod", job_id="1"):
            ...     snapshot = binder.serialize()
            >>> [ctx.job_id for ctx in snapshot.stack]
            ['1']

        """
        return ContextSnapshot(stack=self._stack())

    def deserialize(self, snapshot: ContextSnapshot) -> None:
        """Restore the stack held by ``snapshot``.

        Replaces the current :mod:`contextvars` stack, typically in a child
        process that received the snapshot from :meth:`serialize`, either as an
        object or as text parsed by
        :func:`lib_log_rich.adapters.context_snapshot.load_context_snapshot`.

        Args:
            snapshot: Stack to restore.

        Example:
            >>> binder = ContextBinder()
            >>> binder.deserialize(ContextSnapshot(stack=(LogContext(service="svc", environment="prod", job_id="1"),)))
            >>> binder.current().job_id
            '1'

        """
        self._install(snapshot.stack)

    def seed(self, context: LogContext) -> None:
        """Make ``context`` the only frame, also in scopes that have none bound yet.

        The runtime seeds its binder with a bootstrap frame so every event
        carries service, environment and host identity even outside ``bind()``.

        Args:
            context: The frame every scope starts from.

        Example:
            >>> binder = ContextBinder()
            >>> binder.seed(LogContext(service="svc", environment="prod", job_id="bootstrap"))
            >>> with binder.bind(request_id="r1") as ctx:
            ...     (ctx.job_id, ctx.request_id)
            ('bootstrap', 'r1')

        """
        self._install((context,))

    def _install(self, stack: tuple[LogContext, ...]) -> None:
        """Replace the stack, and the frame a fresh scope falls back to."""
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


CONTEXT_SNAPSHOT_VERSION = 1


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """A binder's context stack as a value, for handing to another process.

    Pass the object itself where it is pickled (``multiprocessing`` arguments);
    where only text travels (an environment variable, a pipe, a file), render it
    with :func:`lib_log_rich.adapters.context_snapshot.dump_context_snapshot` and
    parse the receiving end with ``load_context_snapshot``, which validates every
    field because that text arrives from outside the process.

    Attributes:
        stack: Frames from outermost to innermost.
        version: Format version of the JSON form.

    Example:
        >>> snapshot = ContextSnapshot(stack=(LogContext(service="svc", environment="prod", job_id="1"),))
        >>> [ctx.service for ctx in snapshot.stack]
        ['svc']
        >>> ContextSnapshot(version=2)
        Traceback (most recent call last):
        ...
        ValueError: Invalid context snapshot: version: unsupported version 2; expected 1

    """

    stack: tuple[LogContext, ...] = ()
    version: int = CONTEXT_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        """Refuse a version this release cannot read, and freeze the stack."""
        if self.version != CONTEXT_SNAPSHOT_VERSION:
            raise ValueError(f"Invalid context snapshot: version: unsupported version {self.version!r}; expected {CONTEXT_SNAPSHOT_VERSION}")
        object.__setattr__(self, "stack", tuple(self.stack))


__all__ = ["CONTEXT_SNAPSHOT_VERSION", "ContextBinder", "ContextSnapshot", "LogContext"]
