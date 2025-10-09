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

Rich-powered logging backbone with contextual metadata, multi-target fan-out (console, journald, Windows Event Log, Graylog), ring-buffer dumps, and 
queue-based decoupling for multi-process workloads.  
Rich renders multi-colour output tuned to each terminal, while adapters and dump exporters support configurable formats and templates.  
Each runtime captures the active user, short hostname, process id, and PID chain automatically, so every sink receives consistent system identity fields.  
The public API stays intentionally small: initialise once, bind context, emit logs (with per-event `extra` payloads), dump history in text/JSON/HTML, and shut down cleanly.

> **Python requirement:** lib_log_rich targets Python 3.13 and newer.

- colored terminal logs via rich, with UTC or local timestamps
- supports journald
- supports Windows Event Logs
- supports Graylog via Gelf (and gRPC after adding Open Telemetry Support)
- supports quick log-dump with filtering from the ringbuffer without leaving the application
- runtime configuration validated via Pydantic models, yielding structured errors and JSON schemas
- per-event payload guards (4KB messages, 8KB extras, depth limits) configurable via `payload_limits`
- backend level governs journald / Event Log adapters, while `graylog_level` can be higher to further trim Graylog traffic
- opt-in `.env` loading (same precedence for CLI and programmatic use)
- Open Telemetry Support on user (Your) request - not implemented yet (because I do not need it myself). If You need it, let me know.
- optional `diagnostic_hook` callback that observes the runtime without modifying it. The hook reports queue activity (drops, worker failures, drop-handler errors), rate limiting, and payload clamps so you can surface metrics, alerts, or dashboards while keeping the logging pipeline decoupled from any monitoring stack.
- queue-backed console adapters (ANSI or HTML) — `QueueConsoleAdapter` for threads and `AsyncQueueConsoleAdapter` for asyncio — exposed via `console_adapter_factory`, handy for Textual panes, SSE/WebSocket streams, or tests.
- [EXAMPLES.md](EXAMPLES.md) — runnable snippets from Hello World to multi-backend wiring.

---

## Installation

For a quick start from PyPI:

```bash
pip install lib_log_rich
```

Detailed installation options (venv, pipx, uv, Poetry/PDM, and Git installs) live in [INSTALL.md](INSTALL.md).

### Journald adapter dependency

The Journald adapter imports `systemd.journal`. When you run the CLI stress tester or initialise the runtime with `enable_journald=True`, Python raises the cascading "cannot run inside active event loop / adapter error" if the bindings are missing. Install the bindings with your package manager (`sudo apt-get install python3-systemd` on Debian/Ubuntu) or add them to your virtual environment (`pip install systemd`). Runtime initialisation surfaces the same `RuntimeError` immediately when journald is enabled without the bindings, so you can fix the dependency before emitting events. Once installed the adapter can emit to journald regardless of queue settings.

---

## Usage

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
    logger = log.get("app.http")
    logger.info("ready", extra={"port": 8080})

# Inspect the recent history (text/json/html_table/html_txt)
print(log.dump(dump_format="json"))

log.shutdown()
```

### How to use in submodules

Initialise the runtime once near your process entrypoint, then let every
module fetch its own `LoggerProxy` via `lib_log_rich.get`. The runtime keeps the
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
from lib_log_rich import bind, get
from lib_log_rich.runtime import LoggerProxy

LoggerFactory = Callable[[str], LoggerProxy]


class PaymentProcessor:
    def __init__(self, get_logger: LoggerFactory | None = None) -> None:
        self._get_logger = get_logger or get

    def charge_order(self, order_id: str) -> None:
        logger = self._get_logger(__name__)
        with bind(order_id=order_id):
            logger.info("Submitting charge", extra={"provider": "stripe"})


# Composition root
processor = PaymentProcessor()
processor.charge_order("ord-123")
```

- Key points:
  - No module-level mutable state; PaymentProcessor takes a LoggerFactory, so tests can supply a stub while production code uses lib_log_rich.get.
  - The binding is scoped inside the method, which is explicit and test-friendly.
  - You can register a singleton instance if desired (processor = PaymentProcessor()), yet the module stays stateless and aligns with the repo’s dependency-injection guidelines.

