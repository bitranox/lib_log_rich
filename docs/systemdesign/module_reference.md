# Feature Documentation: Logging Backbone MVP

## Status
Complete

## Links & References
**Feature Requirements:** `docs/systemdesign/konzept.md`, `docs/systemdesign/konzept_architecture.md`  
**Task/Ticket:** Architecture plan `docs/systemdesign/konzept_architecture_plan.md`  
**Related Files:**
- src/lib_log_rich/lib_log_rich.py
- src/lib_log_rich/application/
- src/lib_log_rich/domain/
- src/lib_log_rich/adapters/
- src/lib_log_rich/__main__.py
- src/lib_log_rich/cli.py
- tests/application/test_use_cases.py
- tests/test_runtime.py

## Solution Overview
The MVP introduces a clean architecture layering:
- **Domain layer:** immutable value objects (`LogContext`, `LogEvent`, `LogLevel`, `DumpFormat`) and infrastructure primitives (`RingBuffer`, `ContextBinder`).
- **Application layer:** narrow ports (`ConsolePort`, `StructuredBackendPort`, `GraylogPort`, `DumpPort`, `QueuePort`, `ScrubberPort`, `RateLimiterPort`, `ClockPort`, `IdProvider`, `UnitOfWork`) and use cases (`process_log_event`, `capture_dump`, `shutdown`).
- **Adapters layer:** concrete implementations for Rich console rendering, journald, Windows Event Log, Graylog GELF, dump exporters (text/JSON/HTML), queue orchestration, scrubbing, and rate limiting.
- **Public façade:** `lib_log_rich.init()` wires the dependencies, `get()` returns logger proxies, `bind()` manages contextual metadata, `dump()` exports history, and `shutdown()` tears everything down. Legacy helpers (`hello_world`, `i_should_fail`, `summary_info`) remain for compatibility.
- **CLI:** `lib_log_rich.cli` wraps rich-click with `lib_cli_exit_tools` so the `lib_log_rich` command exposes `info`, `hello`, `fail`, and `logdemo` subcommands plus a `--traceback/--no-traceback` toggle. `python -m lib_log_rich` delegates to the same adapter which prints the metadata banner when no subcommand is given. `logdemo` continues to preview every console theme, printing level→style mappings and, when requested, rendering dumps via `--dump-format`/`--dump-path` while honouring the Graylog/journald/Event Log flags (`--enable-graylog`, `--graylog-endpoint`, `--graylog-protocol`, `--graylog-tls`, `--enable-journald`, `--enable-eventlog`).

## Architecture Integration
**App Layer Fit:**
- Domain objects remain pure and I/O free.
- Application use cases orchestrate ports, rate limiting, scrubbing, and queue hand-off.
- Adapters implement the various sinks, handle platform quirks, and remain opt-in via configuration flags passed to `init()`.
- The public API (`init`, `bind`, `get`, `dump`, `shutdown`) is the composition root for host applications.

**Data Flow:**
1. Host calls `lib_log_rich.init(service=..., environment=...)` which constructs the ring buffer, adapters, and queue.
2. Application code wraps execution inside `with lib_log_rich.bind(job_id=..., request_id=...):` and retrieves a logger via `lib_log_rich.get("package.component")`.
3. Logger methods (`debug/info/warning/error/critical`) send structured payloads to `process_log_event`.
4. `process_log_event` scrubs sensitive fields, enforces rate limits, appends to the ring buffer, and either pushes to the queue (multiprocess mode) or fans out immediately.
5. Queue workers call the same fan-out function, emitting to Rich console, journald, Windows Event Log, and Graylog (if enabled).
6. `lib_log_rich.dump(dump_format=...)` materialises the ring buffer via the dump adapter (text, JSON, or HTML) and optionally writes to disk.
7. `lib_log_rich.shutdown()` drains the queue, flushes Graylog, persists the ring buffer (if configured), and clears global state.

## Core Components

