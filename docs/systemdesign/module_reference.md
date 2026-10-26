# Feature Documentation: Logging Backbone MVP

## Status
Complete

## Links & References
**Feature Requirements:** `docs/systemdesign/concept.md`, `docs/systemdesign/concept_architecture.md`  
**Task/Ticket:** Architecture plan `docs/systemdesign/concept_architecture_plan.md`  
**Related Files:**
- src/lib_log_rich/lib_log_rich.py
- src/lib_log_rich/application/
- src/lib_log_rich/domain/
- src/lib_log_rich/adapters/
- src/lib_log_rich/__main__.py
- src/lib_log_rich/cli.py
- tests/application/test_use_cases.py
- tests/runtime/test_runtime_poetics.py

## Solution Overview
The MVP introduces a clean architecture layering:
- **Domain layer:** immutable value objects (`LogContext`, `LogEvent`, `LogLevel`, `DumpFormat`) and infrastructure primitives (`RingBuffer`, `ContextBinder`).
- **Application layer:** narrow ports (`ConsolePort`, `StructuredBackendPort`, `GraylogPort`, `DumpPort`, `QueuePort`, `ScrubberPort`, `RateLimiterPort`, `ClockPort`, `IdProvider`) and use cases (`process_log_event`, `capture_dump`, `shutdown`).
- **Adapters layer:** concrete implementations for Rich console rendering, journald, Windows Event Log, Graylog GELF, dump exporters (text/JSON/HTML), queue orchestration, scrubbing, and rate limiting. Queue-backed console adapters (threaded + asyncio) stream Rich-rendered lines into queues via the public `console_adapter_factory` so GUIs, SSE feeds, and tests can subscribe without monkey-patching.
- **Public façade:** `lib_log_rich.init(RuntimeConfig(...))` wires the dependencies, `getLogger()` returns logger proxies, `bind()` manages contextual metadata, `dump()` exports history, and `shutdown()` tears everything down. Quick smoke-test helpers (`hello_world`, `i_should_fail`, `summary_info`) provide fast verification without composing a full runtime.
- **Stdlib bridge:** `StdlibLoggingHandler` plus `attach_std_logging(...)` (import from `lib_log_rich.runtime`) forward existing `logging` module usage into the same processing pipeline, carry across `extra` payloads and call-site metadata (`pathname`, `lineno`, `funcName`), and short-circuit records originating from `lib_log_rich` to prevent recursion.
- **CLI:** `lib_log_rich.cli` wraps rich-click with `lib_cli_exit_tools` so the `lib_log_rich` command exposes `info`, `hello`, `fail`, and `logdemo` subcommands plus global toggles for `--traceback`, `--use-dotenv`, and console formatting. Entry points (`python -m lib_log_rich`, `lib_log_rich`, `scripts/run_cli.py`) exist for quick sanity checks - preview palettes, verify presets/templates, or exercise journald/Event Log/Graylog adapters. `logdemo` previews every theme, prints level→style mappings, reports backend destinations when Graylog/journald/Event Log are enabled, and renders optional dumps via `--dump-format`/`--dump-path` while honouring backend flags (`--enable-graylog`, `--graylog-endpoint`, `--graylog-protocol`, `--graylog-tls`, `--enable-journald`, `--enable-eventlog`). The root-level `--console-format-template` flows into `logdemo` via the Click context (a `--console-format-template` on `logdemo` itself wins); the root-level `--console-format-preset` and `--queue-stop-timeout` are accepted and stored, but no subcommand reads them.

## Architecture Integration
**App Layer Fit:**
- Domain objects remain pure and I/O free.
- Application use cases orchestrate ports, rate limiting, scrubbing, and queue hand-off.
- Adapters implement the various sinks, handle platform quirks, and remain opt-in via configuration flags passed to `init()`.
- The public API (`init`, `validate_config`, `bind`, `getLogger`, `dump`, `shutdown`) is the composition root for host applications.

**Data Flow:**
1. Host calls `lib_log_rich.init(RuntimeConfig(service=..., environment=...))` which constructs the ring buffer, adapters, and queue.
2. Application code wraps execution inside `with lib_log_rich.bind(job_id=..., request_id=...):` and retrieves a logger via `lib_log_rich.getLogger("package.component")`.
3. Logger methods (`debug/info/warning/error/critical`) send structured payloads to `process_log_event`.
4. `process_log_event` scrubs sensitive fields, enforces rate limits, appends to the ring buffer, and either pushes to the queue (when the worker thread is enabled) or fans out immediately.
5. Queue workers call the same fan-out function, emitting to Rich console, journald, Windows Event Log, and Graylog (if enabled).
6. `lib_log_rich.dump(dump_format=...)` materialises the ring buffer via the dump adapter (text, JSON, or HTML) and optionally writes to disk.
7. `lib_log_rich.shutdown()` drains the queue, flushes Graylog, flushes the ring buffer to its checkpoint (a no-op unless `ring_buffer_checkpoint` (env `LOG_RING_BUFFER_CHECKPOINT`) names a file), and clears global state.

## Core Components