If you just want a module-level logger but can not guarantee `lib_log_rich.init(...)` ran before the module is imported, add a tiny helper that initialises on demand once and then hands back the cached proxy. The helper keeps the module stateless and makes reuse in notebooks or ad-hoc scripts painless while still respecting the single-runtime rule.

```python
# billing/payments.py
from __future__ import annotations

from lib_log_rich import RuntimeConfig, bind, get, init
from lib_log_rich.runtime import LoggerProxy, is_initialised


def ensure_logging() -> LoggerProxy:
    if not is_initialised():
        try:
            init(RuntimeConfig(service="billing", environment="prod"))
        except RuntimeError as exc:
            # Another thread/process section won the race; re-check before propagating.
            if not is_initialised():
                raise
    return get(__name__)


logger: LoggerProxy = ensure_logging()


def charge_order(order_id: str) -> None:
    with bind(order_id=order_id):
        logger.info("Submitting charge", extra={"provider": "stripe"})
```

If you are certain `lib_log_rich.init(...)` already executed (for example in your CLI entrypoint), you can replace `ensure_logging()` with a direct `get(__name__)` assignment and keep the rest identical.

### Domain helpers

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
- `LoggerProxy.log(level, message, extra=None)` accepts the same level shapes (enum/string/integer), which makes it trivial to forward values from `logging.LogRecord.levelno` or CLI arguments without re-implementing conversion logic.

### Context vs. per-event metadata

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
        logger = log.get("billing.worker")
        # Per-event extras describe this specific message
        logger.info(
            "processed invoice",
            extra={"invoice_id": "INV-42", "duration_ms": 183},
        )

    # The emitted event includes:
    #   context.extra -> {"deployment": "blue", "team": "finops"}
    #   event.extra   -> {"invoice_id": "INV-42", "duration_ms": 183}

### exceptions logging 

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

### Opt-in `.env` loading

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

## CLI entry point

`lib_log_rich` ships with a rich-click interface for quick diagnostics, demos, and automation. See [CLI.md](CLI.md) for the full command breakdown, option tables, and usage examples. Quick highlight: run `python -m lib_log_rich` for the metadata banner, use `lib_log_rich logdemo` to preview console themes and generate text/JSON/HTML dumps (with optional Graylog, journald, or Event Log fan-out), or launch `lib_log_rich stresstest` to drive a Textual TUI that stress-tests the runtime while streaming live diagnostics. (The stress tester requires Textual; install dev extras with `pip install -e .[dev]` before running it.)
Filtering options such as `--context-exact job_id=batch` and `--extra-regex request=^api` flow through `logdemo` so CLI dumps can focus on specific workloads without post-processing. Regex-based filters validate patterns eagerly and raise a friendly `click.BadParameter` when a pattern is invalid, so typos no longer bubble raw `re.error` traces to the terminal.

---

### Streaming console output to other consumers

`runtime.init` accepts a `console_adapter_factory`, letting you swap the Rich console for a custom adapter without touching internals. Ship a queue-backed adapter (see `lib_log_rich.adapters.console.QueueConsoleAdapter`) to feed GUIs, SSE/WebSocket endpoints, or tests. Each adapter honours the same format presets/templates **and** level filtering as the Rich console and can emit ANSI strings or HTML snippets. A detailed walk-through with threaded, asyncio, and composite patterns lives in [STREAMINGCONSOLE.md](STREAMINGCONSOLE.md).

A minimal Flask example lives in [`examples/flask_console_stream.py`](examples/flask_console_stream.py); it pushes rendered HTML lines to an `async EventSource` stream while regular CLI/TUI usage keeps the Textual stress tester responsive—no monkey patching required.

---

### Quick smoke-test helpers ship with the package:

```python
log.hello_world()
try:
    log.i_should_fail()
except RuntimeError as exc:
    print(exc)
```

---

## log dump

