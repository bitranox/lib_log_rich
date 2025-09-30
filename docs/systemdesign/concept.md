---

# Concept: lib_log_rich Logging Backbone

## Idea

lib_log_rich is a Python logging backbone that delivers coloured console output, structured platform sinks, and optional central aggregation while keeping an intentionally small public surface. The runtime should:

* render readable, colour-aware console logs (Rich-based) with configurable line layouts
* support Linux (journald) and Windows Event Log backends, with Graylog/GELF as an optional central sink
* avoid long-running file loggers while remaining thread-safe and multi-process capable
* capture dedicated Job IDs and contextual fields so downstream tools can correlate work items
* expose on-demand dumps (text/JSON/HTML) for incident response

---

## A) Goals & Scope

1. **Primary Outcomes**

* Rich-powered console output with icons, themes, and ANSI toggles
* Per-channel log level thresholds (console vs. structured backends)
* Structured journald and Windows Event Log adapters, opt-in Graylog over TCP/TLS
* No persistent log files; rely on ring buffer + dumps when needed
* Thread-safe core with optional background queue for multi-process scenarios
* Full-context propagation (`service`, `environment`, `job_id`, `request_id`, user metadata, process lineage)
* Dump API returning text, JSON, or HTML snapshots on demand
* Structured fields aligned across adapters (ASCII uppercase for journald, camelCase for Event Log, `_` prefix for GELF)

2. **Clarifications / Out of Scope**

* No bundled metrics/telemetry exporters (hooks only)
* No default OpenTelemetry wiring (can be added later)
* No opinionated file rotation or log shipping agents
* Console is the only channel with colour/Unicode icons; structured sinks stay plain

---

## B) Output Channels & Platforms

1. **Console (Rich)**
   * TTY detection with `force_color` / `no_color` overrides
   * Unicode icons and ANSI styles only when colour is enabled
   * Fallback to plain text for non-TTY streams
   * Configurable style map via `console_styles` and environment overrides

2. **Linux Backend (journald)**
   * Uses `systemd.journal.send` when available
   * Emits uppercase ASCII field names (`SERVICE`, `ENVIRONMENT`, `JOB_ID`, etc.)
   * Encodes process lineage (`PROCESS_ID_CHAIN`) as `>`-joined string

3. **Windows Backend (Event Log)**
   * `pywin32` / `win32evtlogutil.ReportEvent`
   * Default log: `Application`; provider defaults to the configured `service`
   * Event ID map: `INFO=1000`, `WARNING=2000`, `ERROR=3000`, `CRITICAL=4000` (configurable)

4. **Central Backend (Graylog via GELF, optional)**
   * Default transport: TCP with optional TLS
   * Additional fields use `_` prefix (`_job_id`, `_trace_id`, `_process_id_chain`)
   * Backoff + retry; drops events after sustained failure without blocking the console

Adapters can be disabled individually through `init(...)` flags (e.g., console-only deployments set `enable_journald=False`, `enable_eventlog=False`, `enable_ring_buffer=False`).

---

## C) Formatting & Colour Strategy

1. **Template Configuration**
   * Console template example: `{timestamp} {level:>5} {level_code} {logger_name} {process_id}:{thread_id} — {message} {context}`
   * `{level_code}` exposes four-character abbreviations (`DEBG`, `INFO`, `WARN`, `ERRO`, `CRIT`) for fixed-width columns
   * HTML dump uses a dedicated template with badges/icons; defaults to a neutral theme
   * Timestamps follow ISO8601 with microseconds and UTC offsets
   * Exceptions append multi-line traces; HTML separates trace blocks for readability

2. **Colour Controls**
   * Console styles defined per level; merge defaults with custom overrides or `LOG_CONSOLE_STYLES`
   * HTML renders colour tokens through Rich; structured sinks stay ASCII/UTF-8 without styling
   * Flags: `force_color`, `no_color`, or auto-detection based on TTY

---

## D) Level Strategy & Filtering