### Public API (`src/lib_log_rich/lib_log_rich.py`)
- **init(...)** - configures the runtime (service, environment, thresholds, queue, adapters, scrubber patterns, console colour overrides, optional `console_adapter_factory`, rate limits, diagnostic hook, optional `ring_buffer_size`). Must be called before logging.
- **validate_config(config)** - refuses `config` exactly as `init` would (same `ValueError`, same message) without starting a runtime or touching a running one. Both go through settings resolution, which parses levels and refuses unknown level names, uncompilable scrub patterns, invalid `LOG_*` enum values (`LOG_CONSOLE_THEME`, `LOG_CONSOLE_STREAM`, `LOG_CONSOLE_FORMAT_PRESET`, `LOG_DUMP_FORMAT_PRESET`, `LOG_GRAYLOG_PROTOCOL`; the message names the variable), and console style keys that are not level names (only when the built-in console is used), so the two cannot disagree. Enum fields (`console_theme`, `console_stream`, `console_format_preset`, `dump_format_preset`, `graylog_protocol`, `queue_full_policy`) and `payload_limits` are checked earlier still: an unknown value raises pydantic `ValidationError` (a `ValueError` subclass) from `RuntimeConfig(...)` itself.
- **getLogger(name)** - returns a `LoggerProxy` exposing the stdlib-compatible level helpers (`debug/info/warning/error/critical/exception`), `.log(level, msg, *args, exc_info=None, stack_info=None, stacklevel=1, extra=None)`, and `.setLevel(level)`. Messages are formatted inside the process pipeline, `exc_info`/`stack_info` payloads flow through to every adapter, and `stacklevel` is accepted for API parity but ignored today. `.exception(...)` logs at `LogLevel.ERROR` and defaults `exc_info` to `True`, matching the standard library. `.setLevel(...)` mutates only the console threshold; structured backends, Graylog, and queues keep their configured levels.
- **bind(*, service=None, environment=None, job_id=None, request_id=None, user_id=None, user_name=None, hostname=None, process_id=None, process_id_chain=None, trace_id=None, span_id=None, extra=None)** - context manager wrapping `ContextBinder.bind()` for request/job/user metadata; yields the bound `LogContext`. Only these keyword-only fields are accepted (an unknown keyword raises `TypeError` naming `bind()`); anything else goes in `extra`, e.g. `bind(extra={"order_id": order_id})`.
- **dump(*, dump_format="text", path=None, level=None, console_format_preset=None, console_format_template=None, theme=None, console_styles=None, context_filters=None, context_extra_filters=None, extra_filters=None, color=False)** - exports the ring buffer via `DumpAdapter`. `dump_format` takes a `DumpFormat` member or its value (`"text"`, `"json"`, `"html_table"`, `"html_txt"`); `console_format_preset` takes a `FormatPreset` member and `theme` a `ConsoleTheme` member (both from `lib_log_rich.domain`). Supports minimum-level filtering, preset/template-controlled text formatting (template wins; the `text` format refuses `FormatPreset.SHORT_LOC_ICON` with `ValueError: Unknown text dump preset: 'short_loc_icon'`); `theme` and `console_styles` let callers reuse or override the runtime palette for coloured text dumps, `color` toggles ANSI emission (text format only), and filter mappings limit results by context/extra fields before formatting. The rendered payload is returned even when persisted to `path`.
- **attach_std_logging(logger=None, handler_level=None, logger_level=get_minimum_log_level(), propagate=False)** / **StdlibLoggingHandler** - installs a stdlib `logging.Handler` that normalises `LogRecord` inputs (message + args, `exc_info`, `stack_info`, `stacklevel`, `extra`) into the runtime pipeline. Defaults `logger_level` to `get_minimum_log_level()` so events aren't pre-filtered, and `propagate=False` to prevent duplicate emission. Location metadata (`pathname`, `lineno`, `funcName`, plus unmodified `extra` payloads) is preserved so dumps, Graylog, and Rich console output reflect the original call site. Records carrying `extra={"lib_log_rich_skip": True}` are dropped; records originating from `lib_log_rich` stay out of the pipeline to prevent recursion and, when only bridges would see them, go to `logging.lastResort` (stderr, WARNING and above) so an adapter failure is never silent, and non-standard levels fall back to `INFO` while the original `levelno`/`levelname` are retained in `event.extra`.
- **shutdown()** - drains the queue (if any), flushes console streams, awaits Graylog flush, flushes the ring buffer to its checkpoint, and drops the global runtime. Without `ring_buffer_checkpoint` (env `LOG_RING_BUFFER_CHECKPOINT`) there is no checkpoint and the ring-buffer flush does nothing.
- **flush(timeout=None, *, flush_ring_buffer=False)** - drains queues and flushes all adapters (console, Graylog) **without** terminating the runtime. Unlike `shutdown()`, logging remains active after this call. Raises `TimeoutError` if the queue doesn't drain within `timeout` (default: 5.0s). `flush_ring_buffer=True` flushes the ring buffer to its checkpoint; without `ring_buffer_checkpoint` (env `LOG_RING_BUFFER_CHECKPOINT`) there is no checkpoint, so it does nothing and the buffer keeps its events. Raises `RuntimeError` if called from within an active event loop; use `flush_async()` instead.
- **flush_async(timeout=None, *, flush_ring_buffer=False)** - async variant of `flush()`. Awaitable from async contexts. Same behaviour: drains queue, flushes adapters, keeps runtime active.
- **hello_world(), i_should_fail(), summary_info()** - quick verification helpers kept for smoke tests and docs.
- **logdemo(*, theme=ConsoleTheme.CLASSIC, service=None, environment=None, dump_format=None, dump_path=None, color=None, console_format_preset=None, console_format_template=None, dump_format_preset=None, dump_format_template=None, context_filters=None, context_extra_filters=None, extra_filters=None, enable_graylog=False, graylog_endpoint=None, graylog_protocol=GraylogProtocol.TCP, graylog_tls=False, enable_journald=False, enable_eventlog=False)** - spins up a short-lived runtime with the selected palette, emits one sample per level, can render dumps (`dump_format` takes a `DumpFormat` member; the presets take `FormatPreset` members), and returns a `LogDemoResult` (`theme`, `styles`, `events`, `dump`, `service`, `environment`, `backends`) whose `backends` (`BackendStatus` with `graylog`, `journald`, `eventlog` flags) reports which external backends were requested so manual invocations can confirm Graylog/journald/Event Log connectivity.
- **Logger `extra` payload** - per-event dictionary copied to all sinks (console, journald, Windows Event Log, Graylog, dumps) after scrubbing.

