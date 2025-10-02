"""Runtime façade that wires the clean-architecture logging backbone.

Purpose
-------
Expose a stable entry point (`init`, `bind`, `get`, `dump`, `shutdown`) that
host applications use instead of importing the inner layers directly. The
module translates the configuration inputs captured in
``docs/systemdesign/concept_architecture.md`` into a composed runtime built from
domain entities, application use cases, and adapters.

Contents
--------
* ``init`` – composition root for assembling the logging pipeline.
* ``get`` / ``bind`` – accessors for logger proxies and context binding.
* ``dump`` – bridge from the ring buffer to dump adapters.
* ``shutdown`` / ``shutdown_async`` – deterministic teardown paths.
* Legacy helpers (``hello_world``, ``i_should_fail``, ``summary_info``) retained
  for scaffolding and smoke tests.

System Role
-----------
Forms the outer shell mandated by the system design: high-level policy depends
only on abstractions; adapters and infrastructure are hidden behind this
interface so downstream services interact with a minimal, well-documented API.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional

from lib_log_rich.adapters import RegexScrubber, RichConsoleAdapter
from lib_log_rich.domain import DumpFormat, LogLevel
from lib_log_rich.domain.palettes import CONSOLE_STYLE_THEMES

from ._composition import LoggerProxy, build_runtime, coerce_level
from ._settings import DiagnosticHook, build_runtime_settings
from ._state import LoggingRuntime, clear_runtime, current_runtime, is_initialised, set_runtime
from . import _state as _state_module


class _RuntimeStateProxy:
    """Proxy exposing the underlying runtime for legacy callers."""

    def __getattr__(self, item: str) -> Any:
        """Delegate attribute access to the active runtime.

        Parameters
        ----------
        item:
            Attribute requested by the caller.

        Returns
        -------
        Any
            Value from the underlying :class:`LoggingRuntime` instance.

        Raises
        ------
        AttributeError
            When no runtime is initialised.

        Examples
        --------
        >>> _STATE.__bool__() in (True, False)
        True
        """
        try:
            state = _state_module.current_runtime()
        except RuntimeError as exc:
            raise AttributeError("Runtime not initialised") from exc
        return getattr(state, item)

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        """Return ``True`` when a runtime has been initialised.

        Examples
        --------
        >>> isinstance(bool(_STATE), bool)
        True
        """
        return _state_module.is_initialised()


_STATE = _RuntimeStateProxy()


@dataclass(frozen=True)
class RuntimeSnapshot:
    """Immutable view over the active logging runtime."""

    service: str
    environment: str
    console_level: LogLevel
    backend_level: LogLevel
    graylog_level: LogLevel
    queue_present: bool
    theme: str | None
    console_styles: Mapping[str, str] | None


def inspect_runtime() -> RuntimeSnapshot:
    """Return a read-only snapshot of the current runtime state."""

    runtime = current_runtime()
    styles = runtime.console_styles or None
    readonly_styles: Mapping[str, str] | None
    if styles:
        readonly_styles = MappingProxyType(dict(styles))
    else:
        readonly_styles = None
    return RuntimeSnapshot(
        service=runtime.service,
        environment=runtime.environment,
        console_level=runtime.console_level,
        backend_level=runtime.backend_level,
        graylog_level=runtime.graylog_level,
        queue_present=runtime.queue is not None,
        theme=runtime.theme,
        console_styles=readonly_styles,
    )


__all__ = [
    "CONSOLE_STYLE_THEMES",
    "LoggerProxy",
    "RegexScrubber",
    "RichConsoleAdapter",
    "bind",
    "dump",
    "get",
    "inspect_runtime",
    "hello_world",
    "i_should_fail",
    "init",
    "is_initialised",
    "RuntimeSnapshot",
    "shutdown",
    "shutdown_async",
    "summary_info",
]


def init(
    *,
    service: str,
    environment: str,
    console_level: str | LogLevel = LogLevel.INFO,
    backend_level: str | LogLevel = LogLevel.WARNING,
    graylog_endpoint: tuple[str, int] | None = None,
    graylog_level: str | LogLevel = LogLevel.WARNING,
    enable_ring_buffer: bool = True,
    ring_buffer_size: int = 25_000,
    enable_journald: bool = False,
    enable_eventlog: bool = False,
    enable_graylog: bool = False,
    graylog_protocol: str = "tcp",
    graylog_tls: bool = False,
    queue_enabled: bool = True,
    queue_maxsize: int = 2048,
    queue_full_policy: str = "block",
    queue_put_timeout: float | None = None,
    force_color: bool = False,
    no_color: bool = False,
    console_styles: Mapping[str, str] | None = None,
    console_theme: str | None = None,
    console_format_preset: str | None = None,
    console_format_template: str | None = None,
    scrub_patterns: Optional[dict[str, str]] = None,
    dump_format_preset: str | None = None,
    dump_format_template: str | None = None,
    rate_limit: Optional[tuple[int, float]] = None,
    diagnostic_hook: DiagnosticHook = None,
) -> None:
    """Compose the logging runtime according to configuration inputs.

    Why
    ---
    Hosts call ``init`` once during startup to assemble the adapters and use
    cases defined by the system design. Centralising the composition here keeps
    downstream services framework-agnostic and enforces the dependency rule.

    What
    ----
    Resolves configuration (function arguments + environment overrides),
    builds the ring buffer, queue, console/structured adapters, optional
    Graylog sink, and rate limiter, then installs the resulting runtime as the
    active singleton.

    Inputs
    ------
    service, environment:
        Required identifiers injected into every log event for routing and
        multi-tenant support.
    console_level, backend_level, graylog_level:
        Minimum severities for console, structured backends, and Graylog fan
        out. Strings are coerced via :func:`LogLevel.from_name`.
    enable_* flags and ``graylog_*`` options:
        Toggle adapters according to deployment needs (journald, Windows Event
        Log, Graylog transport, TLS).
    queue_enabled:
        Runs the fan-out pipeline on a worker thread when ``True`` to support
        multi-process producers.
    queue_maxsize, queue_full_policy, queue_put_timeout:
        Configure the queue capacity and behaviour when the queue is full.
    console_* / dump_* / scrub_patterns / rate_limit / diagnostic_hook:
        Optional knobs for formatting, redaction, throttling, and diagnostics as
        described in ``docs/systemdesign/module_reference.md``.

    Outputs
    -------
    None. The function installs global state accessible via :func:`get`,
    :func:`bind`, and :func:`dump`.

    Side Effects
    ------------
    Raises :class:`RuntimeError` if called while a runtime is already active.
    Mutates process-wide logging state by registering the composed runtime.
    Spawns a queue worker thread when ``queue_enabled`` is ``True``.
    """

    if is_initialised():
        raise RuntimeError(
            "lib_log_rich.init() cannot be called twice without shutdown(); call lib_log_rich.shutdown() first",
        )

    settings = build_runtime_settings(
        service=service,
        environment=environment,
        console_level=console_level,
        backend_level=backend_level,
        graylog_endpoint=graylog_endpoint,
        graylog_level=graylog_level,
        enable_ring_buffer=enable_ring_buffer,
        ring_buffer_size=ring_buffer_size,
        enable_journald=enable_journald,
        enable_eventlog=enable_eventlog,
        enable_graylog=enable_graylog,
        graylog_protocol=graylog_protocol,
        graylog_tls=graylog_tls,
        queue_enabled=queue_enabled,
        queue_maxsize=queue_maxsize,
        queue_full_policy=queue_full_policy,
        queue_put_timeout=queue_put_timeout,
        force_color=force_color,
        no_color=no_color,
        console_styles=console_styles,
        console_theme=console_theme,
        console_format_preset=console_format_preset,
        console_format_template=console_format_template,
        scrub_patterns=scrub_patterns,
        dump_format_preset=dump_format_preset,
        dump_format_template=dump_format_template,
        rate_limit=rate_limit,
        diagnostic_hook=diagnostic_hook,
    )
    runtime = build_runtime(settings)
    set_runtime(runtime)


def get(name: str) -> LoggerProxy:
    """Return a logger proxy bound to the configured runtime.

    Why
    ---
    Keeps callers decoupled from the application layer: they receive a
    lightweight façade wired to the runtime’s process use case.

    Inputs
    ------
    name:
        Logger name (typically ``package.component``) used for routing,
        diagnostics, and rate limiter keys.

    Outputs
    -------
    :class:`LoggerProxy`
        Callable with ``debug``/``info``/... methods that dispatch to the
        composed pipeline.

    Side Effects
    ------------
    Raises :class:`RuntimeError` when ``init`` has not been called.
    """

    runtime = current_runtime()
    return LoggerProxy(name, runtime.process)


@contextmanager
def bind(**fields: Any):
    """Bind structured metadata for the current execution scope.

    Why
    ---
    The system design requires every log event to carry service/environment
    identifiers plus contextual fields (job IDs, request IDs, etc.). ``bind``
    ensures those invariants hold without leaking :class:`ContextBinder` from
    the domain layer.

    Inputs
    ------
    **fields:
        Context attributes to merge into the active frame. The first bind in a
        call stack must provide the mandatory ``service``, ``environment``, and
        ``job_id`` keys; nested binds can add optional metadata.

    Outputs
    -------
    Context manager yielding the new :class:`LogContext` so callers can inspect
    the effective values.

    Side Effects
    ------------
    Mutates the runtime’s :class:`ContextBinder` stack for the lifetime of the
    ``with`` block. Raises :class:`RuntimeError` if called before ``init``.
    """

    runtime = current_runtime()
    with runtime.binder.bind(**fields) as ctx:
        yield ctx


def dump(
    *,
    dump_format: str | DumpFormat = "text",
    path: str | Path | None = None,
    level: str | LogLevel | None = None,
    console_format_preset: str | None = None,
    console_format_template: str | None = None,
    theme: str | None = None,
    console_styles: Mapping[LogLevel | str, str] | None = None,
    color: bool = False,
) -> str:
    """Render the in-memory ring buffer into a textual artefact.

    Why
    ---
    Operators need ad-hoc introspection without relying on external sinks. This
    helper turns the retained events into the chosen dump format.

    Inputs
    ------
    dump_format:
        Format identifier or :class:`DumpFormat` enum member (text/json/html).
    path:
        Optional filesystem target; when ``None`` the payload is returned only.
    level:
        Minimum severity filter; defaults to the runtime’s thresholds.
    console_* / theme / console_styles / color:
        Overrides for formatting and colour inheritance, mirroring CLI
        switches documented in ``LOGDUMP.md``.

    Outputs
    -------
    str
        Rendered payload (text, JSON string, HTML) regardless of whether it was
        persisted to disk.

    Side Effects
    ------------
    Reads the ring buffer snapshot and flushes it after successful rendering.
    Raises :class:`RuntimeError` when the runtime is not initialised.
    """

    runtime = current_runtime()
    fmt = dump_format if isinstance(dump_format, DumpFormat) else DumpFormat.from_name(dump_format)
    target = Path(path) if path is not None else None
    min_level = coerce_level(level) if level is not None else None
    template = console_format_template
    resolved_theme = theme if theme is not None else runtime.theme
    resolved_styles = console_styles if console_styles is not None else runtime.console_styles
    return runtime.capture_dump(
        dump_format=fmt,
        path=target,
        min_level=min_level,
        format_preset=console_format_preset,
        format_template=template,
        text_template=template,
        theme=resolved_theme,
        console_styles=resolved_styles,
        colorize=color,
    )


def shutdown() -> None:
    """Flush adapters, stop the queue, and clear runtime state synchronously.

    Why
    ---
    Ensures graceful termination for CLI tools and hosts that are not running
    inside an event loop.

    Side Effects
    ------------
    Validates no asyncio loop is active, runs :func:`shutdown_async` in a fresh
    loop, and clears global runtime state. Raises :class:`RuntimeError` when
    invoked inside a running loop to steer callers to ``shutdown_async``.
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    else:
        if loop.is_running():
            raise RuntimeError(
                "lib_log_rich.shutdown() cannot run inside an active event loop; await lib_log_rich.shutdown_async() instead",
            )
    asyncio.run(shutdown_async())


