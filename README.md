# lib_log_rich

<!-- Badges -->
[![CI](https://github.com/bitranox/lib_log_rich/actions/workflows/ci.yml/badge.svg)](https://github.com/bitranox/lib_log_rich/actions/workflows/ci.yml)
[![CodeQL](https://github.com/bitranox/lib_log_rich/actions/workflows/codeql.yml/badge.svg)](https://github.com/bitranox/lib_log_rich/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Open in Codespaces](https://img.shields.io/badge/Codespaces-Open-blue?logo=github&logoColor=white&style=flat-square)](https://codespaces.new/bitranox/lib_log_rich?quickstart=1)
[![PyPI](https://img.shields.io/pypi/v/lib_log_rich.svg)](https://pypi.org/project/lib_log_rich/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/lib_log_rich.svg)](https://pypi.org/project/lib_log_rich/)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-46A3FF?logo=ruff&labelColor=000)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/bitranox/lib_log_rich/graph/badge.svg?token=UFBaUDIgRk)](https://codecov.io/gh/bitranox/lib_log_rich)
[![Maintainability](https://qlty.sh/badges/041ba2c1-37d6-40bb-85a0-ec5a8a0aca0c/maintainability.svg)](https://qlty.sh/gh/bitranox/projects/lib_log_rich)
[![Known Vulnerabilities](https://snyk.io/test/github/bitranox/lib_log_rich/badge.svg)](https://snyk.io/test/github/bitranox/lib_log_rich)

 
* **Enjoy vibrant Rich-style colored console output with clean formatting.**
* **Tailor colored (or monochrome) logs with flexible templates and themes, while your OS takes care of persistence.**
* **Forget about manual logfile rotation.**
* **Export logs as JSON, HTML or plain text whenever you need.**
* **Send events to journald, Windows Event Log, or Graylog with ease.**
* **Get optional OpenTelemetry integration for deeper observability.**
* **Configure logging per app/user/host via configfile or environment**


```python
import logging
import lib_log_rich
from lib_log_rich.runtime import attach_std_logging

lib_log_rich.init(lib_log_rich.RuntimeConfig(service="my-app", environment="dev"))
attach_std_logging()

logging.info("Ready to go!")            # existing logging.* calls to the root logger just work

logger = logging.getLogger(__name__)    # log to any custom logger
logger.info("Also works fine!")         # logging in third party libraries just work

lib_log_rich.shutdown()                 # shutdown to make sure all records are written to backends like graylog
```

---

## Table of Contents

- [Integration Decision Guide](#integration-decision-guide) – Quick guidance on choosing LoggerProxy vs stdlib handler.
- [Getting Started](#getting-started)
  - [Overview](#overview) – High-level introduction, key features, and quick-start pointers.
  - [Installation](#installation) – Supported Python versions, installation commands, editable mode, and journald prerequisites.
    - [Journald adapter dependency](#journald-adapter-dependency) – Extra host prerequisites for systemd-journald emission.
  - [Logging with LoggerProxy](#logging-with-loggerproxy) – Minimal example covering configuration, logging, shutdown, and submodule patterns.
    - [How to use in submodules](#how-to-use-in-submodules) – Dependency-injection friendly usage.
    - [Domain helpers](#domain-helpers) – Reusable value objects and helper utilities.
    - [Custom pipeline wiring](#custom-pipeline-wiring) – Extending the runtime composition root.
    - [Context vs. per-event metadata](#context-vs-per-event-metadata) – Binding semantics and best practices.
    - [exceptions logging](#exceptions-logging) – Capturing tracebacks and stack information.
    - [Opt-in `.env` loading](#opt-in-env-loading) – Environment discovery and precedence.
- [Integration Options](#integration-options)
  - [Stdlib compatibility & deliberate differences](#stdlib-compatibility--deliberate-differences) – Behavioural notes compared to `logging`.
  - [Integrating stdlib logging](#integrating-stdlib-logging) – Step-by-step handler wiring and dump inspection.
- [Runtime Reference](#runtime-reference)
  - [Public API](#public-api) – Table summarising exported helpers (`init`, `getLogger`, `dump`, etc.).
  - [Runtime configuration](#runtime-configuration) – Runtime knobs, environment flags, and presets.
  - [Payload Limits](#payload-limits) – Message/extra/context sizing and truncation diagnostics.
  - [Environment-only overrides](#environment-only-overrides) – Override runtime settings without code changes.
- [Operations & Observability](#operations--observability)
  - [Log dump](#log-dump) – Dump formats, filtering options, and customisation templates.
  - [Inspecting severity and drop metrics](#inspecting-severity-and-drop-metrics) – Using `max_level_seen`, `severity_snapshot`, and stress-test counters.
  - [Terminal compatibility](#terminal-compatibility) – Behaviour across terminals and colour systems.
- [Console & Terminal Experience](#console--terminal-experience)
  - [Customising per-level colours](#customising-per-level-colours) – Theme overrides and presets.
- [CLI and Automation](#cli-and-automation)
  - [CLI entry point](#cli-entry-point) – Usage of `python -m lib_log_rich` and available subcommands.
    - [Streaming console output to other consumers](#streaming-console-output-to-other-consumers) – Queue-backed adapters for GUIs/async consumers.
    - [Quick smoke-test helpers ship with the package:](#quick-smoke-test-helpers-ship-with-the-package) – Self-test commands and diagnostics.
- [Further documentation](#further-documentation) – Links to CLI, dump, style, and architecture guides.
- [Development](#development) – Contribution guidelines, testing strategy, and project structure.
- [License](#license) – Project licensing information.

---

## Integration Decision Guide

- Prefer **`lib_log_rich.getLogger(...)` and `LoggerProxy`** when you own the emitting code. You gain the full structured pipeline: Rich console output, journald/Windows Event Log/Graylog fan-out, payload sanitisation, context binding, queue-based delivery, and diagnostic return values without touching stdlib internals.
- Attach the **`StdlibLoggingHandler`** via `attach_std_logging()` when you must support existing `logging` hierarchies (framework defaults, third-party libraries, legacy modules). The handler normalises `LogRecord` objects into the same runtime, preserves `extra` payloads, copies call-site metadata (`pathname`, `lineno`, `funcName`), honours `exc_info`/`stack_info`, and skips recursion for events originating inside `lib_log_rich`.
- Mixing both approaches is common: initialise the runtime once, use `LoggerProxy` in new code, and bridge whatever still relies on the standard library. All routes share the same ring buffer, payload limits, diagnostics, CLI tooling, and dump facilities.

---

## Getting Started

<a id="section-overview"></a>
### Overview

Rich-powered logging backbone with contextual metadata, multi-target fan-out (console, journald, Windows Event Log, Graylog), ring-buffer dumps, and 
queue-based decoupling for multi-threaded workloads.  
Rich renders multi-colour output tuned to each terminal, while adapters and dump exporters support configurable formats and templates.  
Each runtime captures the active user, short hostname, process id, and PID chain automatically, so every sink receives consistent system identity fields.  
The public API stays intentionally small: initialise once, bind context, emit logs (with per-event `extra` payloads), dump history in text/JSON/HTML, and shut down cleanly.

> **Python requirement:** lib_log_rich targets Python 3.10 and newer.
> Core dependencies: `lib_cli_exit_tools>=2.1.0`, `pydantic>=2.12.0`, `rich>=14.2.0`, `rich-click>=1.9.3`, and `python-dotenv>=1.1.1` (as mirrored in `pyproject.toml`).

- colored terminal logs via rich, with UTC or local timestamps
- supports journald
- supports Windows Event Logs
- supports Graylog via Gelf (and gRPC after adding Open Telemetry Support)
- supports quick log-dump with filtering from the ringbuffer without leaving the application
- runtime configuration validated via Pydantic models, yielding structured errors and JSON schemas
- per-event payload guards (4KB messages, 8KB extras, depth limits) configurable via `payload_limits`
- **independent log levels**: `console_level`, `backend_level`, and `graylog_level` are completely independent — each gates events to its respective adapter (console, journald/EventLog, Graylog) without affecting the others. Use `get_minimum_log_level()` to find the lowest threshold for stdlib integration.
- opt-in `.env` loading (same precedence for CLI and programmatic use)
- Open Telemetry Support on user (Your) request - not implemented yet (because I do not need it myself). If You need it, let me know.
- optional `diagnostic_hook` callback that observes the runtime without modifying it. The hook reports queue activity (drops, worker failures, drop-handler errors), rate limiting, and payload clamps so you can surface metrics, alerts, or dashboards while keeping the logging pipeline decoupled from any monitoring stack.
- queue-backed console adapters (ANSI or HTML) — `QueueConsoleAdapter` for threads and `AsyncQueueConsoleAdapter` for asyncio — exposed via `console_adapter_factory`, handy for Textual panes, SSE/WebSocket streams, or tests.
- [EXAMPLES.md](EXAMPLES.md) — runnable snippets from Hello World to multi-backend wiring.

---


### Installation

For a quick start from PyPI:

```bash
pip install lib_log_rich
```

Detailed installation options (venv, pipx, uv, Poetry/PDM, and Git installs) live in [INSTALL.md](INSTALL.md). If you plan to enable systemd journald logging, follow the extra host prerequisites in [INSTALL_JOURNAL.md](INSTALL_JOURNAL.md).

#### Journald adapter dependency

The Journald adapter requires the `systemd-python` bindings. Install via the optional extra:

```bash
# Requires libsystemd-dev system package to build
sudo apt-get install libsystemd-dev   # Debian/Ubuntu
pip install lib_log_rich[journald]
```

Alternatively, use your distro's pre-built package (`sudo apt-get install python3-systemd` on Debian/Ubuntu) which avoids the build step.

When you initialise the runtime with `enable_journald=True` without the bindings, a `RuntimeError` is raised immediately so you can fix the dependency before emitting events. Once installed the adapter can emit to journald regardless of queue settings. See [INSTALL_JOURNAL.md](INSTALL_JOURNAL.md) for a deeper walkthrough covering Linux service managers and verification steps.

---

<a id="section-usage"></a>
### Logging with LoggerProxy

```python
import lib_log_rich as log

config = log.RuntimeConfig(
    service="my-service",
    environment="dev",
    queue_enabled=False,
    enable_graylog=False,
)
log.init(config)

with log.bind(job_id="startup", request_id="req-001"):
    logger = log.getLogger("app.http")
    logger.info("ready", extra={"port": 8080})

# Inspect the recent history (text/json/html_table/html_txt)
print(log.dump(dump_format="json"))

log.shutdown()
```

---

#### How to use in submodules

Initialise the runtime once near your process entrypoint, then let every
module fetch its own `LoggerProxy` via `lib_log_rich.getLogger`. The runtime keeps the
Rich-backed pipeline global, so submodules only need the name they wish to log
under.

```python
# entrypoint.py
import lib_log_rich as log

log.init(log.RuntimeConfig(service="billing", environment="prod"))

from billing import payments  # import after init so the runtime exists

payments.charge_order("ord-42")
```

```python
# billing/payments.py
from __future__ import annotations

from collections.abc import Callable
from lib_log_rich import bind, getLogger
from lib_log_rich.runtime import LoggerProxy

LoggerFactory = Callable[[str], LoggerProxy]


class PaymentProcessor:
    def __init__(self, get_logger: LoggerFactory | None = None) -> None:
        self._get_logger = get_logger or getLogger

    def charge_order(self, order_id: str) -> None:
        logger = self._get_logger(__name__)
        with bind(order_id=order_id):
            logger.info("Submitting charge", extra={"provider": "stripe"})


# Composition root
processor = PaymentProcessor()
processor.charge_order("ord-123")
```

- Key points:
  - No module-level mutable state; PaymentProcessor takes a LoggerFactory, so tests can supply a stub while production code uses lib_log_rich.getLogger.
  - The binding is scoped inside the method, which is explicit and test-friendly.
  - You can register a singleton instance if desired (processor = PaymentProcessor()), yet the module stays stateless and aligns with the repo’s dependency-injection guidelines.

If you just want a module-level logger but can not guarantee `lib_log_rich.init(...)` ran before the module is imported, add a tiny helper that initialises on demand once and then hands back the cached proxy. The helper keeps the module stateless and makes reuse in notebooks or ad-hoc scripts painless while still respecting the single-runtime rule.

```python
# billing/payments.py
from __future__ import annotations

from lib_log_rich import RuntimeConfig, bind, getLogger, init
from lib_log_rich.runtime import LoggerProxy, is_initialised


def ensure_logging() -> LoggerProxy:
    if not is_initialised():
        try:
            init(RuntimeConfig(service="billing", environment="prod"))
        except RuntimeError as exc:
            # Another thread/process section won the race; re-check before propagating.
            if not is_initialised():
                raise
    return getLogger(__name__)


logger: LoggerProxy = ensure_logging()


def charge_order(order_id: str) -> None:
    with bind(order_id=order_id):
        logger.info("Submitting charge", extra={"provider": "stripe"})
```

If you are certain `lib_log_rich.init(...)` already executed (for example in your CLI entrypoint), you can replace `ensure_logging()` with a direct `getLogger(__name__)` assignment and keep the rest identical.

---

#### Domain helpers

The domain package (`lib_log_rich.domain`) exposes reusable value objects so you can keep adapters and feature modules decoupled from implementation modules. A few shortcuts you might want immediately:

```python
import logging
from datetime import datetime, timezone

from lib_log_rich.domain import DumpFormat, LogContext, LogEvent, LogLevel, build_dump_filter

# Translate stdlib levels into the richer domain enum (icons, display metadata, etc.).
domain_level = LogLevel.from_python_level(logging.WARNING)

# Convert back when bridging to the stdlib logging module.
python_level = domain_level.to_python_level()

# Parse dump format values coming from CLI flags or environment variables.
dump_format = DumpFormat.from_name("json")

# Build reusable filters and exercise them against recorded events.
filters = build_dump_filter(context={"service": "billing"}, extra={"tenant": "acme"})
event = LogEvent(
    event_id="evt-1",
    timestamp=datetime.now(tz=timezone.utc),
    logger_name="billing.worker",
    level=LogLevel.INFO,
    message="ready",
    context=LogContext(service="billing", environment="prod", extra={"tenant": "acme"}),
    extra={"tenant": "acme", "order_id": "ORD-7"},
)
should_emit = filters.matches(event)
```

- `LogLevel` keeps conversions idempotent (`from_name`, `from_python_level`, `to_python_level`), so threading a standard `logging.LogRecord` level through Rich adapters only needs a single call.
- `DumpFormat.from_name(...)` parses human-friendly inputs (`"json"`, `"html_table"`, etc.) and keeps the call site self-documenting.
- `build_dump_filter(...)` returns a `DumpFilter` you can reuse in unit tests, notebook exploration, or dump pipelines by invoking `matches(...)` or handing its field tuples to the runtime façade.
- `LoggerProxy.log(level, msg, *args, exc_info=None, stack_info=None, stacklevel=1, extra=None)` mirrors the stdlib `logging.Logger` signature while still normalising enum/string/integer levels. Messages are formatted lazily inside the process pipeline, `exc_info` can be `True`, an exception instance, or a full tuple, and optional `stack_info` strings are threaded through to every adapter. The `stacklevel` keyword is accepted for API parity and currently ignored.
- `LoggerProxy.exception(msg, *args, exc_info=True, stack_info=None, stacklevel=1, extra=None)` matches `logging.Logger.exception`: it logs at `LogLevel.ERROR`, defaults `exc_info` to `True` (capturing the active exception), and otherwise delegates to the same structured pipeline as `.error(...)`.
- `LoggerProxy.setLevel(level)` updates the proxy's own threshold. Events below that level are discarded before entering the processing pipeline, so a message must pass the proxy level *and* each handler's level (console/backend/Graylog) to be emitted.

#### Custom pipeline wiring

Need to instrument specific collaborators (for example, injecting fakes in a benchmark or swapping adapters without reinitialising the runtime)? Assemble the process pipeline directly with the public dependency bundle:

```python
from datetime import datetime, timezone
from rich.console import Console

from lib_log_rich import application
from lib_log_rich.adapters import RegexScrubber, RichConsoleAdapter, SlidingWindowRateLimiter
from lib_log_rich.application import ProcessPipelineDependencies
from lib_log_rich.domain import ContextBinder, LogLevel, RingBuffer, SeverityMonitor
from lib_log_rich.domain.identity import SystemIdentity
from lib_log_rich.runtime import PayloadLimits


class StubClock:
    def now(self) -> datetime:
        return datetime.now(tz=timezone.utc)


class StubIdProvider:
    def __call__(self) -> str:
        return "evt-stub"


class StubIdentity:
    def resolve_identity(self) -> SystemIdentity:
        return SystemIdentity(user_name="demo", hostname="example", process_id=1234)


binder = ContextBinder()
dependencies = ProcessPipelineDependencies(
    context_binder=binder,
    ring_buffer=RingBuffer(max_events=64),
    severity_monitor=SeverityMonitor(),
    console=RichConsoleAdapter(console=Console(record=True), no_color=True),
    console_level=LogLevel.INFO,
    structured_backends=(),
    backend_level=LogLevel.INFO,
    graylog=None,
    graylog_level=LogLevel.ERROR,
    scrubber=RegexScrubber(patterns={}),
    rate_limiter=SlidingWindowRateLimiter(max_events=10, interval=1.0),
    clock=StubClock(),
    id_provider=StubIdProvider(),
    limits=PayloadLimits(),
    identity=StubIdentity(),
)

process = application.create_process_log_event(dependencies)
with binder.bind(service="custom", environment="demo", job_id="example"):
    payload = process(
        logger_name="example.pipeline",
        level=LogLevel.INFO,
        message="hello",
        extra={"scope": "demo"},
    )

assert payload["ok"] is True

# The stub classes above satisfy the relevant ports (`ClockPort`, `IdProvider`,
# `SystemIdentityPort`) so you can compose the pipeline without touching
# runtime internals. In production you would rely on the factories exported via
# ``lib_log_rich.application`` instead.
```

---

#### Context vs. per-event metadata

`lib_log_rich.bind(...)` establishes the *context* for subsequent log calls: service, environment, job/request identifiers, trace/span IDs, user, hostname, PID, and optional `LogContext.extra`. The `extra` argument on `bind` is a stable mapping you want attached to every event in that scope (for example, deployment labels or tenant metadata). Every event emitted inside the bound scope inherits the entire context automatically.

The `extra=` argument on `logger.debug/info/...` supplements a single event with ad-hoc details (order IDs, feature flags, timing data). The runtime merges the per-event `extra` with the bound context to form the structured payload that adapters see.

Payload limits apply to both buckets: context extras are capped at 20 keys/256 characters, while per-event extras default to 25 keys/512 characters with depth and aggregate guards. Oversized values are truncated (with a `…[truncated]` suffix) and the optional diagnostic hook receives events such as `extra_keys_dropped`, `extra_value_truncated_depth_collapsed`, or `context_extra_keys_dropped` when clamping occurs. Nested mappings deeper than `extra_max_depth` are collapsed into JSON strings so adapters (and downstream storage) are spared from unbounded recursion.

For example::

    import lib_log_rich as log

    # Context-wide extras travel with every event in the scope
    with log.bind(
        service="billing",
        environment="prod",
        job_id="invoice-processor",
        extra={"deployment": "blue", "team": "finops"},
    ):
        logger = log.getLogger("billing.worker")
        # Per-event extras describe this specific message
        logger.info(
            "processed invoice",
            extra={"invoice_id": "INV-42", "duration_ms": 183},
        )

    # The emitted event includes:
    #   context.extra -> {"deployment": "blue", "team": "finops"}
    #   event.extra   -> {"invoice_id": "INV-42", "duration_ms": 183}

---

#### Exceptions logging 

When logging exceptions, add the formatted traceback to `extra["exc_info"]`.
The runtime keeps only the top/bottom `stacktrace_max_frames` frames (default 10)::

    import traceback

    try:
        raise RuntimeError("upstream failed")
    except RuntimeError:
        logger.error(
            "job crashed",
            extra={"exc_info": traceback.format_exc()},
        )

Oversized traces are collapsed with `... truncated N frame(s) ...`, and the diagnostic hook
receives `exc_info_truncated`.

---

#### Opt-in `.env` loading

`lib_log_rich` has always honoured real environment variables over function arguments (`LOG_SERVICE`, `LOG_CONSOLE_LEVEL`, and friends). The `.env` helpers let you keep that precedence while sourcing defaults from a project-local file:

```python
import lib_log_rich as log
import lib_log_rich.config as log_config

log_config.enable_dotenv()  # walk upwards from cwd, load the first .env found
config = log.RuntimeConfig(service="svc", environment="dev", queue_enabled=False)
log.init(config)
...
log.shutdown()
```

Key points:

- `.env` loading is explicit – nothing is read unless you call `enable_dotenv()` (or `load_dotenv()`).
- Precedence stays intact: CLI flag ➝ real `os.environ` ➝ discovered `.env` ➝ defaults.
- Search uses `python-dotenv.find_dotenv(usecwd=True)` and stops once `.env` appears or the filesystem root is reached.
- Pass `dotenv_override=True` when you intentionally want `.env` values to win over real environment variables.

See [DOTENV.md](DOTENV.md) for more detail, examples, and CLI usage.

---

## Integration Options

### Stdlib compatibility & deliberate differences

`LoggerProxy` implements the core `logging.Logger` surface so existing call sites rarely need adjustment. Supported helpers are:

- `.debug(...)`, `.info(...)`, `.warning(...)`, `.error(...)`, `.critical(...)`
- `.exception(...)` (defaults `exc_info=True` just like the stdlib)
- `.log(level, ...)`
- `.setLevel(level)`

Key differences from the stdlib logging framework:

- Calls return structured dictionaries describing emission outcomes instead of `None`.
- No hierarchical logger tree: we do not expose the root logger, `getChild()`, propagation, or handler management. Each `getLogger(name)` call yields an independent proxy whose name is metadata only.
- Aliases such as `.warn(...)`, `.fatal(...)`, `isEnabledFor(...)`, `addHandler(...)`, and handler lists are intentionally unsupported.
- `stacklevel` is accepted but currently ignored. Stack trace capture is explicit: provide `exc_info` or `stack_info` to record traces, and the runtime stores sanitised text in the event payload rather than rendering the default stdlib traceback formatting.
- Console output defaults to stderr (matching the standard library logger), but you can steer it via `RuntimeConfig.console_stream` to `"stdout"`, `"stderr"`, `"both"`, `"custom"` (supply `console_stream_target`), or `"none"` to suppress console output entirely. You can also pass a no-op `console_adapter_factory` for bespoke mute behaviour. Without a console sink nothing touches stdio; stack traces continue to flow only through the configured backends.

When migrating code that relies on root/sub-logger behaviour or handler mutation, refactor to request proxies explicitly via `lib_log_rich.getLogger(name)` and configure sinks through `RuntimeConfig` instead of the stdlib globals.

### Integrating stdlib logging

Existing projects often expose module-level loggers via `logging.getLogger()`. The runtime now ships with a bridge so those callers can continue to emit without refactoring.

```python
import logging

from lib_log_rich.runtime import RuntimeConfig, attach_std_logging, init

init(RuntimeConfig(service="legacy-app", environment="prod", queue_enabled=False, enable_graylog=False))
attach_std_logging()  # logger_level defaults to get_minimum_log_level()

logging.getLogger(__name__).info("legacy path", extra={"tenant": 42})

if __name__ == "__main__":
    from lib_log_rich.runtime import dump

    print(dump())
```

- `attach_std_logging(...)` registers a single `StdlibLoggingHandler` on the chosen logger (root by default). By default it sets the logger level to `get_minimum_log_level()` so events aren't pre-filtered before reaching lib_log_rich, and sets `propagate=False` to prevent duplicate emission. Pass `logger_level=None` to leave the level unchanged.
- Every `logging.LogRecord` is translated into the runtime pipeline: message/arguments flow through the same sanitiser as `LoggerProxy`, `extra` metadata travels unchanged, and `exc_info`/`stack_info` remain available to adapters.
- Location metadata (`pathname`, `lineno`, `funcName`, plus any custom `extra` fields) is copied into the event so dumps, Graylog, and Rich console output can display the original call site.
- Records originating from `lib_log_rich` are ignored automatically to avoid recursion; set `extra={"lib_log_rich_skip": True}` if you need to suppress specific third-party records.

#### Understanding `get_minimum_log_level()`

`attach_std_logging()` automatically sets the logger level to `get_minimum_log_level()`, which returns the lowest (most permissive) threshold among active adapters. This ensures stdlib doesn't pre-filter events before they reach lib_log_rich.

```python
import logging
import lib_log_rich as log

# Initialize with independent levels for different adapters
config = log.RuntimeConfig(
    service="my-service",
    environment="prod",
    console_level="INFO",      # Console shows INFO and above
    backend_level="WARNING",   # Journald/EventLog shows WARNING and above
    graylog_level="ERROR",     # Graylog shows ERROR and above
    enable_graylog=True,
)
log.init(config)
log.attach_std_logging()  # Automatically uses get_minimum_log_level() -> INFO

# Now stdlib loggers won't pre-filter
logging.getLogger("app.module").info("This reaches lib_log_rich console")
logging.getLogger("app.module").debug("This is filtered by lib_log_rich, not stdlib")

log.shutdown()
```

Key points:
- **Independent levels**: `console_level`, `backend_level`, and `graylog_level` are completely independent. Each gates events to its respective adapter without affecting the others.
- **`get_minimum_log_level()`**: Returns the lowest (most permissive) threshold among active adapters. When Graylog is disabled, its level is ignored.
- **Automatic integration**: `attach_std_logging()` uses this by default; pass `logger_level=None` to leave the stdlib logger level unchanged.

---


## Runtime Reference

<a id="section-public-api"></a>
### Public API

All runtime configuration flows through `RuntimeConfig`. Create an instance with the desired fields and pass it to `lib_log_rich.init(config)`. The table below summarises the fields and related helpers.

| Symbol                     | Signature (abridged)                                                                                                                                                                                                                                                | Description                                                                                                                                                                                                                                                                                                                                                                                                      |
|----------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `init`                     | `init(config: RuntimeConfig)`                                                                                                                                                                                                                                       | Composition root. Wires adapters, queue, scrubber, and rate limiter. Pass a `RuntimeConfig` instance; environment variables listed below continue to override matching fields.                                                                                                                                                                                                                                   |
| `bind`                     | `bind(**fields)` (context manager)                                                                                                                                                                                                                                  | Binds contextual metadata. Requires `service`, `environment`, and `job_id` when no parent context exists; nested scopes merge overrides. Yields the active `LogContext`.                                                                                                                                                                                                                                         |
| `getLogger`                | `getLogger(name: str) -> LoggerProxy`                                                                                                                                                                                                                               | Returns a `LoggerProxy` exposing `.debug/.info/.warning/.error/.critical/.exception`. Each call returns a dict (e.g. `{"ok": True, "event_id": "..."}` or `{ "ok": False, "reason": "rate_limited" }`).                                                                                                                                                                                                         |
| `LoggerProxy`              | created via `getLogger(name)`                                                                                                                                                                                                                                       | Lightweight facade around the process use case. Level helpers mirror the stdlib signature: `.debug(msg, *args, exc_info=None, stack_info=False, stacklevel=1, extra=None)` (and similarly for `info`/`warning`/`error`/`critical`) plus `.exception(msg, *args, exc_info=True, ...)`, `.log(level, msg, *args, ...)`, and `.setLevel(level)`. Messages are formatted in the pipeline, `exc_info`/`stack_info` payloads are preserved for adapters, and `stacklevel` is accepted but ignored. `.setLevel(...)` updates the proxy's own threshold so events must satisfy it *and* the handler levels; calls still return the diagnostic dict (unlike `logging.Logger`, which returns `None`). |
| `dump`                     | `dump(*, dump_format="text", path=None, level=None, console_format_preset=None, console_format_template=None, theme=None, console_styles=None, context_filters=None, context_extra_filters=None, extra_filters=None, color=False) -> str`                           | Serialises the ring buffer (text/json/html_table/html_txt). `level` filters events by severity, presets/templates customise text rendering (template wins), `theme`/`console_styles` reuse or override the runtime palette, the `context_*`/`extra_*` filter mappings narrow results by metadata, and `color` toggles ANSI output for text dumps. Payloads are always returned and optionally written to `path`. |
| `get_minimum_log_level`    | `get_minimum_log_level() -> LogLevel`                                                                                                                                                                                                                              | Returns the minimum (most permissive) log level among all active adapters (`console_level`, `backend_level`, and `graylog_level` when Graylog is enabled). Useful for setting the stdlib `logging.Logger` root level to match the lowest threshold, ensuring no events are pre-filtered before reaching lib_log_rich. Raises `RuntimeError` if called before `init()`.                                           |
| `max_level_seen`           | `max_level_seen() -> LogLevel                                                                                                                                                                                                                                       | None`                                                                                                                                                                                                                                                                                                                                                                                                            | Returns the highest severity observed since the runtime was initialised or metrics were reset. Handy for "only dump logs when something ≥ ERROR happened" checks. |
| `severity_snapshot`        | `severity_snapshot() -> SeveritySnapshot`                                                                                                                                                                                                                           | Captures totals, per-level counts, threshold buckets, and drop statistics (by reason and severity). Safe to call from any thread.                                                                                                                                                                                                                                                                                |
| `reset_severity_metrics`   | `reset_severity_metrics() -> None`                                                                                                                                                                                                                                  | Clears severity and drop counters without touching the ring buffer or adapters. Invoke after you’ve handed dumps to operators.                                                                                                                                                                                                                                                                                   |
| `shutdown`                 | `shutdown() -> None`                                                                                                                                                                                                                                                | Flushes adapters, drains/stops the queue, and clears global state. Safe to call repeatedly after initialisation.                                                                                                                                                                                                                                                                                                 |
| `hello_world`              | `hello_world() -> None`                                                                                                                                                                                                                                             | Prints the canonical “Hello World” message for smoke tests.                                                                                                                                                                                                                                                                                                                                                      |
| `i_should_fail`            | `i_should_fail() -> None`                                                                                                                                                                                                                                           | Raises `RuntimeError("I should fail")` to exercise failure handling paths.                                                                                                                                                                                                                                                                                                                                       |
| `summary_info`             | `summary_info() -> str`                                                                                                                                                                                                                                             | Returns the CLI metadata banner as a string without printing it.                                                                                                                                                                                                                                                                                                                                                 |
| `logdemo`                  | `logdemo(*, theme="classic", service=None, environment=None, dump_format=None, dump_path=None, color=None, enable_graylog=False, graylog_endpoint=None, graylog_protocol="tcp", graylog_tls=False, enable_journald=False, enable_eventlog=False) -> dict[str, Any]` | Spins up a temporary runtime, emits one sample event per level, optionally renders a dump, and records which backends were requested via the `backends` mapping. Use the boolean flags to exercise Graylog, journald, or Windows Event Log sinks from the CLI or API.                                                                                                                                            |
| `SeveritySnapshot`         | dataclass returned by `severity_snapshot()`                                                                                                                                                                                                                         | Fields: `highest`, `total_events`, `counts`, `thresholds`, `dropped_total`, `drops_by_reason`, `drops_by_level`, `drops_by_reason_and_level`. All mappings are read-only copies so you can serialise them directly.                                                                                                                                                                                              |
| `QueueConsoleAdapter`      | `QueueConsoleAdapter(queue, *, export_style="ansi", force_color=False, no_color=False, styles=None, format_preset=None, format_template=None, console_width=None)`                                                                                                  | Threaded console adapter that renders Rich output into a `queue.Queue`. It reuses the Rich formatter so console level thresholds and styling stay consistent. Pass via `console_adapter_factory` to stream ANSI or HTML lines to GUIs, SSE/WebSocket feeds, or background workers without touching global state.                                                                                                 |
| `AsyncQueueConsoleAdapter` | `AsyncQueueConsoleAdapter(queue, *, export_style="ansi", force_color=False, no_color=False, styles=None, format_preset=None, format_template=None, console_width=None)`                                                                                             | Asyncio variant targeting `asyncio.Queue` producers/consumers. It shares the Rich formatter and level gate with the default console adapter. Ideal for Textual apps, async servers, or tests that await console output; wire it with `console_adapter_factory` alongside the threaded adapter. Chunks are enqueued with `put_nowait`, so full queues drop latest segments.                                       |
| `ExportStyle`              | `Literal["ansi", "html"]`                                                                                                                                                                                                                                           | Type alias selecting the payload format returned by queue-backed console adapters (`"ansi"` for terminal passthrough, `"html"` for rich web panes).                                                                                                                                                                                                                                                              |

---

<a id="section-severity-metrics"></a>
## Inspecting severity and drop metrics

The runtime keeps a thread-safe severity monitor so you can make dump decisions without scanning the ring buffer.

```python
import lib_log_rich as log

config = log.RuntimeConfig(service="svc", environment="metrics", queue_enabled=False)
log.init(config)

with log.bind(job_id="run-42"):
    log.getLogger("svc.worker").info("started")
    log.getLogger("svc.worker").error("boom")

# `LoggerProxy` instances returned by `getLogger()` support the standard logging-level methods:

logger = log.getLogger("app.component")
logger.info("payload %s", "alice", extra={"user": "alice"})
logger.error("boom", exc_info=True)
logger.warning("slow call", stack_info=True)
try:
    raise RuntimeError("bad input")
except RuntimeError:
    logger.exception("captured runtime failure")

# Drop INFO/DEBUG at the proxy before they hit any handlers
logger.setLevel("ERROR")
```

Each call returns a dictionary describing the outcome (success + event id, `{ "queued": True }`, or `{ "reason": "rate_limited" }`). Standard logging APIs return `None`; this diagnostic payload is the deliberate divergence we keep for caller observability.

The public `.log(...)` helper (and the private `_log`) normalise level inputs from strings (`"warning"`), integers (`logging.WARNING`), or the domain enum so advanced callers can apply dynamic thresholds without reimplementing conversions. Format strings are interpolated inside the process pipeline, so `%`-style placeholders behave exactly like `logging.Logger`. When `exc_info` or `stack_info` are provided they are compacted according to the configured payload limits and forwarded to every adapter. The `stacklevel` keyword is accepted for drop-in compatibility but currently ignored. `.exception(...)` is a thin convenience wrapper over `.error(...)` that sets `level=LogLevel.ERROR` and defaults `exc_info` to `True`. `.setLevel(...)` gates events at the proxy, leaving console/backend/Graylog thresholds untouched so both conditions must be satisfied for emission.

The optional `extra` mapping is copied into the structured event and travels end-to-end: it is scrubbed, persisted in the ring buffer, and forwarded to every adapter (Rich console, journald, Windows Event Log, Graylog, dump exporters). Use it to attach contextual fields such as port numbers, tenant IDs, or feature flags.

Need a quick preview of console colours? Call:

```python
import lib_log_rich as log

result = log.logdemo(theme="neon", dump_format="json")
print(result["events"])   # list of per-level emission results
print(result["dump"])     # rendered dump string (or None when not requested)
print(result["backends"]) # {'graylog': False, 'journald': False, 'eventlog': False}
```

The helper initialises a throwaway runtime, emits one message per level using the selected theme, optionally renders a text/JSON/HTML dump via the `dump_format` argument, and then shuts itself down. Themes are defined in [CONSOLESTYLES.md](CONSOLESTYLES.md) and include `classic`, `dark`, `neon`, and `pastel` (you can add more via `console_styles`).

The optional backend flags (`enable_graylog`, `enable_journald`, `enable_eventlog`) let you route the demo events to real adapters during manual testing—the return payload exposes the chosen targets via `result["backends"]`.

---

### Runtime configuration

`lib_log_rich.init` wires the entire runtime. All parameters are keyword-only and may be overridden by environment variables shown in the last column.

| Parameter                       | Type                                                 | Default                                             | Expected values                                                                                  | Purpose                                                                             | Environment variable                                 |
|---------------------------------|------------------------------------------------------|-----------------------------------------------------|--------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|------------------------------------------------------|
| `service`                       | `str`                                                | *(required)*                                        | Non-empty identifier such as `checkout`, `worker`, `billing`.                                    | Logical service name recorded in each event and used by adapters.                   | `LOG_SERVICE`                                        |
| `environment`                   | `str`                                                | *(required)*                                        | Deployment label (`dev`, `stage`, `prod`, `local`, ...)                                          | Deployment environment recorded in each event and used by adapters.                 | `LOG_ENVIRONMENT`                                    |
| `console_level`                 | `str \| LogLevel`                                    | `LogLevel.INFO`                                     | Case-insensitive `debug`, `info`, `warning`, `error`, `critical` or a `LogLevel` enum.           | Lowest level emitted to the Rich console adapter. **Independent** of backend/Graylog levels. | `LOG_CONSOLE_LEVEL`                                  |
| `backend_level`                 | `str \| LogLevel`                                    | `LogLevel.WARNING`                                  | Same set as `console_level`.                                                                     | Threshold shared by journald and Windows Event Log adapters. **Independent** of console/Graylog levels. | `LOG_BACKEND_LEVEL`                                  |
| `graylog_endpoint`              | `tuple[str, int] \| None`                            | `None`                                              | `(host, port)` tuple or `HOST:PORT` string (port > 0).                                           | Host/port for GELF; combine with `enable_graylog=True`.                             | `LOG_GRAYLOG_ENDPOINT` (`host:port`)                 |
| `graylog_protocol`              | `str`                                                | `"tcp"`                                             | Literal `tcp` or `udp` (case-insensitive).                                                       | Transport to reach Graylog.                                                         | `LOG_GRAYLOG_PROTOCOL`                               |
| `graylog_tls`                   | `bool`                                               | `False`                                             | `True` only when `graylog_protocol="tcp"`.                                                       | Wrap the Graylog connection in TLS.                                                 | `LOG_GRAYLOG_TLS`                                    |
| `graylog_level`                 | `str \| LogLevel`                                    | `LogLevel.WARNING`                                  | Same set as `console_level`.                                                                     | Severity threshold for Graylog fan-out. **Independent** of console/backend levels. Use `get_minimum_log_level()` to find the lowest among all three. | `LOG_GRAYLOG_LEVEL`                                  |
| `enable_ring_buffer`            | `bool`                                               | `True`                                              | `True`/`False`.                                                                                  | Toggles the in-memory ring buffer (falls back to 1024 events when disabled).        | `LOG_RING_BUFFER_ENABLED`                            |
| `ring_buffer_size`              | `int`                                                | `25_000`                                            | Integer > 0 (events).                                                                            | Max events retained in the ring buffer.                                             | `LOG_RING_BUFFER_SIZE`                               |
| `enable_journald`               | `bool`                                               | `False`                                             | `True`/`False`; effective on Linux/systemd only.                                                 | Adds the journald adapter.                                                          | `LOG_ENABLE_JOURNALD`                                |
| `enable_eventlog`               | `bool`                                               | `False`                                             | `True`/`False`; effective on Windows only.                                                       | Adds the Windows Event Log adapter.                                                 | `LOG_ENABLE_EVENTLOG`                                |
| `enable_graylog`                | `bool`                                               | `False`                                             | `True`/`False`; requires `graylog_endpoint`.                                                     | Enables the Graylog adapter.                                                        | `LOG_ENABLE_GRAYLOG`                                 |
| `queue_enabled`                 | `bool`                                               | `True`                                              | `True`/`False`.                                                                                  | Routes events through the background queue worker.                                  | `LOG_QUEUE_ENABLED`                                  |
| `queue_maxsize`                 | `int`                                                | `2048`                                              | Integer > 0 (slots).                                                                             | Maximum pending events before the full-policy applies.                              | `LOG_QUEUE_MAXSIZE`                                  |
| `queue_full_policy`             | `str`                                                | `"block"`                                           | Literal `block` or `drop`.                                                                       | Choose whether producers wait or drop when the queue is full.                       | `LOG_QUEUE_FULL_POLICY`                              |
| `queue_put_timeout`             | `float \| None`                                      | `1.0`                                               | Seconds > 0, or `None`/`<=0` for indefinite wait.                                                | Timeout applied to blocking queue puts before falling back to the caller.           | `LOG_QUEUE_PUT_TIMEOUT`                              |
| `queue_stop_timeout`            | `float \| None`                                      | `5.0`                                               | Seconds > 0, or `None`/`<=0` to wait forever.                                                    | Deadline for draining the queue during `shutdown()`.                                | `LOG_QUEUE_STOP_TIMEOUT`                             |
| `force_color`                   | `bool`                                               | `False`                                             | `True`/`False`.                                                                                  | Forces Rich colour output even when `stderr` is not a TTY.                          | `LOG_FORCE_COLOR`                                    |
| `no_color`                      | `bool`                                               | `False`                                             | `True`/`False`.                                                                                  | Disables colour output regardless of terminal support.                              | `LOG_NO_COLOR`                                       |
| `console_styles`                | `mapping[str, str] \| None`                          | `None`                                              | Mapping keyed by `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` with Rich style strings.        | Rich style overrides per level.                                                     | `LOG_CONSOLE_STYLES` (comma-separated `LEVEL=style`) |
| `console_theme`                 | `str \| None`                                        | `"dark"`                                            | `classic`, `dark`, `neon`, `pastel`, or another Rich theme name.                                 | Palette applied to the console (and inherited by dumps without overrides).          | `LOG_CONSOLE_THEME`                                  |
| `console_format_preset`         | `str \| None`                                        | platform-specific                                   | Preset key `full`, `short`, `full_loc`, `short_loc`, `short_loc_icon` (case-insensitive). Default: `short_loc_icon` on Windows, `short_loc` on Linux/Mac. | Console line layout when no custom template is supplied.                            | `LOG_CONSOLE_FORMAT_PRESET`                          |
| `console_format_template`       | `str \| None`                                        | `None`                                              | Python `str.format` template using fields listed in [Template fields](#console-template-fields). | Custom console template overriding the preset (also used by text dumps by default). | `LOG_CONSOLE_FORMAT_TEMPLATE`                        |
| `console_stream`                | `Literal["stdout","stderr","both","custom","none"]` | `"stderr"`                                          | Choose the Rich console destination: stdout, stderr, both, a custom stream, or mute entirely.    | Default mirrors stdlib logging (stderr); tweak to match host expectations.           | `LOG_CONSOLE_STREAM`                                 |
| `console_stream_target`         | `TextIO \| None`                                     | `None`                                              | File-like object implementing `write()`; required when `console_stream="custom"`.              | Inject in-memory buffers or other sinks without a custom adapter factory.           | (none)                                               |
| `console_adapter_factory`       | `Callable[[ConsoleAppearance], ConsolePort] \| None` | `None`                                              | Callable returning a custom `ConsolePort` adapter.                                               | Plug-in point for queue-backed or test adapters; see `STREAMINGCONSOLE.md`.         | (none)                                               |
| `dump_format_preset`            | `str \| None`                                        | `"full"`                                            | `full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`.                                      | Default text dump layout used when callers omit one.                                | `LOG_DUMP_FORMAT_PRESET`                             |
| `dump_format_template`          | `str \| None`                                        | `None`                                              | Python `str.format` template using [Template fields](#console-template-fields).                  | Default text dump template overriding the preset.                                   | `LOG_DUMP_FORMAT_TEMPLATE`                           |
| `scrub_patterns`                | `dict[str, str] \| None`                             | `{"password": ".+", "secret": ".+", "token": ".+"}` | Mapping of `field=regex` pairs merged with built-in scrub rules.                                 | Removes sensitive values before fan-out.                                            | `LOG_SCRUB_PATTERNS`                                 |
| `rate_limit`                    | `tuple[int, float] \| None`                          | `None`                                              | `(max_events, window_seconds)` with both positives; env string `MAX:WINDOW_SECONDS`.             | Throttles emissions before fan-out.                                                 | `LOG_RATE_LIMIT`                                     |
| `payload_limits`                | `dict[str, Any] \| PayloadLimits \| None`            | See below                                           | Keys from `PayloadLimits` (`message_max_chars`, `extra_max_keys`, etc.).                         | Guards message/extra size; see [Payload Limits](#payload-limits).                   | (none)                                               |
| `diagnostic_hook`               | `Callable[[str, dict[str, Any]], None] \| None`      | `None`                                              | Callable receiving `(event_name, diagnostics)` or `None`.                                        | Observes queue drops, rate limiting, payload clamps, etc.                           | (none)                                               |
| `config.enable_dotenv()` helper | *(call before `init()`)*                             | *(opt-in)*                                          | Optional `search_from`, `markers`, `dotenv_override`; see [DOTENV.md](DOTENV.md).                | Loads `.env` files while preserving env precedence.                                 | `LOG_USE_DOTENV` (CLI/module entry points)           |

#### Console template fields

`console_format_template` and `dump_format_template` accept the same Python `str.format` placeholders. Common fields include:

| Placeholder                           | Description                                                              |
|---------------------------------------|--------------------------------------------------------------------------|
| `timestamp`                           | ISO 8601 UTC timestamp with microseconds.                                |
| `timestamp_trimmed_naive`             | UTC timestamp trimmed to whole seconds without timezone information.     |
| `timestamp_loc`                       | Host-local ISO 8601 timestamp with offset.                               |
| `LEVEL`                               | Upper-case level name (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `level_icon`                          | Rich glyph representing the level (bug, info, warning, cross, skull).    |
| `level_code`                          | Four-character abbreviation (`DEBG`, `INFO`, `WARN`, `ERRO`, `CRIT`).    |
| `logger_name`                         | Name passed to `getLogger(...)` when the event was emitted.              |
| `message`                             | Log message string.                                                      |
| `context_fields`                      | Space-prefixed `key=value` pairs merged from `LogContext` and `extra`.   |
| `user_name`, `hostname`, `process_id` | System identity fields captured during initialisation.                   |
| `process_id_chain`                    | Parent/child process lineage formatted as `pid1>pid2>pid3`.              |
| `extra`                               | Shallow copy of the `extra` mapping supplied to the logger.              |

See [LOGDUMP.md](LOGDUMP.md#text-format-placeholders) for the exhaustive placeholder list (including individual date/time components and dotted aliases).

> See `STREAMINGCONSOLE.md` for advanced queue-backed console wiring examples that build on `console_adapter_factory`.

---

### Payload Limits

These guards exist to keep a single buggy or malicious caller from flooding the ring buffer, queue, or downstream sinks with giant payloads. The defaults clamp events to dimensions that safely fit journald, GELF, and log-shipper expectations while still leaving room for rich context.

Use `payload_limits` as either a mapping or a `PayloadLimits` instance, for example::

    config = log.RuntimeConfig(
        service="svc",
        environment="prod",
        payload_limits={"message_max_chars": 2048, "extra_max_keys": 10},
    )
    log.init(config)

Default limits guard the pipeline and can be tuned per environment. Each field is optional when you provide a mapping; unspecified values fall back to the defaults below.

**`PayloadLimits` fields**

- `truncate_message` *(bool, default `True`)* – when `True` long messages are truncated to `message_max_chars`; when `False` oversized messages raise `ValueError`.
- `message_max_chars` *(int, default `4096`)* – maximum characters for the primary log message.
- `extra_max_keys` *(int, default `25`)* – maximum number of keys accepted in the `extra` mapping attached to the event. Additional keys are dropped with a diagnostic hook notice.
- `extra_max_value_chars` *(int, default `512`)* – per-key character cap after values are stringified; excess content is truncated with a `…[truncated]` suffix.
- `extra_max_depth` *(int, default `3`)* – nesting depth allowed before nested structures are stringified.
- `extra_max_total_bytes` *(int \| None, default `8192`)* – total UTF-8 encoded size allowed for the sanitized `extra` payload. Set to `None` to disable the aggregate clamp.
- `context_max_keys` *(int, default `20`)* – maximum keys stored in `LogContext.extra`.
- `context_max_value_chars` *(int, default `256`)* – per-value limit for context metadata once stringified.
- `stacktrace_max_frames` *(int, default `10`)* – number of leading and trailing traceback frames preserved when `exc_info` is present; middle frames are replaced with `... truncated N frame(s) ...` and the result is subject to `extra_max_value_chars`.

Whenever a limit is enforced, the optional `diagnostic_hook` receives an event (for example `message_truncated`, `extra_keys_dropped`, `exc_info_truncated`) so operators can monitor clamping in production. Queue resilience signals use the same channel: `queue_worker_error` fires when the background worker raises (and the adapter flips its `worker_failed` flag for health checks, clearing automatically after the cooldown or a clean restart), and `queue_drop_callback_error` captures drop-handler failures without tearing the worker down.


Graylog fan-out uses the configured `graylog_level` (default `WARNING` when enabled, automatically tightened to `CRITICAL` when Graylog is disabled). Presets/templates cascade: console settings become the defaults for text dumps unless you provide dump-specific overrides.

The initializer also honours `LOG_BACKEND_LEVEL`, `LOG_FORCE_COLOR`, and `LOG_NO_COLOR` simultaneously—environment variables always win over supplied keyword arguments. When `enable_journald` is requested on Windows hosts or `enable_eventlog` on non-Windows hosts the runtime silently disables those adapters so cross-platform deployments never fail during initialisation.

> **Note:** TLS is only supported with the TCP transport. Combining `graylog_protocol="udp"` with TLS (or setting `LOG_GRAYLOG_PROTOCOL=udp` alongside `LOG_GRAYLOG_TLS=1`) raises a `ValueError` during initialisation.

---

<a id="section-env-overrides"></a>
### Environment-only overrides

Set these, restart your process, and the runtime will merge them with the arguments you pass to `init(...)`.

| Variable                      | Default                                 | Expected values                                                          | Effect                                                        |
|-------------------------------|-----------------------------------------|--------------------------------------------------------------------------|---------------------------------------------------------------|
| `LOG_SERVICE`                 | value passed to `init(service=...)`     | Non-empty string (e.g., `checkout`).                                     | Override the advertised service name.                         |
| `LOG_ENVIRONMENT`             | value passed to `init(environment=...)` | Non-empty string (e.g., `dev`, `stage`, `prod`).                         | Override the deployment/stage label.                          |
| `LOG_CONSOLE_LEVEL`           | `info`                                  | `debug`, `info`, `warning`, `error`, `critical` (case-insensitive).      | Minimum level emitted to the console adapter.                 |
| `LOG_BACKEND_LEVEL`           | `warning`                               | Same as `LOG_CONSOLE_LEVEL`.                                             | Threshold for journald/Event Log adapters.                    |
| `LOG_GRAYLOG_LEVEL`           | `warning`                               | Same as `LOG_CONSOLE_LEVEL`.                                             | Threshold for Graylog emission.                               |
| `LOG_RING_BUFFER_ENABLED`     | `true`                                  | Boolean toggle (`1/true/on` or `0/false/off`).                           | Disable to skip ring-buffer retention.                        |
| `LOG_RING_BUFFER_SIZE`        | `25000`                                 | Integer > 0 (events).                                                    | Resize the in-memory ring buffer.                             |
| `LOG_ENABLE_JOURNALD`         | `false`                                 | Boolean toggle (ignored on Windows).                                     | Enable/disable the journald adapter.                          |
| `LOG_ENABLE_EVENTLOG`         | `false`                                 | Boolean toggle (ignored on non-Windows).                                 | Enable/disable the Windows Event Log adapter.                 |
| `LOG_ENABLE_GRAYLOG`          | `false`                                 | Boolean toggle; requires `LOG_GRAYLOG_ENDPOINT`.                         | Enable the Graylog adapter.                                   |
| `LOG_GRAYLOG_ENDPOINT`        | none                                    | `HOST:PORT` string with port > 0.                                        | Target host and port for GELF.                                |
| `LOG_GRAYLOG_PROTOCOL`        | `tcp`                                   | `tcp` or `udp`.                                                          | Choose Graylog transport.                                     |
| `LOG_GRAYLOG_TLS`             | `false`                                 | Boolean toggle; only valid with `tcp`.                                   | Wrap the Graylog connection in TLS.                           |
| `LOG_QUEUE_ENABLED`           | `true`                                  | Boolean toggle.                                                          | Disable to process fan-out inline without a queue.            |
| `LOG_QUEUE_MAXSIZE`           | `2048`                                  | Integer > 0 (slots).                                                     | Queue capacity before the full-policy applies.                |
| `LOG_QUEUE_FULL_POLICY`       | `block`                                 | `block` or `drop`.                                                       | Decide whether producers wait or drop when the queue is full. |
| `LOG_QUEUE_PUT_TIMEOUT`       | none                                    | Seconds (float). `<=0` clears the timeout.                               | Timeout for blocking puts before the caller handles overflow. |
| `LOG_QUEUE_STOP_TIMEOUT`      | `5.0`                                   | Seconds (float). `<=0` waits indefinitely.                               | Drain deadline during shutdown.                               |
| `LOG_FORCE_COLOR`             | `false`                                 | Boolean toggle.                                                          | Force ANSI colour even when stderr is not a TTY.              |
| `LOG_NO_COLOR`                | `false`                                 | Boolean toggle.                                                          | Strip colour output entirely.                                 |
| `LOG_CONSOLE_THEME`           | `"dark"`                                | `classic`, `dark`, `neon`, `pastel`, or Rich theme name.                 | Apply a palette to the console/dumps.                         |
| `LOG_CONSOLE_STYLES`          | none                                    | Comma-separated `LEVEL=style` pairs (e.g., `INFO=green`).                | Override Rich styles per level.                               |
| `LOG_CONSOLE_FORMAT_PRESET`   | platform-specific                       | `full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`. Default: `short_loc_icon` on Windows, `short_loc` on Linux/Mac. | Default preset for console lines. |
| `LOG_CONSOLE_FORMAT_TEMPLATE` | none                                    | `str.format` template using [Template fields](#console-template-fields). | Override the preset with a custom layout.                     |
| `LOG_CONSOLE_STREAM`          | `stderr`                                | `stdout`, `stderr`, `both`, `custom`, `none`.                             | Redirect console output; `custom` requires `console_stream_target`, `none` mutes the console. |
| `LOG_DUMP_FORMAT_PRESET`      | `full`                                  | `full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`.              | Default preset when dumping with `dump_format="text"`.        |
| `LOG_DUMP_FORMAT_TEMPLATE`    | none                                    | `str.format` template using [Template fields](#console-template-fields). | Custom text-dump template.                                    |
| `LOG_SCRUB_PATTERNS`          | `password=.+,secret=.+,token=.+`        | Comma-separated `field=regex` pairs.                                     | Merge additional scrub patterns with defaults.                |
| `LOG_RATE_LIMIT`              | none                                    | `MAX:WINDOW_SECONDS` with positive numbers (e.g., `500:60`).             | Rate limit emissions before fan-out.                          |
| `LOG_USE_DOTENV`              | `false`                                 | Boolean toggle.                                                          | Allow the CLI/module entry point to load a nearby `.env`.     |

Queue safety defaults
---------------------
See also [QUEUE.md](QUEUE.md) for a complete guide to queue policies, diagnostics, and migration notes.

The queue waits indefinitely by default (`queue_put_timeout=None`). When you supply a positive timeout and the worker remains in a failed state, blocking puts fall back to drop mode once the timeout elapses and the runtime emits a `queue_degraded_drop_mode` diagnostic. After recovery (cooldown expiry, clean `stop(drain=True)`, or a fresh `start()`), the adapter restores the configured policy. Tune `queue_put_timeout` or `LOG_QUEUE_PUT_TIMEOUT` if you need bounded waits.

Boolean variables treat `1`, `true`, `yes`, or `on` (case-insensitive) as truthy; everything else falls back to the default or provided argument.

---



## Operations & Observability

### Log dump
`log.dump(...)` bridges the in-memory ring buffer to structured exports. See [LOGDUMP.md](LOGDUMP.md) for parameter tables, text placeholder references, and usage notes covering text/JSON/HTML dumps.

JSON dumps expose enriched metadata (`level_name`, numeric `level_value`, the four-character `level_code`, and the console `level_icon`) plus a normalised `process_id_chain`.
When you need to isolate specific events, provide mapping-based filters such as ``context_filters={"job_id": "batch-42"}`` or ``extra_filters={"request": {"icontains": "api"}}``. Entries accept exact values, substring predicates (`contains`/`icontains`), or regex dictionaries (`{"pattern": r"^prefix", "regex": True}`), and multiple keys combine with logical AND while repeated keys OR together.

### Inspecting severity and drop metrics
```
snapshot = log.severity_snapshot()
assert snapshot.highest is log.LogLevel.ERROR
assert snapshot.total_events == 2
assert snapshot.dropped_total == 0
assert snapshot.drops_by_reason["rate_limited"] == 0  # counters are pre-seeded
warning_or_higher = snapshot.thresholds[log.LogLevel.WARNING]
assert warning_or_higher == 1

# Drops caused by rate limiting, queue saturation, or adapter failures
# surface by reason and severity:

rate_limited_errors = snapshot.drops_by_reason.get("rate_limited", 0)

log.reset_severity_metrics()  # start a fresh window without reinitialising the runtime

if log.max_level_seen() is None:
    print("No high-severity events since the reset")
```

Current drop reasons are `"rate_limited"`, `"queue_full"`, and `"adapter_error"`; use `drops_by_reason_and_level` when you need per-severity breakdowns for dashboards or alerts.

Threshold buckets complement the per-level counts: each threshold represents an
“at least” view (for example the built-in `LogLevel.WARNING` bucket includes
WARNING, ERROR, and CRITICAL events). The runtime seeds WARNING and ERROR by
default so operators can monitor actionable activity without summing individual
levels. When wiring a monitor manually (e.g., for custom tooling) you can pass
additional `LogLevel` values to `SeverityMonitor(thresholds=...)` to track
different cut-offs.

- By default the `SeverityMonitor` tracks two cumulative thresholds:
  - `LogLevel.WARNING`: counts every event logged at WARNING, ERROR, or CRITICAL.
  - `LogLevel.ERROR`: counts every event logged at ERROR or CRITICAL.

  Those buckets are included automatically so you can see “actionable” volume
  without summing individual levels. If you need different cut-offs (e.g., include
  INFO or add a CRITICAL-only bucket) you can pass your own iterable when
  constructing the monitor—`SeverityMonitor(thresholds=[LogLevel.INFO, LogLevel.ERROR])`—
  and the snapshot will expose exactly those you request.

---

## Terminal compatibility
Rich automatically detects whether the target is 16-colour, 256-colour, or truecolor, and adjusts the style to the nearest supported palette. For truly minimal environments (plain logs, CI artefacts), set `no_color=True` (or `LOG_NO_COLOR=1`) and Rich suppresses ANSI escapes entirely. Conversely, `force_color=True` (or `LOG_FORCE_COLOR=1`) forces colouring even if `stderr` isn’t a tty (useful in some container setups).

## Console & Terminal Experience

### Customising per-level colours

Override the default Rich styles by passing a dictionary to `init(console_styles=...)` or by exporting `LOG_CONSOLE_STYLES` as a comma-separated list, for example:

```
export LOG_CONSOLE_STYLES="DEBUG=dim,INFO=bright_green,WARNING=bold yellow,ERROR=bold white on red,CRITICAL=bold magenta"
```

Values use Rich's style grammar (named colours, modifiers like `bold`/`dim`, or hex RGB). Omitted keys fall back to the built-in theme. `logdemo` cycles through all preset × theme combinations (5 presets × 4 themes = 20 examples by default) so you can preview styles before committing to overrides. Use `--preset` and `--theme` options to filter specific combinations.

---

## CLI and Automation

<a id="section-cli"></a>
### CLI entry point

`lib_log_rich` ships with a rich-click interface for quick diagnostics, demos, and automation. See [CLI.md](CLI.md) for the full command breakdown, option tables, and usage examples. Quick highlight: run `python -m lib_log_rich` for the metadata banner, use `lib_log_rich logdemo` to preview console themes and generate text/JSON/HTML dumps (with optional Graylog, journald, or Event Log fan-out), or launch `lib_log_rich stresstest` to drive a Textual TUI that stress-tests the runtime while streaming live diagnostics. (The stress tester requires Textual; install dev extras with `pip install -e .[dev]` before running it.)
Filtering options such as `--context-exact job_id=batch` and `--extra-regex request=^api` flow through `logdemo` so CLI dumps can focus on specific workloads without post-processing. Regex-based filters validate patterns eagerly and raise a friendly `click.BadParameter` when a pattern is invalid, so typos no longer bubble raw `re.error` traces to the terminal.

---

<a id="section-streaming"></a>
#### Streaming console output to other consumers

`runtime.init` accepts a `console_adapter_factory`, letting you swap the Rich console for a custom adapter without touching internals. Ship a queue-backed adapter (see `lib_log_rich.adapters.console.QueueConsoleAdapter`) to feed GUIs, SSE/WebSocket endpoints, or tests. Each adapter honours the same format presets/templates **and** level filtering as the Rich console and can emit ANSI strings or HTML snippets. A detailed walk-through with threaded, asyncio, and composite patterns lives in [STREAMINGCONSOLE.md](STREAMINGCONSOLE.md).

A minimal Flask example lives in [`examples/flask_console_stream.py`](examples/flask_console_stream.py); it pushes rendered HTML lines to an `async EventSource` stream while regular CLI/TUI usage keeps the Textual stress tester responsive—no monkey patching required.

---

#### Quick smoke-test helpers ship with the package:

```python
import lib_log_rich as log
log.hello_world()
try:
    log.i_should_fail()
except RuntimeError as exc:
    print(exc)
```

---


<a id="section-further-docs"></a>
## Further documentation
- [docs/systemdesign/concept.md](docs/systemdesign/concept.md) — product concept and goals.
- [docs/systemdesign/concept_architecture.md](docs/systemdesign/concept_architecture.md) — layered architecture guide.
- [docs/systemdesign/concept_architecture_plan.md](docs/systemdesign/concept_architecture_plan.md) — TDD implementation roadmap.
- [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md) — authoritative design reference.
- [INSTALL.md](INSTALL.md) — detailed installation paths.
- [README.md](README.md) — quick overview and parameters.
- [CLI.md](CLI.md) — command reference, options, and CLI usage examples.
- [LOGDUMP.md](LOGDUMP.md) — dump API parameters, placeholders, and usage guidance.
- [CONSOLESTYLES.md](CONSOLESTYLES.md) — palette syntax, themes, and overrides.
- [STREAMINGCONSOLE.md](STREAMINGCONSOLE.md) — queue-backed console adapters and `console_adapter_factory` patterns.
- [DOTENV.md](DOTENV.md) — opt-in `.env` loading flow, CLI flags, and precedence rules.
- [INSTALL_JOURNAL.md](INSTALL_JOURNAL.md) — journald-specific installation checks, socket permissions, and smoke tests.
- [SUBPROCESSES.md](SUBPROCESSES.md) — multi-process logging guidance.
- [EXAMPLES.md](EXAMPLES.md) — runnable snippets from Hello World to multi-backend wiring.
- [DEVELOPMENT.md](DEVELOPMENT.md) — contributor workflow.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution expectations, coding standards, and review process.
- [CHANGELOG.md](CHANGELOG.md) — release history and noteworthy changes.
- [DIAGNOSTIC.md](DIAGNOSTIC.md) — diagnostic hook semantics, event catalogue, and instrumentation patterns.

---

<a id="section-development"></a>
## Development

Contributor workflows, make targets, CI automation, and release guidance are documented in [DEVELOPMENT.md](DEVELOPMENT.md).



## License

[MIT](LICENSE)