### Domain Layer (`src/lib_log_rich/domain/`)
- **LogLevel (Enum)** - canonical levels with severity strings, logging numerics, four-character formatter codes, and icon metadata.
- **LogContext (dataclass)** - immutable context (service, environment, job/job_id, request_id, user identifiers, user name, short hostname, process id, bounded `process_id_chain`, trace/span, extra). Validates mandatory fields, normalises PID chains (max depth eight), and offers serialisation helpers for subprocess propagation.
- **ContextBinder** - manages a stack of `LogContext` instances using `contextvars`; `serialize()` returns a `ContextSnapshot` and `deserialize(snapshot)` / `seed(context)` install one in another process. The JSON text form for boundaries that carry only text lives in `lib_log_rich.adapters.context_snapshot`.
- **ContextSnapshot (dataclass)** - immutable `version` plus `stack` of `LogContext` frames produced by `ContextBinder.serialize()`; picklable, so `multiprocessing` arguments carry it as is.
- **LogEvent (dataclass)** - immutable log event (event_id, timestamp, logger_name, level, message, context, extra, exc_info, stack_info). Validates timezone awareness and non-empty messages. The stdlib bridge populates location metadata (`pathname`, `lineno`, `funcName`) inside `extra` so adapters and dumps can display the originating call site without mutating the dataclass signature.
- **DumpFormat (Enum)** - allowed dump formats (text, json, html_table, html_txt) with friendly parsing via `.from_name()`.
- **Option enums (`domain/enums.py`)** - `ConsoleTheme`, `FormatPreset`, `ConsoleStream`, `ExportStyle`, `GraylogProtocol`, `QueuePolicy`, `DropReason`, and `DiagnosticEvent`, all `StrValueEnum` subclasses, so `str()` and f-strings give the value. Public functions take the members; `RuntimeConfig`, `LOG_*` variables, and the CLI also parse the value strings.
- **EventCheckpoint (Protocol)** - the persistence port a `RingBuffer` hydrates from (`load() -> Sequence[LogEvent]`) and flushes to (`append(events)`); `lib_log_rich.adapters.JsonlCheckpoint` implements it.
- **RingBuffer** - fixed-size event buffer (`RingBuffer(max_events=..., checkpoint=None)`) with an optional `EventCheckpoint`, snapshot, flush, and property-based FIFO guarantees. `init()` gives its buffer a `JsonlCheckpoint` when `ring_buffer_checkpoint` (env `LOG_RING_BUFFER_CHECKPOINT`) names a file.

### Application Layer
- **Ports (Protocols)** - console, structured backend, Graylog, dump, queue, rate limiter, scrubber, clock, id provider, system identity, unit of work.
- **Use Cases:**
  - `create_process_log_event(...)` - orchestrates scrubbing, rate limiting, ring-buffer append, queue hand-off, and fan-out. Emits diagnostic hooks (`rate_limited`, `queued`, `queue_full`, `queue_dropped`, `queue_worker_error`, `queue_drop_callback_error`, `queue_shutdown_timeout`, `emitted`).
  - `create_capture_dump(...)` - snapshots the ring buffer and delegates to the configured `DumpPort`.
  - `create_shutdown(...)` - async shutdown function that stops the queue, flushes console/Graylog/ring buffer, and clears the runtime.
  - `create_flush(...)` - async flush function that drains the queue (without stopping it), flushes console/Graylog adapters, and optionally flushes the ring buffer to its checkpoint (a no-op unless `ring_buffer_checkpoint` (env `LOG_RING_BUFFER_CHECKPOINT`) gave the buffer one). Runtime remains active after flush completes.

### Adapters Layer (`src/lib_log_rich/adapters/`)
- **RichConsoleAdapter** - uses Rich to render events with icons/colour, honours `console_styles` overrides (code or `LOG_CONSOLE_STYLES`), and falls back gracefully when colour is disabled or unsupported. Built-in palettes (`classic`, `dark`, `neon`, `pastel`) power the `logdemo` preview.
- **QueueConsoleAdapter / AsyncQueueConsoleAdapter** - feed ANSI or HTML-rendered console lines into thread-safe or asyncio queues. They reuse the Rich console formatter and level gate so appearance and thresholds stay consistent. Ideal for Textual panes, SSE/WebSocket streaming, or tests; wired via `console_adapter_factory` without monkey-patching. The asyncio variant exposes an `on_drop` hook and logs a warning whenever the queue is full, so segment loss is observable instead of silent. Because `asyncio.Queue` is not thread-safe, it remembers the loop that owns the queue (running at construction, or `loop=`) and hands segments emitted on the runtime's queue worker thread to that loop with `call_soon_threadsafe`.
- **JournaldAdapter** - uppercase field mapping and syslog-level conversion for `systemd.journal.send`.
- **WindowsEventLogAdapter** - wraps `win32evtlogutil.ReportEvent`, mapping log levels to configurable event IDs and types.
- **GraylogAdapter** - GELF client supporting TCP (optional TLS) or UDP transports with host/port configuration, persistent TCP sockets (with automatic reconnect on failure), and validation protecting unsupported TLS/UDP combos.
- **DumpAdapter** - renders ring buffer snapshots to text, JSON, HTML tables, or palette-aware HTML text; honours minimum level filters, preset/template-controlled text formatting (template wins); themes/`console_styles` drive colour for text/HTML text formats, optional colourisation toggles, and writes to disk when `path` is provided. After a successful dump the capture use case flushes the ring buffer to its checkpoint; the buffer `init()` builds has none, so it keeps its events.
- **JsonlCheckpoint (`adapters/checkpoint.py`)** - `EventCheckpoint` over a newline-delimited JSON file (`JsonlCheckpoint(path)`): `append(events)` writes one `LogEvent.to_json()` line per event (creating parent directories), `load()` validates every line into a `CheckpointRecord` before building the event. A missing file loads as empty; a bad line raises `ValueError("Invalid checkpoint <path>, line <n>:")` followed by one `  <field>: <reason>` line per refused field.
- **Context snapshot text (`adapters/context_snapshot.py`)** - `dump_context_snapshot(snapshot) -> str` renders a `ContextSnapshot` as JSON text and `load_context_snapshot(text) -> ContextSnapshot` parses it back through strict private models, refusing unknown keys, wrong types, and `null` PID chains or `extra` with `ValueError("Invalid context snapshot: <location>: <reason>")` (dotted locations such as `stack.0.process_id`, every refused field joined by `; `).
- **Validation rendering (`adapters/_validation.py`)** - private helpers `describe_validation_error` / `validation_error_lines` render a pydantic `ValidationError` as `field: reason` clauses; the settings resolver and `JsonlCheckpoint` share them so their refusals read alike.
- **Formatting utilities (`adapters._formatting`)** - produce the canonical placeholder dictionary shared by the console and dump adapters so presets, custom templates, and documentation reference the same payload.
- **QueueAdapter** - thread-based queue with configurable worker, capacity (`queue_maxsize`), full-policy (`QueuePolicy.BLOCK` vs `QueuePolicy.DROP`), drop diagnostics, `worker_failed` health flag, configurable auto-reset cooldown, `queue_put_timeout` defaulting to 1 second, and `set_worker` for late binding. When the worker stays in a failed state the adapter automatically shifts blocking producers into drop mode so application threads do not hang; shutdown treats joins transactionally and raises `queue_shutdown_timeout` diagnostics whenever the worker fails to stop within the configured deadline.
- **RegexScrubber** - redacts string fields using configurable regex patterns (defaults mask `password`, `secret`, `token`) across both event `extra` payloads and `LogContext.extra`, keeping the original objects immutable for caller introspection.
- **SlidingWindowRateLimiter** - per `(logger, level)` sliding-window throttling with configurable window and max events, enforcing the `concept_architecture_plan.md` rate-limiting policy.

