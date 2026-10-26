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
* :class:`ContextSnapshot` - the binder's stack as a value, plus the JSON form
  that carries it across a process boundary.
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
import json
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Mapping, Sequence

_T = TypeVar("_T")


def _new_extra_dict() -> dict[str, Any]:
    """Return a new mutable mapping for context extras."""
    return {}


def _pick(override: _T | None, current: _T) -> _T:
    """Return ``override`` unless it is ``None``, in which case keep ``current``."""
    return current if override is None else override


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
        pickles (``multiprocessing`` arguments) and renders as JSON through
        :meth:`ContextSnapshot.to_json`.

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
        object or as text parsed by :meth:`ContextSnapshot.from_json`.

        Args:
            snapshot: Stack to restore.

        Example:
            >>> binder = ContextBinder()
            >>> binder.deserialize(ContextSnapshot.from_json('{"version": 1, "stack": [{"service": "svc", "environment": "prod", "job_id": "1"}]}'))
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

_SNAPSHOT_KEYS = frozenset({"version", "stack"})
_CONTEXT_KEYS = frozenset(item.name for item in fields(LogContext))


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """A binder's context stack as a value, for handing to another process.

    Pass the object itself where it is pickled (``multiprocessing`` arguments);
    where only text travels (an environment variable, a pipe, a file), send
    :meth:`to_json` and parse the receiving end with :meth:`from_json`, which
    validates every field because that text arrives from outside the process.

    Attributes:
        stack: Frames from outermost to innermost.
        version: Format version of the JSON form.

    Example:
        >>> snapshot = ContextSnapshot(stack=(LogContext(service="svc", environment="prod", job_id="1"),))
        >>> ContextSnapshot.from_json(snapshot.to_json()) == snapshot
        True

    """

    stack: tuple[LogContext, ...] = ()
    version: int = CONTEXT_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        """Refuse a version this release cannot read, and freeze the stack."""
        if self.version != CONTEXT_SNAPSHOT_VERSION:
            raise ValueError(f"Invalid context snapshot: version: unsupported version {self.version!r}; expected {CONTEXT_SNAPSHOT_VERSION}")
        object.__setattr__(self, "stack", tuple(self.stack))

    def to_json(self) -> str:
        """Render the snapshot as JSON text that :meth:`from_json` reads back.

        Raises:
            TypeError: If a frame's ``extra`` holds a value JSON cannot encode.

        Example:
            >>> ContextSnapshot().to_json()
            '{"version": 1, "stack": []}'

        """
        frames = [ctx.to_dict(include_none=True) for ctx in self.stack]
        return json.dumps({"version": self.version, "stack": frames})

    @classmethod
    def from_json(cls, text: str) -> ContextSnapshot:
        """Parse JSON text produced by :meth:`to_json`, validating every field.

        Args:
            text: The JSON document.

        Returns:
            The snapshot it describes.

        Raises:
            ValueError: If ``text`` is not JSON or a field is missing, unknown
                or of the wrong type; the message names the field
                (``stack[0].process_id``).

        Example:
            >>> ContextSnapshot.from_json('{"version": 1, "stack": [{"service": "svc", "environment": "prod", "job_id": ""}]}')
            Traceback (most recent call last):
            ...
            ValueError: Invalid context snapshot: stack[0].job_id: must not be empty

        """
        try:
            document: object = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _snapshot_error("document", f"not valid JSON ({exc.msg} at position {exc.pos})") from exc
        root = _require_object(document, "document", _SNAPSHOT_KEYS)
        version = _require_int(root.get("version"), "version")
        stack = _require_list(root.get("stack"), "stack")
        return cls(stack=tuple(_context_from_json(entry, f"stack[{index}]") for index, entry in enumerate(stack)), version=version)


def _snapshot_error(location: str, reason: str) -> ValueError:
    """Build the refusal :meth:`ContextSnapshot.from_json` raises."""
    return ValueError(f"Invalid context snapshot: {location}: {reason}")


def _kind(value: object) -> str:
    """Name a JSON value's kind for a refusal, without echoing the value."""
    return "null" if value is None else type(value).__name__


def _require_object(value: object, location: str, allowed: frozenset[str]) -> dict[str, object]:
    """Return ``value`` as a JSON object whose keys are all in ``allowed``."""
    if not isinstance(value, dict):
        raise _snapshot_error(location, f"expected an object, got {_kind(value)}")
    mapping = cast("dict[str, object]", value)
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise _snapshot_error(f"{location}.{unknown[0]}", "unknown field")
    return mapping


def _require_list(value: object, location: str) -> list[object]:
    """Return ``value`` as a JSON array."""
    if not isinstance(value, list):
        raise _snapshot_error(location, f"expected an array, got {_kind(value)}")
    return cast("list[object]", value)


def _require_int(value: object, location: str) -> int:
    """Return ``value`` as an integer; JSON ``true``/``false`` are not integers."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _snapshot_error(location, f"expected an integer, got {_kind(value)}")
    return value


def _optional_int(value: object, location: str) -> int | None:
    """Return ``value`` as an integer or ``None``."""
    return None if value is None else _require_int(value, location)


def _require_text(value: object, location: str) -> str:
    """Return ``value`` as a string."""
    if not isinstance(value, str):
        raise _snapshot_error(location, f"expected a string, got {_kind(value)}")
    return value


def _required_text(value: object, location: str) -> str:
    """Return ``value`` as a string holding more than whitespace, as LogContext requires."""
    text = _require_text(value, location)
    if not text.strip():
        raise _snapshot_error(location, "must not be empty")
    return text


def _optional_text(value: object, location: str) -> str | None:
    """Return ``value`` as a string or ``None``."""
    return None if value is None else _require_text(value, location)


def _pid_chain(value: object, location: str) -> tuple[int, ...]:
    """Return a JSON array of integers (absent or ``null`` meaning empty) as a tuple."""
    if value is None:
        return ()
    entries = _require_list(value, location)
    return tuple(_require_int(pid, f"{location}[{index}]") for index, pid in enumerate(entries))


def _extra(value: object, location: str) -> dict[str, object]:
    """Return a JSON object (absent or ``null`` meaning empty) of caller metadata."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _snapshot_error(location, f"expected an object, got {_kind(value)}")
    return dict(cast("dict[str, object]", value))


def _read(frame: dict[str, object], location: str, key: str, parse: Callable[[object, str], _T]) -> _T:
    """Parse ``frame[key]`` (``None`` when absent), naming ``location.key`` on refusal."""
    return parse(frame.get(key), f"{location}.{key}")


def _context_from_json(value: object, location: str) -> LogContext:
    """Validate one frame of a snapshot document into a :class:`LogContext`."""
    frame = _require_object(value, location, _CONTEXT_KEYS)
    return LogContext(
        service=_read(frame, location, "service", _required_text),
        environment=_read(frame, location, "environment", _required_text),
        job_id=_read(frame, location, "job_id", _required_text),
        request_id=_read(frame, location, "request_id", _optional_text),
        user_id=_read(frame, location, "user_id", _optional_text),
        user_name=_read(frame, location, "user_name", _optional_text),
        hostname=_read(frame, location, "hostname", _optional_text),
        process_id=_read(frame, location, "process_id", _optional_int),
        process_id_chain=_read(frame, location, "process_id_chain", _pid_chain),
        trace_id=_read(frame, location, "trace_id", _optional_text),
        span_id=_read(frame, location, "span_id", _optional_text),
        extra=_read(frame, location, "extra", _extra),
    )


__all__ = ["CONTEXT_SNAPSHOT_VERSION", "ContextBinder", "ContextSnapshot", "LogContext"]