async def shutdown_async() -> None:
    """Flush adapters, stop the queue, and clear runtime state asynchronously.

    Why
    ---
    Async applications need a non-blocking teardown path that awaits adapter
    cleanup (Graylog flush, queue drain).

    Side Effects
    ------------
    Stops the queue worker, awaits adapter shutdown, and clears the runtime
    singleton.
    """

    runtime = current_runtime()
    await _perform_shutdown(runtime)
    clear_runtime()


async def _perform_shutdown(runtime: LoggingRuntime) -> None:
    """Coordinate shutdown hooks across adapters and use cases.

    Parameters
    ----------
    runtime:
        Active :class:`LoggingRuntime` instance.

    Side Effects
    ------------
    Stops the queue worker and awaits adapter-specific shutdown coroutines when
    provided.

    Examples
    --------
    >>> import asyncio
    >>> class DummyRuntime:
    ...     def __init__(self) -> None:
    ...         self.queue = None
    ...         self.calls = []
    ...     def shutdown_async(self):
    ...         self.calls.append('shutdown')
    ...         return None
    >>> runtime = DummyRuntime()
    >>> asyncio.run(_perform_shutdown(runtime))
    >>> runtime.calls
    ['shutdown']
    """
    if runtime.queue is not None:
        runtime.queue.stop()
    result = runtime.shutdown_async()
    if inspect.isawaitable(result):
        await result