### CLI (`src/lib_log_rich/cli.py`, `src/lib_log_rich/__main__.py`)
- Supports `--hello`/`--version` flags on the root command plus the `logdemo` subcommand. `logdemo` loops through the configured palettes, emits sample events, and either prints the rendered dump (text/JSON/HTML_TABLE/HTML_TXT) or writes one file per preset-theme combination (naming pattern `logdemo-<preset>-<theme>.<ext>` in a directory). `python -m lib_log_rich` runs `cli.main()`, the same function the console script runs.

## Implementation Details
**Dependencies:**
- Runtime deps: `rich` (console rendering).
- Optional runtime: Graylog (TCP), journald (systemd), Windows Event Log (pywin32) - activated via configuration flags.
- Development deps expanded to cover `hypothesis` (property tests) and `import-linter` (architecture gate).

**Key Configuration:**
- `init` flags: `queue_enabled`, `queue_maxsize`, `queue_full_policy`, `queue_put_timeout`, `queue_stop_timeout`, `enable_ring_buffer`, `enable_journald`, `enable_eventlog`, `enable_graylog`, `force_color`, `no_color`, `console_styles`, `console_format_preset`, `console_format_template`, `console_stream` (`stdout`/`stderr`/`both`/`custom`/`none`), `console_stream_target`, `dump_format_preset`, `dump_format_template`, `graylog_level`, `scrub_patterns`, `rate_limit`, `diagnostic_hook` (journald auto-disables on Windows; Event Log auto-disables on non-Windows hosts). Environment overrides mirror each option (e.g., `LOG_QUEUE_MAXSIZE`, `LOG_QUEUE_FULL_POLICY`, `LOG_QUEUE_PUT_TIMEOUT`, `LOG_QUEUE_STOP_TIMEOUT`, `LOG_CONSOLE_FORMAT_PRESET`, `LOG_CONSOLE_FORMAT_TEMPLATE`, `LOG_CONSOLE_STREAM`, `LOG_DUMP_FORMAT_PRESET`, `LOG_DUMP_FORMAT_TEMPLATE`, `LOG_GRAYLOG_LEVEL`).
- Diagnostic hook receives tuples `(event_name, payload)` and intentionally swallows its own exceptions to avoid feedback loops.
- Queue worker uses the same fan-out closure as synchronous execution to guarantee consistent behaviour.

**Database Changes:** None.

## Testing Approach
**Automated tests:**
- Domain invariants and serialisation (`tests/domain/`).
- Port contract tests (`tests/application/test_ports_contracts.py`).
- Use-case behaviour incl. rate limiter, queue wiring, dump integration, diagnostic hook (`tests/application/test_use_cases.py`).
- Adapter-specific behaviour (`tests/adapters/`), including snapshot tests and fake backends.
- Public API flow and CLI smoke tests (`tests/runtime/test_runtime_poetics.py`, `tests/test_basic.py`, `tests/test_scripts.py`).
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
- Graylog adapter reuses a persistent TCP socket between events and reconnects automatically when the peer closes the connection.
- No HTML templating theme selection yet; the HTML dump is intentionally minimal.

**Future Enhancements:**
- Structured diagnostic metrics (RED style) and integration with OpenTelemetry exporters.
- Pluggable scrubber/rate-limiter policies loaded from configuration objects or environment variables.
- Propagate `process_id_chain` across spawn-based workers automatically; today each process appends its own PID and the chain depth is capped at eight entries.
- Text dump placeholders mirror `str.format` keys exposed by the `dump` API: `timestamp` (ISO8601 UTC), calendar components (`YYYY`, `MM`, `DD`, `hh`, `mm`, `ss`), `level`, `level_code`, `logger_name`, `event_id`, `message`, `user_name`, `hostname`, `process_id`, `process_id_chain`, plus the full `context` dictionary (service, environment, job_id, request_id, user_id, user_name, hostname, process_id, process_id_chain, trace_id, span_id, additional bound fields) and `extra`.
- Additional adapters (e.g., GELF UDP, S3 dumps) and richer CLI commands.
- Extended severity analytics (peak transitions, sliding-window histograms, streak detection) layered atop the new monitor when operators request deeper diagnostics.

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
**Last Updated:** 2026-01-26
**Review Date:** 2026-01-26


## Module Reference Supplements (2025-09-30)

### lib_log_rich.domain.context
* **Purpose:** Maintains execution-scoped metadata via `LogContext` and `ContextBinder`, aligned with the context propagation contract.
* **Key Functions:** `_validate_not_blank`, `ContextBinder.bind/current/serialize/deserialize/seed/replace_top` (documented with doctests emphasising inheritance, serialisation, and error modes). `ContextBinder.bind()` and `LogContext.merge()` take the typed keyword-only context fields and raise `TypeError` for any other keyword; `serialize()` returns a `ContextSnapshot` and `deserialize()` takes one.
* **Design Hooks:** Maps directly to the "Context & Field Management" rules in `concept_architecture.md`; doctests show binding requirements and round-tripping payloads.

### lib_log_rich.domain.events
* **Purpose:** Encapsulates `LogEvent` value semantics and timestamp normalisation; doctests cover serialisation (`to_dict()`, `to_json()`). Reading stored events back is the job of `lib_log_rich.adapters.JsonlCheckpoint`, which validates each line before building the event.
* **Alignment:** `_ensure_aware` enforces UTC as mandated in the system plan.

### lib_log_rich.domain.ring_buffer
* **Purpose:** Provides bounded retention with optional checkpointing through the `EventCheckpoint` port (`load()` / `append(events)`); the buffer never touches a file itself.
* **Highlights:** `RingBuffer(max_events=..., checkpoint=...)` hydrates from `checkpoint.load()` on construction; doctests demonstrate eviction and persistence paths with an in-memory checkpoint.
* **Flush Behavior:** `RingBuffer.flush()` appends all buffer events to the checkpoint and clears the in-memory buffer. This prevents duplicates since each event is written exactly once. Without a checkpoint, or with nothing appended since the last flush, flush is a no-op (buffer preserved). `init()` gives its buffer a checkpoint only when `ring_buffer_checkpoint` (env `LOG_RING_BUFFER_CHECKPOINT`) names a file; without one `flush(flush_ring_buffer=True)`, shutdown, and the flush after each dump leave it untouched.