### Public API (`src/lib_log_rich/lib_log_rich.py`)
- **init(...)** – configures the runtime (service, environment, thresholds, queue, adapters, scrubber patterns, console colour overrides, rate limits, diagnostic hook, optional `ring_buffer_size`). Must be called before logging.
- **get(name)** – returns a `LoggerProxy` exposing `debug/info/warning/error/critical` methods that call the process use case.
- **bind(**fields)** – context manager wrapping `ContextBinder.bind()` for request/job/user metadata.
- **dump(dump_format="text", path=None, level=None, text_format=None, color=False)** – exports the ring buffer via `DumpAdapter`. Supports minimum-level filtering, custom text templates, optional ANSI colouring (text format only), and still returns the rendered payload even when writing to `path`.
- **shutdown()** – drains the queue (if any), awaits Graylog flush, flushes the ring buffer, and drops the global runtime.
- **hello_world(), i_should_fail(), summary_info()** – legacy helpers retained for docs/tests.
- **logdemo(*, theme="classic", service=None, environment=None, dump_format=None, dump_path=None, color=None, enable_graylog=False, graylog_endpoint=None, graylog_protocol="tcp", graylog_tls=False, enable_journald=False, enable_eventlog=False)** – spins up a short-lived runtime with the selected palette, emits one sample per level, can render dumps (text/JSON/HTML), and reports which external backends were requested via the returned `backends` mapping so manual invocations can confirm Graylog/journald/Event Log connectivity.
- **Logger `extra` payload** – per-event dictionary copied to all sinks (console, journald, Windows Event Log, Graylog, dumps) after scrubbing.

### Domain Layerer (`src/lib_log_rich/domain/`)
- **LogLevel (Enum)** – canonical levels with severity strings, logging numerics, and icon metadata.
- **LogContext (dataclass)** – immutable context (service, environment, job/job_id, request_id, user identifiers, user name, short hostname, process id, bounded `process_id_chain`, trace/span, extra). Validates mandatory fields, normalises PID chains (max depth eight), and offers serialisation helpers for subprocess propagation.
- **ContextBinder** – manages a stack of `LogContext` instances using `contextvars`; supports serialisation/deserialisation for multi-process propagation.
- **LogEvent (dataclass)** – immutable log event (event_id, timestamp, logger_name, level, message, context, extra, exc_info). Validates timezone awareness and non-empty messages.
- **DumpFormat (Enum)** – allowed dump formats (text, json, html) with friendly parsing via `.from_name()`.
- **RingBuffer** – fixed-size event buffer with optional JSONL checkpoint, snapshot, flush, and property-based FIFO guarantees.

### Application Layer
- **Ports (Protocols)** – console, structured backend, Graylog, dump, queue, rate limiter, scrubber, clock, id provider, unit of work.
- **Use Cases:**
  - `create_process_log_event(...)` – orchestrates scrubbing, rate limiting, ring-buffer append, queue hand-off, and fan-out. Emits diagnostic hooks (`rate_limited`, `queued`, `emitted`).
  - `create_capture_dump(...)` – snapshots the ring buffer and delegates to the configured `DumpPort`.
  - `create_shutdown(...)` – async shutdown function that stops the queue, flushes Graylog, and flushes the ring buffer when requested.

### Adapters Layer (`src/lib_log_rich/adapters/`)
- **RichConsoleAdapter** – uses Rich to render events with icons/colour, honours `console_styles` overrides (code or `LOG_CONSOLE_STYLES`), and falls back gracefully when colour is disabled or unsupported. Built-in palettes (`classic`, `dark`, `neon`, `pastel`) power the `logdemo` preview.
- **JournaldAdapter** – uppercase field mapping and syslog-level conversion for `systemd.journal.send`.
- **WindowsEventLogAdapter** – wraps `win32evtlogutil.ReportEvent`, mapping log levels to configurable event IDs and types.
- **GraylogAdapter** – GELF client supporting TCP (optional TLS) or UDP transports with host/port configuration, persistent TCP sockets (with automatic reconnect on failure), and validation protecting unsupported TLS/UDP combos.
- **DumpAdapter** – renders ring buffer snapshots to text, JSON, or HTML; honours minimum level filters, custom text templates, optional colourisation, writes to disk when `path` is provided, and flushes the ring buffer after successful dumps.
- **QueueAdapter** – thread-based queue with configurable worker, drain semantics, and `set_worker` for late binding; decouples producers from I/O-heavy adapters.
- **RegexScrubber** – redacts string fields using configurable regex patterns (defaults mask `password`, `secret`, `token`).
- **SlidingWindowRateLimiter** – per `(logger, level)` sliding-window throttling with configurable window and max events, enforcing the `konzept_architecture_plan.md` rate-limiting policy.