`log.dump(...)` bridges the in-memory ring buffer to structured exports. See [LOGDUMP.md](LOGDUMP.md) for parameter tables, text placeholder references, and usage notes covering text/JSON/HTML dumps.

JSON dumps expose enriched metadata (`level_name`, numeric `level_value`, the four-character `level_code`, and the console `level_icon`) plus a normalised `process_id_chain`.
When you need to isolate specific events, provide mapping-based filters such as ``context_filters={"job_id": "batch-42"}`` or ``extra_filters={"request": {"icontains": "api"}}``. Entries accept exact values, substring predicates (`contains`/`icontains`), or regex dictionaries (`{"pattern": r"^prefix", "regex": True}`), and multiple keys combine with logical AND while repeated keys OR together.

---


## Public API

All runtime configuration flows through `RuntimeConfig`. Create an instance with the desired fields and pass it to `lib_log_rich.init(config)`. The table below summarises the fields and related helpers.

| Symbol          | Signature (abridged)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Description                                                                                                                                                                                                                                                                                                                                                                                                          |
|-----------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `init`          | `init(config: RuntimeConfig)`                                                                                                                                                                                                                                                                                                                                                                                    | Composition root. Wires adapters, queue, scrubber, and rate limiter. Pass a `RuntimeConfig` instance; environment variables listed below continue to override matching fields. |
| `bind`          | `bind(**fields)` (context manager) | Binds contextual metadata. Requires `service`, `environment`, and `job_id` when no parent context exists; nested scopes merge overrides. Yields the active `LogContext`. |
| `get`           | `get(name: str) -> LoggerProxy` | Returns a `LoggerProxy` exposing `.debug/.info/.warning/.error/.critical`. Each call returns a dict (e.g. `{"ok": True, "event_id": "..."}` or `{ "ok": False, "reason": "rate_limited" }`). |
| `LoggerProxy`   | created via `get(name)` | Lightweight facade around the process use case. Methods: `.debug(message, extra=None)`, `.info(...)`, `.warning(...)`, `.error(...)`, `.critical(...)`, and `.log(level, message, extra=None)`. All accept a string message plus optional mutable mapping for `extra`. The internal `_log(level, ...)` normalises `LogLevel`, strings, and stdlib numeric constants, raising on unsupported inputs. |
| `dump`          | `dump(*, dump_format="text", path=None, level=None, console_format_preset=None, console_format_template=None, theme=None, console_styles=None, context_filters=None, context_extra_filters=None, extra_filters=None, color=False) -> str` | Serialises the ring buffer (text/json/html_table/html_txt). `level` filters events by severity, presets/templates customise text rendering (template wins), `theme`/`console_styles` reuse or override the runtime palette, the `context_*`/`extra_*` filter mappings narrow results by metadata, and `color` toggles ANSI output for text dumps. Payloads are always returned and optionally written to `path`. |
| `shutdown`      | `shutdown() -> None` | Flushes adapters, drains/stops the queue, and clears global state. Safe to call repeatedly after initialisation. |
| `hello_world`   | `hello_world() -> None` | Prints the canonical “Hello World” message for smoke tests. |
| `i_should_fail` | `i_should_fail() -> None` | Raises `RuntimeError("I should fail")` to exercise failure handling paths. |
| `summary_info`  | `summary_info() -> str` | Returns the CLI metadata banner as a string without printing it. |
| `logdemo`       | `logdemo(*, theme="classic", service=None, environment=None, dump_format=None, dump_path=None, color=None, enable_graylog=False, graylog_endpoint=None, graylog_protocol="tcp", graylog_tls=False, enable_journald=False, enable_eventlog=False) -> dict[str, Any]` | Spins up a temporary runtime, emits one sample event per level, optionally renders a dump, and records which backends were requested via the `backends` mapping. Use the boolean flags to exercise Graylog, journald, or Windows Event Log sinks from the CLI or API. |
| `QueueConsoleAdapter` | `QueueConsoleAdapter(queue, *, export_style=ExportStyle.ANSI, force_color=False, no_color=False, styles=None, format_preset=None, format_template=None, console_width=None)` | Threaded console adapter that renders Rich output into a `queue.Queue`. It reuses the Rich formatter so console level thresholds and styling stay consistent. Pass via `console_adapter_factory` to stream ANSI or HTML lines to GUIs, SSE/WebSocket feeds, or background workers without touching global state. |
| `AsyncQueueConsoleAdapter` | `AsyncQueueConsoleAdapter(queue, *, export_style=ExportStyle.ANSI, force_color=False, no_color=False, styles=None, format_preset=None, format_template=None, console_width=None)` | Asyncio variant targeting `asyncio.Queue` producers/consumers. It shares the Rich formatter and level gate with the default console adapter. Ideal for Textual apps, async servers, or tests that await console output; wire it with `console_adapter_factory` alongside the threaded adapter. Chunks are enqueued with `put_nowait`, so full queues drop latest segments. |
| `ExportStyle`    | `Enum("ANSI", "HTML")` | Selects the payload format returned by queue-backed console adapters (`ANSI` for terminal passthrough, `HTML` for rich web panes). |