### lib_log_rich.domain.analytics
* **Purpose:** Maintains the thread-safe :class:`SeverityMonitor` used for aggregate severity analytics (peak level, per-level counts, threshold totals, drop tracking).
* **Highlights:** Doctests cover record/reset flows and drop bookkeeping (`record_drop(level, reason)` takes a `DropReason`; `drops_by_reason` is keyed by `DropReason`, and a value-string lookup such as `drops_by_reason["queue_full"]` finds the same entry); the monitor exposes read-only snapshots so runtime/reporting code can decide whether to surface log dumps without traversing the ring buffer.

### Application Ports
* **Coverage:** Console, dump, structured, Graylog, queue, scrubber, rate-limiter, clock, ID, and system-identity ports include intent-driven docstrings plus doctests showing `Protocol` compatibility, reinforcing clean architecture boundaries.

### Application Use Cases
* **Process Pipeline:** `create_process_log_event` documents context refresh, message formatting (mirroring `%` interpolation from `logging.Logger`), payload limiting (message clamp, extra/context sanitisation, traceback/stack-info compaction), fan-out sequencing, and diagnostics, including doctests wiring minimal fakes. Logger-level gating happens at the proxy (`LoggerProxy.setLevel(...)`) before events reach the pipeline, while the pipeline itself still enforces per-handler thresholds (console/backend/Graylog). The context helper now lives in `application/use_cases/_pipeline.py` as `refresh_context` and is re-exported via `process_event.refresh_context` for callers that need to synchronise PID/host/user data without rebuilding the full pipeline.
* **Dump & Shutdown:** Capture/Shutdown factories describe side effects (ring buffer flush, queue drain) to mirror operational checklists.

### Adapter Layer
* **Console / Queue / Scrubber / Rate Limiter / Graylog / Journald / Windows Event Log:** Each adapter explains configuration expectations, error handling, and includes doctests for offline validation (e.g., disabled Graylog, queue drain).

### Configuration Helpers (`config.py`)
* **Toggle Strategy:** Module-level docs outline precedence while individual helpers (toggle interpretation, directory search, cached state) include executable examples to demonstrate `.env` discovery semantics.


### lib_log_rich.lib_log_rich
* **Purpose:** Public façade documenting why each entry point exists (`init`, `validate_config`, `bind`, `getLogger`, `dump`, `shutdown`, `logdemo`) and how they map to the architecture.
* **Operational Notes:** Docstrings describe queue/Graylog side effects, provide doctests for toggles, and clarify required invariants (service/environment/job).

### lib_log_rich.cli
* **Purpose:** Presentation adapter exposing the documented commands (`info`, `hello`, `fail`, `logdemo`) with intent-driven docstrings aligned with the system design CLI expectations.
* **Highlights:** Helper functions (_dump_extension, _resolve_dump_path, _parse_graylog_endpoint) explain filename conventions and validation rules; command callbacks document why/what/side-effects for ops scripts.

### lib_log_rich.application.use_cases.dump
* **Purpose:** Bridge the ring buffer with dump adapters, respecting level filters, templates, and colour toggles documented in the dump workflow.
* **Input:** `RingBuffer`, `DumpPort`, default template/preset/theme/style hints supplied during wiring; callers pass dump format, destination path, severity filter, preset/template, theme, style overrides, colour flag.
* **Output:** Returns the rendered dump string and flushes the ring buffer to its checkpoint after successful emission (a no-op, keeping the events, unless `ring_buffer_checkpoint` (env `LOG_RING_BUFFER_CHECKPOINT`) gave the buffer one); raises adapter exceptions when formatting fails.
* **Location:** src/lib_log_rich/application/use_cases/dump.py

### lib_log_rich.adapters.dump
* **Purpose:** Implement the `DumpPort` contract for text, JSON, HTML table, and Rich-coloured HTML outputs.
* **Input:** Snapshot of `LogEvent` instances, dump format, optional path, severity filter, preset/template, theme, style overrides, colour flag.
* **Output:** Returns the rendered payload and writes to disk when `path` is provided; preserves adapter-side invariants (e.g., PID chain column, ANSI suppression when colour disabled).
* **Location:** src/lib_log_rich/adapters/dump.py

### lib_log_rich.adapters.checkpoint
* **Purpose:** Implement the domain `EventCheckpoint` port as a newline-delimited JSON file, validating every stored line at the adapter boundary so a corrupt record never reaches the ring buffer as raw data.
* **Input:** `JsonlCheckpoint(path)`; `append(events)` takes `LogEvent` instances, `load()` reads the file. Each line passes through the `CheckpointRecord` / `CheckpointContext` models (unknown fields, a string where an integer belongs, a naive or out-of-range timestamp, or an unknown level are refused).
* **Output:** `append` writes one line per event, byte for byte `LogEvent.to_json()` (sorted keys, ISO-8601 `+00:00` timestamp, lowercase severity), creating parent directories; `load` returns the events oldest first, an empty list for a missing file, and raises `ValueError("Invalid checkpoint <path>, line <n>:")` with one `  <field>: <reason>` line per refused field.
* **Usage:** `RingBuffer(max_events=..., checkpoint=JsonlCheckpoint(path))`. `init()` builds exactly that when `ring_buffer_checkpoint` (env `LOG_RING_BUFFER_CHECKPOINT`) names a file.
* **Location:** src/lib_log_rich/adapters/checkpoint.py

### lib_log_rich.adapters.context_snapshot
* **Purpose:** JSON text form of a `ContextSnapshot` for process boundaries that carry only text (an environment variable, a pipe, a file). Where the snapshot object itself can travel (`multiprocessing` arguments pickle it), no text form is needed.
* **Input:** `dump_context_snapshot(snapshot: ContextSnapshot)`; `load_context_snapshot(text: str)`.
* **Output:** `dump_context_snapshot` returns `json.dumps` text with every context field present (`null` for unset fields, the PID chain as an array, `extra` as an object); `load_context_snapshot` returns the `ContextSnapshot`, refusing malformed input with `ValueError("Invalid context snapshot: <location>: <reason>")`, every refused field named by its dotted location (`stack.0.process_id`) and joined by `; `, without echoing values.
* **Location:** src/lib_log_rich/adapters/context_snapshot.py