### CLI (`src/lib_log_rich/__main__.py`)
- Supports `--hello`/`--version` flags on the root command plus the `logdemo` subcommand. `logdemo` loops through the configured palettes, emits sample events, and either prints the rendered dump (text/JSON/HTML) or writes per-theme files (naming pattern `logdemo-<theme>.<ext>`).

## Implementation Details
**Dependencies:**
- Runtime deps: `rich` (console rendering).
- Optional runtime: Graylog (TCP), journald (systemd), Windows Event Log (pywin32) – activated via configuration flags.
- Development deps expanded to cover `hypothesis` (property tests) and `import-linter` (architecture gate).

**Key Configuration:**
- `init` flags: `queue_enabled`, `enable_ring_buffer`, `enable_journald`, `enable_eventlog`, `enable_graylog`, `force_color`, `no_color`, `console_styles`, `text_format`, `scrub_patterns`, `rate_limit`, `diagnostic_hook` (journald is auto-disabled on Windows; Windows Event Log is auto-disabled on non-Windows hosts). `scrub_patterns` honours `LOG_SCRUB_PATTERNS` (comma-separated `field=regex`) and `text_format` honours `LOG_DUMP_TEXT_FORMAT`.
- Diagnostic hook receives tuples `(event_name, payload)` and intentionally swallows its own exceptions to avoid feedback loops.
- Queue worker uses the same fan-out closure as synchronous execution to guarantee consistent behaviour.

**Database Changes:** None.

## Testing Approach
**Automated tests:**
- Domain invariants and serialisation (`tests/domain/`).
- Port contract tests (`tests/application/test_ports_contracts.py`).
- Use-case behaviour incl. rate limiter, queue wiring, dump integration, diagnostic hook (`tests/application/test_use_cases.py`).
- Adapter-specific behaviour (`tests/adapters/`), including snapshot tests and fake backends.
- Public API flow and CLI smoke tests (`tests/test_runtime.py`, `tests/test_basic.py`, `tests/test_scripts.py`).
- Property-based FIFO guarantee for the ring buffer via `hypothesis`.

**Edge cases covered:**
- Missing context raises runtime error.
- Rate-limited events do not enter the ring buffer and emit diagnostic events.
- Queue drain semantics guarantee no event loss.
- Dump adapters handle path-less invocations and file writes.
- CLI handles version, hello, and dump scenarios without leaving global state initialised.

## Known Issues & Future Improvements
**Limitations:**
- Journald and Windows Event Log adapters rely on platform-specific libraries; they remain opt-in and untested on CI by default.
- Graylog adapter now reuses a persistent TCP socket between events and reconnects automatically when the peer closes the connection.
- No HTML templating theme selection yet; the HTML dump is intentionally minimal.

**Future Enhancements:**
- Structured diagnostic metrics (RED style) and integration with OpenTelemetry exporters.
- Pluggable scrubber/rate-limiter policies loaded from configuration objects or environment variables.
- Propagate `process_id_chain` across spawn-based workers automatically; today each process appends its own PID and the chain depth is capped at eight entries.
- Text dump placeholders mirror `str.format` keys exposed by the `dump` API: `timestamp` (ISO8601 UTC), `level`, `logger_name`, `event_id`, `message`, `user_name`, `hostname`, `process_id`, plus the full `context` and `extra` dictionaries.
- Additional adapters (e.g., GELF UDP, S3 dumps) and richer CLI commands.

## Risks & Considerations
- Misconfiguration can initialise adapters that are unavailable on the host (journald, Windows Event Log). The façade defaults keep them disabled unless explicitly requested.
- Diagnostic hooks must remain side-effect safe; they deliberately swallow exceptions to avoid recursive logging loops.
- Queue runs on a daemon thread; hosts should call `shutdown()` during process teardown to avoid losing buffered events.

## Documentation & Resources
- Updated README usage examples.
- CLI help (`lib_log_rich --help`).
- System design documents linked above.

---
**Created:** 2025-09-23 by GPT-5 Codex  
**Last Updated:** 2025-09-24 by GPT-5 Codex  
**Review Date:** 2025-12-23