* Independent thresholds: `console_level`, `backend_level`, optional Graylog minimum level
* Level enum: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` with icons and four-letter codes
* Mapping to syslog/Event Log priorities: `CRITICAL→2`, `ERROR→3`, `WARNING→4`, `INFO→6`, `DEBUG→7`
* Optional rate limiter (sliding window) to prevent log floods during error loops

---

## E) Structured Fields

* Journald: `SERVICE`, `ENVIRONMENT`, `JOB_ID`, `REQUEST_ID`, `USER_ID`, `USER_NAME`, `HOSTNAME`, `PROCESS_ID`, `PROCESS_ID_CHAIN`, `TRACE_ID`, `SPAN_ID`
* Windows Event Log data section: camelCase equivalents (`service`, `environment`, `jobId`, ...)
* GELF: `_service`, `_environment`, `_job_id`, `_process_id_chain`, `_trace_id`, `_span_id`, `_hostname`
* Ring buffer retains the fully normalised context dictionary and the `extra` payload without mutation

---

## F) Multiprocessing & Thread-Safety

* Core logger functions remain thread-safe by relying on the stdlib logging infrastructure
* Optional `QueueAdapter` processes events asynchronously; disable via `queue_enabled=False`
* Context stored in `contextvars`; child processes hydrate context via serialized snapshots (`process_id_chain` prepends new PID)

---

## G) Dumps & Incident Response

* Ring buffer keeps the last `ring_buffer_size` events (default 25,000)
* `dump(dump_format="text"|"json"|"html", path=None, level=None, console_format_preset=None, console_format_template=None, color=False)` renders snapshots
  * Text: `str.format` template with placeholders (`timestamp`, `YYYY`, `MM`, `DD`, `hh`, `mm`, `ss`, `level`, `level_code`, `logger_name`, `event_id`, `message`, `user_name`, `hostname`, `process_id`, `process_id_chain`, `context`, `extra`)
  * JSON: deterministic array of event dictionaries
  * HTML: Rich-rendered table suitable for sharing
* Text dumps remain colour-free unless `color=True`

---

## H) Public API Surface

```python
import lib_log_rich as log

log.init(
    service="orders",
    environment="prod",
    console_level="info",
    backend_level="warning",
    enable_journald=True,
    enable_eventlog=False,
    enable_graylog=False,
    console_styles={"WARNING": "bold yellow"},
)

with log.bind(job_id="reindex-20250930", request_id="req-42", user_id="svc"):
    logger = log.get("app.indexer")
    logger.info("started", extra={"batch": 7})
    logger.error("failed", extra={"error_code": "IDX_500"})

print(log.dump(dump_format="text", level="warning"))
log.shutdown()
```

Exported helpers: `init`, `bind`, `get`, `dump`, `shutdown`, plus documentation examples (`hello_world`, `logdemo`, `summary_info`).

---

## I) Configuration Matrix

* API parameters accept keyword arguments with sensible defaults; environment variables mirror the same options (`LOG_CONSOLE_LEVEL`, `LOG_BACKEND_LEVEL`, `LOG_CONSOLE_STYLES`, `LOG_FORCE_COLOR`, etc.)
* `.env` support is explicit (`lib_log_rich.config.enable_dotenv()`); precedence: CLI ➝ real environment ➝ `.env` ➝ defaults
* Backend toggles: `enable_ring_buffer`, `enable_journald`, `enable_eventlog`, `enable_graylog`
* Queue toggle: `queue_enabled`
* Graylog transport controls: `graylog_endpoint`, `graylog_protocol`, `graylog_tls`

---

## J) Performance & Resilience

* Lazy formatting until fan-out to minimise overhead when levels filter events
* Queue adapter decouples producers; bounded queue raises backpressure for pathological throughput
* Graylog adapter retries with exponential backoff + jitter and logs diagnostics through a hook instead of recursive logging

---

## K) Security & Scrubbing

* Scrubber port masks secrets (JWTs, API keys, passwords) before the event reaches adapters
* Context validation rejects empty service/environment and normalises process lineage depth (max eight entries)
* Diagnostic hook allows custom observers without modifying the core pipeline

---

## L) Testing & Developer Experience

* `pytest` suite covers domain invariants, port contracts, adapter behaviours, queue semantics, and CLI surfaces
* Property-based tests validate ring buffer eviction and context propagation
* Snapshots clamp console/HTML rendering to detect regressions
* `make test` runs Ruff, Pyright, pytest, and coverage; contract violations fail CI

---

## M) Dependencies

* Core library: Python stdlib + Rich (console/HTML)
* Optional extras: `systemd-python` (journald), `pywin32` (Event Log), TLS requirements for Graylog (standard library `ssl`)
* Development tooling: `pytest`, `hypothesis`, `ruff`, `pyright`

---

## N) Outstanding Decisions

* Tune ring buffer size defaults for different deployment profiles
* Validate final journald field whitelist with operations stakeholders
* Expand Graylog options (UDP, HTTP) if required by deployments
* Decide whether to ship additional dump adapters (S3 export, NDJSON files)

---

## O) Baseline Flow Snapshot

```
Producer → LoggerProxy → ContextBinder → QueueAdapter (optional) →
ProcessEvent use case → fan-out to Console / journald / Event Log / Graylog →
Ring buffer retention → Dump adapter on demand
```

This concept document remains the product-facing source of truth. Architecture details live in `konzept_architecture.md`, and the implementation plan is tracked in `konzept_architecture_plan.md`.