### lib_log_rich.adapters._validation
* **Purpose:** One private renderer for pydantic `ValidationError` refusals, shared by the settings resolver and the JSONL checkpoint so their messages cannot drift apart.
* **Output:** `validation_error_lines(error)` returns one `<dotted.location>: <reason>` line per error; `describe_validation_error(error)` joins them with `; `. Model names, input echoes, and documentation URLs are left out.
* **Location:** src/lib_log_rich/adapters/_validation.py

### lib_log_rich.adapters.scrubber
* **Purpose:** Mask sensitive `extra` fields using configurable regex patterns before fan-out.
* **Input:** Mapping of field name → regex, replacement token; invoked with individual `LogEvent` instances.
* **Output:** Returns a `LogEvent` with redacted extras; the scrubber recurses into nested mappings/sequences/sets and inspects byte payloads to honour the secrecy contract laid out in `concept_architecture.md`.
* **Location:** src/lib_log_rich/adapters/scrubber.py

### lib_log_rich.adapters.structured.journald
* **Purpose:** Emit structured log payloads to systemd-journald using either native bindings or the UNIX-socket fallback.
* **Input:** `LogEvent` instances promoted to uppercase journald fields; optional overrides for the service field.
* **Output:** Calls `systemd.journal.send` when available; otherwise attempts the documented UNIX-socket fallback.
* **Platform Notes:** On hosts without `socket.AF_UNIX` (e.g., Windows runners) the fallback is disabled and callers receive a runtime error instructing them to install `python-systemd` or run on a journald-capable distro. Tests skip in that scenario to keep CI green.
* **Location:** src/lib_log_rich/adapters/structured/journald.py

### lib_log_rich.runtime
* **Purpose:** Façade enforcing the runtime lifecycle (`init`, `getLogger`, `bind`, `dump`, `shutdown`) plus the side-effect-free `validate_config`, while shielding the inner clean-architecture layers.
* **Guard Rails:** `init` raises `RuntimeError` when called twice without an intervening `shutdown` so queue workers and runtime state are never leaked, reflecting the lifecycle rules in `module_reference.md`.
* **Analytics API:** `max_level_seen`, `severity_snapshot`, and `reset_severity_metrics` expose SeverityMonitor data (peak, per-level counts, and drop reasons) so operators can decide when to surface ring-buffer dumps.
* **Helper Functions:** `_build_runtime_snapshot()`, `_build_severity_snapshot()`, `_build_dump_request()`, `_render_dump()`, `_ensure_shutdown_allowed()`, `_shutdown_runtime()`, and `_await_shutdown_result()` keep the façade declarative; each mirrors the responsibilities described in the lifecycle diagrams (snapshotting, filtering, and orderly shutdown).
* **Payload Limits:** `init` exposes `payload_limits` so operators can adjust message, extra, context, and stack-trace bounds enforced in the process pipeline.
* **Stdlib Bridge:** `StdlibLoggingHandler` and `attach_std_logging(...)` live in the runtime layer, transforming `logging.LogRecord` inputs into the `process` callable, preserving call-site metadata and `extra` payloads, and short-circuiting `lib_log_rich`-sourced records (or ones tagged with `lib_log_rich_skip`) to avoid recursion; the library's own warnings fall back to `logging.lastResort` when no real handler would see them.

### lib_log_rich.runtime._composition
* **Purpose:** Document the composition root that wires binders, ring buffers, adapters, and use cases into the `LoggingRuntime` singleton described in the system design plan.
* **Input:** `RuntimeSettings` plus feature flags that toggle queue/Graylog/consoles; helpers consume context binders, scrubbers, rate limiters, clocks, and ID providers.
* **Output:** Returns `LoggingRuntime` instances, queue workers, and helper callables (`process`, `capture_dump`, `shutdown_async`); docstrings spell out queue side effects, diagnostic hooks, and fallback behaviours.
* **Key Helpers:**
  - `_create_severity_monitor()` - seeds `SeverityMonitor` with `DROP_REASON_LABELS` so dashboards consistently chart `DropReason.RATE_LIMITED`, `DropReason.QUEUE_FULL`, and `DropReason.ADAPTER_ERROR` events.
  - `_select_console_adapter()` - honours optional console injection while falling back to the Rich-based default, preserving the clean-architecture boundary.
  - `_create_dump_capture()` - binds dump defaults, themes, and style overrides into a testable callable used by the runtime façade.
  - `_bind_shutdown_callable()` / `_bind_flush_callable()` - bind the async shutdown and flush hooks that drain queues and flush adapters in the documented order.
  - `_create_process_callable()` - produces the application-layer logging pipeline, accepting queue/no-queue variants without leaking adapter details.
  - `_create_queue_adapter()` - builds the queue worker with documented size, policy, and timeout semantics before wiring fan-out handlers.
* **Constants:** `DROP_REASON_LABELS` is a tuple of `DropReason` members (`RATE_LIMITED`, `QUEUE_FULL`, `ADAPTER_ERROR`), the drop-reason vocabulary shared with observability dashboards and design docs.
* **Location:** src/lib_log_rich/runtime/_composition.py