`LoggerProxy` instances returned by `get()` support the standard logging-level methods:

```python
logger = log.get("app.component")
logger.info("payload", extra={"user": "alice"})
logger.error("boom", extra={"secret": "***"})
```

Each call returns a dictionary describing the outcome (success + event id, `{ "queued": True }`, or `{ "reason": "rate_limited" }`). The public `.log(...)` helper (and the private `_log`) normalise level inputs from strings (`"warning"`), integers (`logging.WARNING`), or the domain enum so advanced callers can apply dynamic thresholds without reimplementing conversions.

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

| Parameter                       | Type                                | Default                                                                                                                                 | Purpose                                                                                                                | Environment variable                                 |
|---------------------------------|-------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| `service`                       | `str`                               | *(required)*                                                                                                                            | Logical service name recorded in each event and used by adapters.                                                      | `LOG_SERVICE`                                        |
| `environment`                   | `str`                               | *(required)*                                                                                                                            | Deployment environment (e.g., `dev`, `prod`).                                                                          | `LOG_ENVIRONMENT`                                    |
| `console_level`                 | `str \| LogLevel`                   | `LogLevel.INFO`                                                                                                                         | Lowest level emitted to the Rich console adapter. Accepts names (`"warning"`) or `LogLevel` instances.                 | `LOG_CONSOLE_LEVEL`                                  |
| `backend_level`                 | `str \| LogLevel`                   | `LogLevel.WARNING`                                                                                                                      | Threshold shared by structured backends (journald, Windows Event Log).                                                 | `LOG_BACKEND_LEVEL`                                  |
| `graylog_endpoint`              | `tuple[str, int] \| None`           | `None`                                                                                                                                  | Host/port for GELF over TCP. When set, combine with `enable_graylog=True`.                                             | `LOG_GRAYLOG_ENDPOINT` (`host:port` form)            |
| `graylog_protocol`              | `str`                               | `"tcp"`                                                                                                                                 | Transport to reach Graylog (`"tcp"` or `"udp"`).                                                                       | `LOG_GRAYLOG_PROTOCOL`                               |
| `graylog_tls`                   | `bool`                              | `False`                                                                                                                                 | Enables TLS when using TCP transport.                                                                                  | `LOG_GRAYLOG_TLS`                                    |
| `graylog_level`                 | `str \| LogLevel`                   | `LogLevel.WARNING`                                                                                                                      | Severity threshold for Graylog fan-out (applies when `enable_graylog=True`).                                           | `LOG_GRAYLOG_LEVEL`                                  |
| `enable_ring_buffer`            | `bool`                              | `True`                                                                                                                                  | Toggles the in-memory ring buffer. When disabled the system retains a small fallback buffer (1024 events).             | `LOG_RING_BUFFER_ENABLED`                            |
| `ring_buffer_size`              | `int`                               | `25_000`                                                                                                                                | Max events retained in the ring buffer when enabled.                                                                   | `LOG_RING_BUFFER_SIZE`                               |
| `enable_journald`               | `bool`                              | `False`                                                                                                                                 | Adds the journald adapter (Linux/systemd). Ignored on Windows hosts.                                                   | `LOG_ENABLE_JOURNALD`                                |
| `enable_eventlog`               | `bool`                              | `False`                                                                                                                                 | Adds the Windows Event Log adapter. Ignored on non-Windows platforms.                                                  | `LOG_ENABLE_EVENTLOG`                                |
| `enable_graylog`                | `bool`                              | `False`                                                                                                                                 | Enables the Graylog adapter (requires `graylog_endpoint`).                                                             | `LOG_ENABLE_GRAYLOG`                                 |
| `queue_enabled`                 | `bool`                              | `True`                                                                                                                                  | Routes events through a background queue for multi-process safety. Disable for simple scripts/tests.                   | `LOG_QUEUE_ENABLED`                                  |
| `queue_maxsize`                 | `int`                               | `2048`                                                                                                                                  | Max number of pending events before the full-policy applies.                                                           | `LOG_QUEUE_MAXSIZE`                                  |
| `queue_full_policy`             | `str` (`"block"`/`"drop"`)          | `"block"`                                                                                                                               | Choose whether producers block when the queue is full or drop additional events.                                              | `LOG_QUEUE_FULL_POLICY`                              |
| `queue_put_timeout`             | `float` \| `None`                   | `1.0`                                                                                                                                  | Timeout (seconds) for blocking queue puts; use `None` to wait indefinitely, positive values bound the wait before falling back on the caller. | `LOG_QUEUE_PUT_TIMEOUT`                              |
| `queue_stop_timeout`            | `float` \| `None`                   | `5.0`                                                                                                                                   | Deadline for draining the queue during `shutdown()`; `None` waits indefinitely.                                        | `LOG_QUEUE_STOP_TIMEOUT`                             |
| `force_color`                   | `bool`                              | `False`                                                                                                                                 | Forces Rich console colour output even when `stderr` isn’t a TTY.                                                      | `LOG_FORCE_COLOR`                                    |
| `no_color`                      | `bool`                              | `False`                                                                                                                                 | Disables colour output regardless of terminal support.                                                                 | `LOG_NO_COLOR`                                       |
| `console_styles`                | `mapping[str, str] \| None`         | `None`                                                                                                                                  | Optional Rich style overrides per level (e.g. `{ "INFO": "bright_green" }`).                                           | `LOG_CONSOLE_STYLES` (comma-separated `LEVEL=style`) |
| `console_theme`                 | `str \| None`                       | `None`                                                                                                                                  | Built-in palette name applied to the console and inherited by dumps when unset.                                        | `LOG_CONSOLE_THEME`                                  |
| `console_format_preset`         | `str \| None`                       | `"full"`                                                                                                                                | Preset used for console lines (and reused as the default text dump preset when no template is provided).               | `LOG_CONSOLE_FORMAT_PRESET` (defaults to `"full"`)   |
| `console_format_template`       | `str \| None`                       | `None`                                                                                                                                  | Custom console template overriding the preset and cascading to text dumps by default.                                  | `LOG_CONSOLE_FORMAT_TEMPLATE`                        |
| `console_adapter_factory`       | `Callable[[ConsoleAppearance], ConsolePort] \| None` | `None`                                                                                                                                  | Optional factory that returns a console adapter. Use it to plug `QueueConsoleAdapter` or `AsyncQueueConsoleAdapter` without monkey-patching; see `STREAMINGCONSOLE.md` for patterns. | *(no env var)*                                       |
| `dump_format_preset`            | `str \| None`                       | `"full"`                                                                                                                                | Default preset for text dumps when callers do not provide one explicitly.                                              | `LOG_DUMP_FORMAT_PRESET` (defaults to `"full"`)      |
| `dump_format_template`          | `str \| None`                       | `None`                                                                                                                                  | Default text dump template overriding the preset.                                                                      | `LOG_DUMP_FORMAT_TEMPLATE`                           |
| `scrub_patterns`                | `dict[str, str] \| None`            | `{"password": ".+", "secret": ".+", "token": ".+"}`                                                                                     | Regex patterns scrubbed from payloads before fan-out.                                                                  | `LOG_SCRUB_PATTERNS` (comma-separated `field=regex`) |
| `rate_limit`                    | `tuple[int, float] \| None`         | `None`                                                                                                                                  | `(max_events, window_seconds)` throttling applied before fan-out.                                                      | `LOG_RATE_LIMIT` (`"100:60"` format)                 |
| `payload_limits`                | `dict[str, Any]` \| `PayloadLimits` | Defaults clamp 4KB messages, 25 extras, 512-char values, depth 3, ~8KB aggregate, and compact stack traces. Override to tune behaviour. | Keeps a single buggy or malicious caller from flooding the ring buffer, queue, or downstream sinks with giant payloads. |
| `diagnostic_hook`               | `Callable`                          | `None`                                                                                                                                  | Optional callback the runtime invokes for internal telemetry (queue drops, worker failures, drop-handler errors, emissions, rate limiting, payload clamps). | *(code-only)*                                        |
| `config.enable_dotenv()` helper | *(call before `init()`)*            | *(opt-in)*                                                                                                                              | Walks upwards from a starting directory, loads the first `.env`, and caches the result.                                | `LOG_USE_DOTENV` (CLI/entry points only)             |

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

