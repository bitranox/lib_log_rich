---

# Concept: lib_log_rich Logging Backbone

## Idea

lib_log_rich is a Clean Architecture logging backbone that delivers coloured console output, structured platform sinks, and optional central aggregation while exposing a deliberately small public API. The runtime fans out events to Rich, journald, Windows Event Log, and (when enabled) Graylog, keeps a bounded ring buffer for incident response, and includes diagnostic and throttling hooks so operators can reason about behaviour in production.

---

## A) Goals & Scope

1. **Primary Outcomes**

* Rich-powered console output with themes, presets, templates, and explicit colour toggles (`force_color`, `no_color`).
* Structured journald and Windows Event Log adapters with field normalisation, plus an optional Graylog GELF adapter supporting TCP/TLS or UDP.
* No persistent file loggers; a ring buffer plus text/JSON/HTML dump exporter cover forensic needs.
* Queue-backed fan-out enabled by default (`queue_enabled=True`) with a one-second blocking timeout, degraded-drop diagnostics, and inline processing fall-back for simple scripts.
* Context propagation via `ContextBinder`: service, environment, job/request/user IDs, host metrics, PID chain, and trace IDs flow through every adapter.
* Configurable `diagnostic_hook` surfacing queue, fan-out, and throttling signals (`queued`, `queue_dropped`, `queue_degraded_drop_mode`, `queue_worker_error`, `emitted`, `rate_limited`) without affecting the logging pipeline.
* Optional sliding-window rate limiter (`rate_limit=(max_events, window_seconds)` or `LOG_RATE_LIMIT=count/window`) to protect downstream systems.
* Explicit configuration surface with keyword-only API parameters, environment overrides, and opt-in `.env` loading through `lib_log_rich.config.enable_dotenv()` or `LOG_USE_DOTENV=1`.

2. **Clarifications / Out of Scope**

* No bundled metrics or OpenTelemetry wiring; consumers should integrate via the diagnostic hook or adapters of their own.
* No default log shipping agents or file rotation strategies.
* `.env` files are never loaded implicitly; operators must opt in at runtime or via CLI flags.
* Console is the only coloured channel; structured sinks stay plain UTF-8/ASCII.

---

## B) Output Channels & Platforms

1. **Console (Rich)**
   * Uses Rich renderables with Unicode icons when colour is active.
   * Format controlled by `console_format_preset` (`full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`) or a `console_format_template`; templates override presets. Platform-specific defaults: Windows uses `short_loc_icon`, Linux/Mac use `short_loc`.
   * Styles merge built-in palettes (`CONSOLE_STYLE_THEMES`) with overrides from `console_styles` or `LOG_CONSOLE_STYLES`.
   * TTY detection disables colour by default; `force_color`/`no_color` invert behaviour.
   * Runtime stores the active theme/styles so dumps can reuse them.
   * Queue-backed console adapters (`QueueConsoleAdapter`, `AsyncQueueConsoleAdapter`) reuse the same formatter; the asyncio variant emits drop diagnostics when its queue overflows.

2. **Linux Backend (journald)**
   * Emits uppercase ASCII fields via `systemd.journal.send`.
   * Injects PID chain as a `>`-joined string while respecting the eight-entry cap.
   * Auto-disables when `systemd-python` is missing or the platform is not Linux.

3. **Windows Backend (Event Log)**
   * Wraps `win32evtlogutil.ReportEvent`.
   * Defaults to the `Application` log; per-level Event IDs (`INFO=1000`, `WARNING=2000`, `ERROR=3000`, `CRITICAL=4000`) can be overridden.
   * Message strings include the colour-free message plus sorted context and extra fields.

4. **Central Backend (Graylog via GELF, optional)**
   * Works only when `enable_graylog=True` and an endpoint is provided (`graylog_endpoint=("host", port)` or `LOG_GRAYLOG_ENDPOINT`).
   * Supports TCP (optional TLS) or UDP. TLS with UDP raises `ValueError`.
   * Retries once on TCP send failure, recreating sockets as needed; `flush()` closes sockets on shutdown.
   * Adds `_service`, `_environment`, `_job_id`, `_process_id_chain`, and `_`-prefixed extras to payloads.

---

## C) Formatting & Colour Strategy

* Console presets and templates share placeholders defined in `adapters._formatting`.
* Text dumps inherit the same template logic, allowing operators to reuse console layouts.
* Themes (`console_theme` or `LOG_CONSOLE_THEME`) capture palette intent; overrides from `console_styles` or `LOG_CONSOLE_STYLES` merge with defaults.
* `color=True` on `dump(...)` renders ANSI sequences; JSON and HTML variants remain colour-free.