### lib_log_rich.runtime.settings
* **Purpose:** The `lib_log_rich.runtime.settings` package (`models.py`, `resolvers.py`) holds the configuration schemas and helper utilities. Together they blend function arguments, environment defaults, and platform guards (journald vs. Event Log, Graylog endpoints).
* **Input:** Keyword arguments from `init`, environment variables (`LOG_*`), and default scrub patterns.
* **Output:** Typed Pydantic models (`RuntimeSettings`, `FeatureFlags`, `ConsoleAppearance`, `DumpDefaults`, `GraylogSettings`, `PayloadLimits`) plus helper functions documenting edge cases (rate limit parsing, console style merges). Optional `console_factory` entries carry injected `ConsolePort` implementations (queue adapters, HTML renderers) to the composition root.
* **Refusals:** settings resolution refuses everything `init` refuses, so `build_runtime` never meets a configuration error. `resolve_levels` parses the three thresholds into `LogLevel` and names the field or `LOG_*_LEVEL` variable on refusal; `resolve_console`, `resolve_dump_defaults`, and `resolve_graylog` parse `LOG_CONSOLE_THEME`, `LOG_CONSOLE_STREAM`, `LOG_CONSOLE_FORMAT_PRESET`, `LOG_DUMP_FORMAT_PRESET`, and `LOG_GRAYLOG_PROTOCOL` into their enums and name the variable on refusal (`LOG_GRAYLOG_PROTOCOL: Invalid Graylog protocol: ...`); an invalid `LOG_QUEUE_FULL_POLICY` falls back to the configured policy. `RuntimeSettings` compiles scrub patterns and, when `console_factory` is `None`, checks every console style key against the level names. A `ValidationError` from any settings model is rendered as `field: reason` clauses joined by `; ` (`adapters._validation.describe_validation_error`). `init` and `validate_config` both call `_api._resolve_settings`, which adds the `Invalid runtime settings:` prefix.
* **RuntimeConfig field parsing:** `console_theme` (`ConsoleTheme | None`), `console_stream` (`ConsoleStream`), `console_format_preset` / `dump_format_preset` (`FormatPreset | None`), `graylog_protocol` (`GraylogProtocol`), and `queue_full_policy` (`QueuePolicy`) also accept their value strings in any case with surrounding space (a blank preset or theme means `None`); `payload_limits` accepts a mapping, validated into `PayloadLimits`. An unknown value raises `ValidationError` from `RuntimeConfig(...)` itself, one `<field>` entry per refused field.
* **TOML Compatibility Validators:** `RuntimeConfig` includes two Pydantic validators that normalise edge-case inputs from TOML files and environment variables:
  - `_empty_str_as_none` (mode=`before`) - coerces empty or whitespace-only strings to `None` for `console_format_template` and `dump_format_template`, so TOML files with `console_format_template = ""` produce the same behaviour as omitting the key entirely.
  - `_empty_seq_as_none` (mode=`before`) - coerces empty lists/tuples to `None` for `graylog_endpoint` and `rate_limit`, so `graylog_endpoint = []` in TOML is equivalent to `None`.
* **Location:** src/lib_log_rich/runtime/settings/ (`models.py`, `resolvers.py`).

### lib_log_rich.adapters.console.rich_console
* **Purpose:** Rich-backed console adapter that honours presets, templates, themes, and explicit style overrides highlighted in `CONSOLESTYLES.md`.
* **Input:** `LogEvent` instances with optional colourisation flag, runtime style maps, and format presets/templates.
* **Output:** Prints formatted lines to Rich consoles; docstrings enumerate fallback rules and failure handling for malformed templates.
* **Location:** src/lib_log_rich/adapters/console/rich_console.py

### lib_log_rich.application.ports.time
* **Purpose:** Define protocol contracts for clocks, ID providers, and unit-of-work execution to keep application logic decoupled from infrastructure time/transaction providers.
* **Input:** Implementations supply `now()`, `__call__()`, or `run()` semantics injected via the composition root.
* **Output:** Protocols consumed throughout the runtime; doctests demonstrate stub implementations used in examples and tests.
* **Location:** src/lib_log_rich/application/ports/time.py

### lib_log_rich.config
* **Purpose:** Expose opt-in helpers for loading `.env` files (`enable_dotenv(search_from=None, *, markers=..., dotenv_override=False)`) and interpreting dotenv toggles so configuration precedence stays explicit.
* **Input:** File system roots, environment values, CLI flags.
* **Output:** Applies dotenv side effects (environment variables) when enabled; exposes pure helpers returning booleans or paths.
* **Location:** src/lib_log_rich/config.py

### lib_log_rich.cli
* **Purpose:** Present the public façade (`info`, `hello`, `fail`, `logdemo`) through a rich-click command surface that mirrors documented operational flows.
* **Input:** CLI arguments/environment toggles; interacts with `lib_cli_exit_tools` for traceback configuration.
* **Output:** Prints metadata banners, greetings, error traces, or demo output; returns process exit codes for automation.
* **Location:** src/lib_log_rich/cli.py

### scripts._utils
* **Purpose:** Shared helpers that power automation scripts (`make test`, `make build`, CLI wrappers) documented in `DEVELOPMENT.md`.
* **Highlights:** Utilities centralise bootstrap logic for lint/type/test flows and keep script entry points narrowly focused on orchestration.
* **Location:** scripts/_utils.py


### lib_log_rich.cli_stresstest
* **Purpose:** Textual-based stress harness that mirrors runtime composition and lets engineers validate presets, scrubbers, and dump filters before deployment.
* **Key Functions:** `_parse_config` normalises widget values into `RunConfig`; `_create_app_class` builds the lazy Textual app; `_parse_dump_filters` and sibling helpers enforce the filtering grammar; `_collect_values` reads every settings row once into a typed `_FormValues` snapshot, the only place user-typed text enters the TUI.
* **Design Hooks:** Implements the "Stress Harness" capability earmarked in `concept_architecture_plan.md`, guaranteeing that queue/fan-out defaults stay in sync with the documented observability contract.

### lib_log_rich.runtime._factories
* **Purpose:** Bridges configuration (`RuntimeSettings`) to concrete adapters, rate limiters, and binders so the composition root can remain declarative.
* **Key Functions:** `create_dump_renderer` wires dump capture; `create_runtime_binder` seeds the global context; `create_structured_backends` and `create_graylog_adapter` toggle optional sinks; `compute_thresholds` harmonises level settings across adapters.
* **Design Hooks:** Encapsulates the dependency wiring rules outlined in `concept_architecture.md` (DI boundaries, optional adapters, queue defaults) ensuring the runtime API reads clean architecture ports instead of concretes.

### RuntimeConfig Parameter Reference

The `RuntimeConfig` Pydantic model is the sole entry point for configuring the logging runtime via `init()`. All parameters and their defaults:

| Parameter                 | Type                                                 | Default                                             | Description                                                                                                                               |
|---------------------------|------------------------------------------------------|-----------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| `service`                 | `str`                                                | **required**                                        | Service identifier propagated to all log events and backends.                                                                             |
| `environment`             | `str`                                                | **required**                                        | Environment label (e.g. `production`, `staging`).                                                                                         |
| `console_level`           | `str \| LogLevel`                                    | `LogLevel.INFO`                                     | Minimum level for console output.                                                                                                         |
| `backend_level`           | `str \| LogLevel`                                    | `LogLevel.WARNING`                                  | Minimum level for structured backends (journald, Event Log).                                                                              |
| `graylog_endpoint`        | `tuple[str, int] \| None`                            | `None`                                              | Graylog host and port (e.g. `("graylog.local", 12201)`).                                                                                  |
| `graylog_level`           | `str \| LogLevel`                                    | `LogLevel.WARNING`                                  | Minimum level for Graylog events.                                                                                                         |
| `enable_ring_buffer`      | `bool`                                               | `True`                                              | Maintain an in-memory ring buffer for dump exports.                                                                                       |
| `ring_buffer_size`        | `int`                                                | `25_000`                                            | Maximum events retained in the ring buffer.                                                                                               |
| `ring_buffer_checkpoint`  | `Path \| None`                                       | `None`                                              | JSONL file the ring buffer is loaded from at `init()` and appended to by every ring-buffer flush; `LOG_RING_BUFFER_CHECKPOINT` overrides. |
| `enable_journald`         | `bool`                                               | `False`                                             | Forward events to systemd-journald (Linux only).                                                                                          |
| `enable_eventlog`         | `bool`                                               | `False`                                             | Forward events to Windows Event Log (Windows only).                                                                                       |
| `enable_graylog`          | `bool`                                               | `False`                                             | Forward events to Graylog via GELF.                                                                                                       |
| `graylog_protocol`        | `GraylogProtocol`                                    | `GraylogProtocol.TCP`                               | Graylog transport: `TCP` or `UDP` (value strings parse in any case).                                                                      |
| `graylog_tls`             | `bool`                                               | `False`                                             | Enable TLS for Graylog TCP connections.                                                                                                   |
| `queue_enabled`           | `bool`                                               | `True`                                              | Use a background queue worker for async log dispatch.                                                                                     |
| `queue_maxsize`           | `int`                                                | `2048`                                              | Maximum queue depth before backpressure applies.                                                                                          |
| `queue_full_policy`       | `QueuePolicy`                                        | `QueuePolicy.BLOCK`                                 | Behaviour when queue is full: `BLOCK` or `DROP`.                                                                                          |
| `queue_put_timeout`       | `float \| None`                                      | `1.0`                                               | Seconds to wait when enqueuing (block policy); `None` = indefinite.                                                                       |
| `queue_stop_timeout`      | `float \| None`                                      | `5.0`                                               | Seconds to wait for queue drain during shutdown; `None` = indefinite.                                                                     |
| `force_color`             | `bool`                                               | `False`                                             | Force ANSI colour output even when not a TTY.                                                                                             |
| `no_color`                | `bool`                                               | `False`                                             | Suppress all colour output.                                                                                                               |
| `console_styles`          | `Mapping[str \| LogLevel, str] \| None`              | `None`                                              | Per-level Rich style overrides (e.g. `{"DEBUG": "dim cyan"}`).                                                                            |
| `console_theme`           | `ConsoleTheme \| None`                               | `ConsoleTheme.DARK`                                 | Named palette: `CLASSIC`, `DARK`, `NEON`, `PASTEL`; `None` = no palette.                                                                  |
| `console_format_preset`   | `FormatPreset \| None`                               | `None` (platform-specific)                          | Layout preset: `FULL`, `SHORT`, `FULL_LOC`, `SHORT_LOC`, `SHORT_LOC_ICON`; `None` = `SHORT_LOC_ICON` on Windows, `SHORT_LOC` elsewhere.   |
| `console_format_template` | `str \| None`                                        | `None`                                              | Custom `str.format` template (overrides preset when set).                                                                                 |
| `console_stream`          | `ConsoleStream`                                      | `ConsoleStream.STDERR`                              | Output destination: `STDOUT`, `STDERR`, `BOTH`, `CUSTOM`, `NONE`.                                                                         |
| `console_stream_target`   | `object \| None`                                     | `None`                                              | Writable stream object when `console_stream=ConsoleStream.CUSTOM`.                                                                        |
| `scrub_patterns`          | `dict[str, str] \| None`                             | `{"password": ".+", "secret": ".+", "token": ".+"}` | Regex patterns for sensitive-field redaction.                                                                                             |
| `dump_format_preset`      | `FormatPreset \| None`                               | `None`                                              | Default text dump layout preset; `None` = `FULL` (the console-only `SHORT_LOC_ICON` is refused at `init()`).                              |
| `dump_format_template`    | `str \| None`                                        | `None`                                              | Custom template for text dump format.                                                                                                     |
| `rate_limit`              | `tuple[int, float] \| None`                          | `None`                                              | Rate limiting as `(max_events, window_seconds)`.                                                                                          |
| `payload_limits`          | `PayloadLimits \| None`                              | See `PayloadLimits`                                 | Bounds on message length, extra keys, depth, etc.; a mapping is validated into `PayloadLimits`.                                           |
| `diagnostic_hook`         | `DiagnosticCallback \| None`                         | `None`                                              | Callback receiving `(event_name, payload)` tuples.                                                                                        |
| `console_adapter_factory` | `Callable[[ConsoleAppearance], ConsolePort] \| None` | `None`                                              | Inject a custom `ConsolePort` implementation.                                                                                             |

#### PayloadLimits Defaults

| Field                     | Type          | Default | Description                                      |
|---------------------------|---------------|---------|--------------------------------------------------|
| `truncate_message`        | `bool`        | `True`  | Truncate messages exceeding `message_max_chars`. |
| `message_max_chars`       | `int`         | `4096`  | Maximum message length in characters.            |
| `extra_max_keys`          | `int`         | `25`    | Maximum number of keys in event extra payload.   |
| `extra_max_value_chars`   | `int`         | `512`   | Maximum characters per extra value.              |
| `extra_max_depth`         | `int`         | `3`     | Maximum nesting depth for extra payloads.        |
| `extra_max_total_bytes`   | `int \| None` | `8192`  | Total byte budget for serialised extra data.     |
| `context_max_keys`        | `int`         | `20`    | Maximum keys in context extra.                   |
| `context_max_value_chars` | `int`         | `256`   | Maximum characters per context value.            |
| `stacktrace_max_frames`   | `int`         | `10`    | Maximum stack frames retained in dumps.          |

### lib_log_rich.adapters._schemas
* **Purpose:** Authoritative Pydantic models for queue/dump payloads consumed by downstream adapters and exported artefacts.
* **Key Functions:** `LogContextPayload` + `LogEventPayload` guarantee serialisation stability; helper factories (`_new_int_list`, `_new_str_dict`) protect against shared mutable defaults.
* **Design Hooks:** Aligns JSON structure with the "Structured Payload" contract in `concept_architecture_plan.md`, keeping CLI dumps, queue workers, and external sinks interoperable.