## Environment-only overrides

Set these, restart your process, and the runtime will merge them with the arguments you pass to `init(...)`.

| Variable                      | Default                                 | Effect                                                           |
|-------------------------------|-----------------------------------------|------------------------------------------------------------------|
| `LOG_SERVICE`                 | value passed to `init(service=...)`     | Override the advertised service name.                            |
| `LOG_ENVIRONMENT`             | value passed to `init(environment=...)` | Override the deployment/stage label.                             |
| `LOG_CONSOLE_LEVEL`           | `info`                                  | Minimum level emitted to the console adapter.                    |
| `LOG_BACKEND_LEVEL`           | `warning`                               | Threshold for journald/Event Log adapters.                       |
| `LOG_GRAYLOG_LEVEL`           | `warning`                               | Threshold for Graylog emission.                                  |
| `LOG_RING_BUFFER_ENABLED`     | `true`                                  | Disable (`0`) to skip ring-buffer retention.                     |
| `LOG_RING_BUFFER_SIZE`        | `25000`                                 | Resize the in-memory ring buffer (must stay > 0).                |
| `LOG_ENABLE_JOURNALD`         | `false`                                 | Toggle the journald adapter (ignored on Windows).                |
| `LOG_ENABLE_EVENTLOG`         | `false`                                 | Toggle the Windows Event Log adapter (ignored elsewhere).        |
| `LOG_ENABLE_GRAYLOG`          | `false`                                 | Enable the Graylog adapter; requires `LOG_GRAYLOG_ENDPOINT`.     |
| `LOG_GRAYLOG_ENDPOINT`        | none                                    | Host and port for GELF (`host:port`).                            |
| `LOG_GRAYLOG_PROTOCOL`        | `tcp`                                   | Choose `tcp` or `udp` transport for Graylog.                     |
| `LOG_GRAYLOG_TLS`             | `false`                                 | Wrap the TCP connection in TLS.                                  |
| `LOG_QUEUE_ENABLED`           | `true`                                  | Disable to process fan-out inline without a queue.               |
| `LOG_QUEUE_MAXSIZE`           | `2048`                                  | Queue capacity before the full-policy applies.                   |
| `LOG_QUEUE_FULL_POLICY`       | `block`                                 | `block` waits for space, `drop` rejects additional events.              |
| `LOG_QUEUE_PUT_TIMEOUT`       | none                                    | Timeout (seconds) for blocking puts; `<=0` clears it.            |
| `LOG_QUEUE_STOP_TIMEOUT`      | `5.0`                                   | Drain deadline during shutdown; `<=0` waits indefinitely.        |
| `LOG_FORCE_COLOR`             | `false`                                 | Force ANSI colour even when stderr is not a TTY.                 |
| `LOG_NO_COLOR`                | `false`                                 | Strip colour output entirely.                                    |
| `LOG_CONSOLE_THEME`           | none                                    | Apply a built-in Rich theme (`classic`, `dark`, `neon`, …).      |
| `LOG_CONSOLE_STYLES`          | none                                    | Comma-separated overrides such as `INFO=green,ERROR="bold red"`. |
| `LOG_CONSOLE_FORMAT_PRESET`   | `full`                                  | Default Rich preset for console lines and text dumps.            |
| `LOG_CONSOLE_FORMAT_TEMPLATE` | none                                    | Custom template that overrides the preset.                       |
| `LOG_DUMP_FORMAT_PRESET`      | `full`                                  | Default preset when dumping with `dump_format="text"`.           |
| `LOG_DUMP_FORMAT_TEMPLATE`    | none                                    | Custom text-dump template.                                       |
| `LOG_SCRUB_PATTERNS`          | `password=.+,secret=.+,token=.+`        | Extra `field=regex` pairs merged with defaults.                  |
| `LOG_RATE_LIMIT`              | none                                    | Rate limit as `MAX/WINDOW_SECONDS` (e.g., `500/60`).             |
| `LOG_USE_DOTENV`              | `false`                                 | Allow the CLI/module entry point to load a nearby `.env`.        |