---

## D) Level Strategy & Filtering

* Independent thresholds for console (`console_level`/`LOG_CONSOLE_LEVEL`), structured backends (`backend_level`/`LOG_BACKEND_LEVEL`), and Graylog (`graylog_level`/`LOG_GRAYLOG_LEVEL`).
* `LogLevel` enumerates `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` with icons, severity codes, and syslog/GELF mappings.
* Optional rate limiter accepts `(max_events, window_seconds)` tuples or `LOG_RATE_LIMIT="count/window"`. Rejected events emit `rate_limited` diagnostics and do not enter the ring buffer.
* Diagnostic hook executions never raise; failures are swallowed deliberately.

---

## E) Structured Fields

* Journald fields: uppercase keys (`SERVICE`, `ENVIRONMENT`, `JOB_ID`, `REQUEST_ID`, `USER_ID`, `USER_NAME`, `HOSTNAME`, `PROCESS_ID`, `PROCESS_ID_CHAIN`, `TRACE_ID`, `SPAN_ID`, plus `EXTRA` flattened).
* Windows Event Log strings: camelCase and `PROCESS_ID_CHAIN=...`/`EXTRA={...}` appended to the message array.
* Graylog: `_service`, `_environment`, `_job_id`, `_request_id`, `_process_id_chain`, `_hostname`, `_pid`, `_user`, plus `_`-prefixed extras.
* Ring buffer retains the complete context dictionary and `extra` payload without mutation.

---

## F) Multiprocessing & Thread-Safety

* `QueueAdapter` (default) uses a daemon thread and bounded queue (`maxsize=2048`) to decouple producers.
* `queue_enabled=False` switches to inline fan-out for synchronous scripts.
* Context is stored in `ContextBinder` using `contextvars`; `refresh_context` (defined in `application/use_cases/_pipeline.py` and exported via `process_event.refresh_context`) refreshes PID, hostname, and user per emit, truncating PID chains to eight entries.
* Diagnostic hook marks `queued` events when the queue path is taken.

---

## G) Dumps & Incident Response

* `dump(dump_format="text"|"json"|"html_table"|"html_txt", path=None, level=None, console_format_preset=None, console_format_template=None, theme=None, console_styles=None, color=False)` captures the ring buffer.
  * Text: `str.format` template using placeholders such as `{timestamp}`, `{level_code}`, `{logger_name}`, `{process_id_chain}`, `{context}`, `{extra}`; accepts colour via `color=True`.
  * JSON: deterministic list of event dictionaries sorted by timestamp.
  * HTML table: Rich-rendered table with badges/icons; HTML text provides colourised preformatted blocks.
* When `path` is provided the rendered payload is still returned and the adapter writes to disk, creating parent directories.
* Ring buffer flushes after a successful dump; failures leave the buffer intact.
* Level filtering applies before rendering; optional `context_filters`, `context_extra_filters`, and `extra_filters` refine dumps by exact/substring/regex predicates before the buffer is flushed. CLI surfacing mirrors these arguments (`--context-exact`, `--extra-regex`, etc.).

---

## H) Public API Surface

```python
import lib_log_rich as log

def bootstrap() -> None:
    config = log.RuntimeConfig(
        service="orders",
        environment="production",
        console_level="info",
        backend_level="warning",
        enable_journald=True,
        enable_eventlog=False,
        enable_graylog=True,
        graylog_endpoint=("graylog.local", 12201),
        graylog_protocol="tcp",
        graylog_tls=True,
        queue_enabled=True,
        enable_ring_buffer=True,
        ring_buffer_size=25_000,
        console_theme="classic",
        console_styles={"WARNING": "bold yellow"},
        console_format_preset="full",
        dump_format_preset="full",
        rate_limit=(120, 60.0),
        scrub_patterns={"password": r".+"},
        diagnostic_hook=lambda event, payload: None,
    )
    log.init(config)

    with log.bind(job_id="reindex-20251001", request_id="req-42", user_id="svc"):
        logger = log.getLogger("orders.reindexer")
        logger.info("started", extra={"batch": 7})
        logger.error("failed", extra={"error_code": "IDX_500"})

    text = log.dump(dump_format="text", level="warning", color=True)
    print(text)
    log.shutdown()
```

Public helpers for quick smoke tests: `hello_world`, `i_should_fail`, `summary_info`, plus the `logdemo` showcase.

---

## I) Configuration Matrix