def hello_world() -> None:
    """Print the canonical smoke-test message used in docs and doctests.

    Why
    ---
    Provides a deterministic success path for quick verifications and examples
    without triggering the full logging pipeline.

    Side Effects
    ------------
    Writes ``"Hello World"`` followed by a newline to stdout.
    """

    print("Hello World")


def i_should_fail() -> None:
    """Raise ``RuntimeError`` to exercise failure handling in examples/tests.

    Why
    ---
    Keeps the scaffold’s failure path available so tutorials and automated
    tests can assert error handling without mocking internals.

    Side Effects
    ------------
    Always raises :class:`RuntimeError` with the canonical message.
    """

    raise RuntimeError("I should fail")


def summary_info() -> str:
    """Return the metadata banner used by the CLI entry point and docs.

    Why
    ---
    Centralises the package metadata banner so CLI commands, doctests, and
    README excerpts stay consistent with ``pyproject.toml``.

    Outputs
    -------
    str
        Multi-line banner ending with a newline.
    """

    from .. import __init__conf__

    lines: list[str] = []

    def _capture(text: str) -> None:
        """Collect emitted metadata lines for later concatenation.

        Parameters
        ----------
        text:
            Line emitted by :func:`lib_log_rich.__init__conf__.print_info`.
        """
        lines.append(text)

    __init__conf__.print_info(writer=_capture)
    return "".join(lines)