Queue safety defaults
---------------------
See also [QUEUE.md](QUEUE.md) for a complete guide to queue policies, diagnostics, and migration notes.

The queue waits indefinitely by default (`queue_put_timeout=None`). When you supply a positive timeout and the worker remains in a failed state, blocking puts fall back to drop mode once the timeout elapses and the runtime emits a `queue_degraded_drop_mode` diagnostic. After recovery (cooldown expiry, clean `stop(drain=True)`, or a fresh `start()`), the adapter restores the configured policy. Tune `queue_put_timeout` or `LOG_QUEUE_PUT_TIMEOUT` if you need bounded waits.

Boolean variables treat `1`, `true`, `yes`, or `on` (case-insensitive) as truthy; everything else falls back to the default or provided argument.

---

## Terminal compatibility

Rich automatically detects whether the target is 16-colour, 256-colour, or truecolor, and adjusts the style to the nearest supported palette. For truly minimal environments (plain logs, CI artefacts), set `no_color=True` (or `LOG_NO_COLOR=1`) and Rich suppresses ANSI escapes entirely. Conversely, `force_color=True` (or `LOG_FORCE_COLOR=1`) forces colouring even if `stderr` isn’t a tty (useful in some container setups).

---

## Customising per-level colours

Override the default Rich styles by passing a dictionary to `init(console_styles=...)` or by exporting `LOG_CONSOLE_STYLES` as a comma-separated list, for example:

```
export LOG_CONSOLE_STYLES="DEBUG=dim,INFO=bright_green,WARNING=bold yellow,ERROR=bold white on red,CRITICAL=bold magenta"
```

Values use Rich’s style grammar (named colours, modifiers like `bold`/`dim`, or hex RGB). Omitted keys fall back to the built-in theme. `logdemo` cycles through the built-in palettes (`classic`, `dark`, `neon`, `pastel`) so you can preview styles before committing to overrides.

---

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
- [SUBPROCESSES.md](SUBPROCESSES.md) — multi-process logging guidance.
- [EXAMPLES.md](EXAMPLES.md) — runnable snippets from Hello World to multi-backend wiring.
- [DEVELOPMENT.md](DEVELOPMENT.md) — contributor workflow.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution expectations, coding standards, and review process.
- [CHANGELOG.md](CHANGELOG.md) — release history and noteworthy changes.
- [DIAGNOSTIC.md](DIAGNOSTIC.md) — diagnostic hook semantics, event catalogue, and instrumentation patterns.

---

## Development

Contributor workflows, make targets, CI automation, and release guidance are documented in [DEVELOPMENT.md](DEVELOPMENT.md).

---

## License

[MIT](LICENSE)