* Precedence: CLI/explicit kwargs → process environment → `.env` (when enabled) → defaults.
* Environment variables mirror API keywords:
  * Identity/levels: `LOG_SERVICE`, `LOG_ENVIRONMENT`, `LOG_CONSOLE_LEVEL`, `LOG_BACKEND_LEVEL`, `LOG_GRAYLOG_LEVEL`.
  * Feature toggles: `LOG_QUEUE_ENABLED`, `LOG_RING_BUFFER_ENABLED`, `LOG_ENABLE_JOURNALD`, `LOG_ENABLE_EVENTLOG`, `LOG_ENABLE_GRAYLOG`.
  * Graylog transport: `LOG_GRAYLOG_ENDPOINT` (`host:port`), `LOG_GRAYLOG_PROTOCOL`, `LOG_GRAYLOG_TLS`.
  * Console appearance: `LOG_FORCE_COLOR`, `LOG_NO_COLOR`, `LOG_CONSOLE_THEME`, `LOG_CONSOLE_STYLES`, `LOG_CONSOLE_FORMAT_PRESET`, `LOG_CONSOLE_FORMAT_TEMPLATE`.
  * Dump defaults: `LOG_DUMP_FORMAT_PRESET`, `LOG_DUMP_FORMAT_TEMPLATE`.
  * Scrubber and throttling: `LOG_SCRUB_PATTERNS` (`FIELD=regex` comma list), `LOG_RATE_LIMIT` (`count/window`).
* `.env` loading controlled by `lib_log_rich.config.enable_dotenv()` or `LOG_USE_DOTENV`; helpers refuse to search when the starting directory is missing and cache the chosen file.

---

## J) Performance & Resilience

* Lazy string formatting; console formatting happens only after thresholds pass.
* Queue path decouples producers from slow sinks; diagnostic hook marks queue usage so operators can observe throughput.
* Graylog adapter retries once on TCP errors and closes sockets on shutdown.
* Rate limiter guards platform sinks during storms without affecting the console path.
* QueueAdapter enforces a bounded queue with a one-second `queue_put_timeout`; worker failures emit `queue_degraded_drop_mode` and `queue_worker_error` diagnostics so operators can react before producers stall.
* Ring buffer capacity defaults to 25,000 events when enabled and downgrades to 1,024 for lightweight deployments (`enable_ring_buffer=False` keeps a smaller buffer for dumps).

---

## K) Security & Scrubbing

* Default scrub patterns target common secret-like keys (`password`, `secret`, `token`) and replace matches with `***`; the scrubber walks both per-event `extra` payloads and `LogContext.extra` while leaving caller-visible objects immutable.
* Custom patterns merge with defaults via kwargs and `LOG_SCRUB_PATTERNS`; later definitions win.
* Context validation enforces non-empty `service`/`environment`; PID chains never exceed eight entries.
* Diagnostic hook receives metadata but no event bodies to avoid accidental leakage.

---

## L) Testing & Developer Experience

* `make test` runs Ruff, Pyright, pytest (unit, contract, doctest, snapshot) and coverage; coverage DBs use unique filenames to avoid lock contention.
* Contract tests exercise ports with fakes to keep adapters substitutable.
* Queue, Graylog, and dump paths have doctests or unit tests to verify behaviour offline.
* CLI surface verified via rich-click runner; docstrings include actionable doctest examples.

---

## M) Dependencies

* Core: Python stdlib, Rich.
* Optional extras: `systemd-python` (journald), `pywin32` (Event Log), standard library `ssl` for Graylog TLS.
* Tooling: `pytest`, `hypothesis`, `ruff`, `pyright`, `python-dotenv`, `rich-click`, `lib_cli_exit_tools`.

---

## N) Outstanding Decisions

* Evaluate alternative defaults for `ring_buffer_size` per deployment profile.
* Confirm journald field whitelist with SRE stakeholders before widening payloads.
* Consider additional dump adapters (S3 upload, NDJSON file) once incident workflows stabilise.
* Explore emitting diagnostic metrics or OpenTelemetry spans from the diagnostic hook without coupling the core.

---

## O) Baseline Flow Snapshot

```
Producer → LoggerProxy → ContextBinder.refresh → RateLimiter
    ├─ reject → diagnostic("rate_limited")
    └─ accept → RingBuffer.append → {QueueAdapter.put → diagnostic("queued")} |
                                      {Fan-out → Console/Journald/EventLog/Graylog → diagnostic("emitted")}
                    ↓
                 Dump adapter (on demand) with flush
```

This concept document is the product-facing source of truth. Architecture details live in `concept_architecture.md`, and the implementation plan is tracked in `concept_architecture_plan.md`.
