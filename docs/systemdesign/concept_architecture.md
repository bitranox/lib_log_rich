# Architecture Guide: lib_log_rich Logging Backbone

## 1. Purpose & Context
lib_log_rich packages a layered logging runtime: a domain core, application use cases, and adapters for console output, platform backends, and optional central aggregation. This guide explains how the Clean Architecture boundaries are applied and how the system satisfies the goals defined in `konzept.md`.

## 2. Target Architecture & Principles
- **Clean layering:** domain (value objects, invariants) → application (use cases, ports) → adapters (console, structured sinks, dumps, queues).
- **Configurable fan-out:** console, journald, Windows Event Log, and Graylog each expose independent thresholds and toggles.
- **Context-first design:** service/environment plus job, request, user, host, and process lineage flow through every adapter.
- **Operational resilience:** no long-lived file handlers; ring buffer + dumps cover diagnostics, and adapters fail independently.
- **Extensibility:** ports allow additional sinks without modifying the core orchestration.

## 3. High-Level Data Flow
1. Application code calls `lib_log_rich.get()` to obtain a `LoggerProxy` bound to the current runtime.
2. `bind()` scopes context fields (job, request, user, trace) using `contextvars`.
3. Logging calls create `LogEvent` objects via `process_log_event`.
4. Optional `QueueAdapter` performs asynchronous fan-out; otherwise events flow synchronously.
5. Use case fans out to enabled adapters (Rich console, journald, Windows Event Log, Graylog) and appends to the ring buffer.
6. `dump(...)` pulls from the ring buffer to render text/JSON/HTML snapshots without stopping the runtime.
7. `shutdown()` drains queues, flushes Graylog, and clears runtime state.

## 4. Ports & Adapters
| Port | Responsibility | Default Adapters | Notes |
| --- | --- | --- | --- |
| `ConsolePort` | Human-friendly rendering | `RichConsoleAdapter` | Style map merge, colour toggles, icons, level codes |
| `StructuredBackendPort` | Platform logs (journald/Event Log) | `JournaldAdapter`, `WindowsEventLogAdapter` | ASCII uppercase vs camelCase payloads |
| `GraylogPort` (optional) | Central syslog/GELF | `GraylogAdapter` | TCP/TLS, `_`-prefixed fields, retries |
| `DumpPort` | Export ring buffer | `DumpAdapter` | Text, JSON, HTML, `{process_id_chain}` placeholder |
| `QueuePort` | Background worker | `QueueAdapter` | Bounded queue, sentinel-based shutdown |
| `ScrubberPort` | Secret masking | default scrubber | Regex-driven, configurable |
| `RateLimiterPort` | Throttling | sliding window | Guard against noisy loops |
| `ClockPort` / `IdProvider` | Deterministic time/IDs | monotonic clock + UUID-lite | Injected for tests |

Adapters are wired in the composition root (`init`). Flags disable specific adapters per deployment (e.g., `enable_journald=False`).

## 5. Formatting & Layout
- **Console:** default template `"{timestamp} {level.icon} {level.severity.upper():>8} {logger_name} — {message}{context_str}"`; available keys include `{level_code}` and the merged context/extra values.
- **HTML dumps:** separate template with Rich styling, no reliance on console format.
- **Structured sinks:** plain text message plus structured fields; no ANSI escape sequences.
- **Configuration:** `init` parameters include `console_styles`, `console_format_preset`, `console_format_template`, `force_color`, `no_color`; environment variables mirror them.

## 6. Context & Field Management
- `LogContext` enforces non-empty `service`, `environment`, `job_id` requirement is optional but recommended.
- Context stores `process_id` and a bounded `process_id_chain`; new processes append their PID.
- Fields are normalised per adapter (uppercase, camelCase, or `_` prefix) before emission.
- Scrubber replaces sensitive patterns (JWTs, emails, tokens) before fan-out.

## 7. Concurrency Model
- `QueueAdapter` (enabled by default) uses a background thread with `queue.Queue`; set `queue_enabled=False` to run synchronously.
- Producers remain non-blocking until queue saturation; saturation raises `queue.Full` and surfaces via diagnostic hook.
- Shutdown hands a sentinel to the queue and waits for the worker to drain before closing adapters.

## 8. Error Handling & Resilience
- Console adapter never raises; it writes diagnostics when styling fails.
- Journald and Event Log adapters swallow platform-specific errors and log them via the diagnostic hook.
- Graylog adapter retries with exponential backoff + jitter; after configured attempts it drops the event and records a diagnostic.
- Dump adapter validates templates and raises `ValueError` for unknown placeholders to avoid silent data loss.

## 9. Configuration & Deployment
- `init` keyword-only parameters map directly to environment variables (`LOG_CONSOLE_LEVEL`, `LOG_BACKEND_LEVEL`, `LOG_ENABLE_JOURNALD`, etc.).
- `.env` loading must be opted in via `lib_log_rich.config.enable_dotenv()` to avoid surprises in production.
- Optional extras: install `lib_log_rich[journald]` or `lib_log_rich[eventlog]` to pull in platform dependencies.
- Runtime emits a summary banner (`summary_info()`) that lists enabled adapters for quick verification.

## 10. Testing Strategy
- Domain: doctests + pytest cover LogLevel conversions, context invariants, ring buffer eviction.
- Ports: each port ships with contract tests exercising fake adapters.
- Adapters: journald/Event Log rely on fakes/mocks; Graylog tests use in-memory sockets.
- Queue: integration tests verify ordering, sentinel shutdown, and rate limiting.
- CLI: Click runner snapshots CLI commands (`info`, `hello`, `fail`, `logdemo`).
- Coverage target ≥ 90%; enforced by `make test`.

## 11. Known Risks & Decisions
- Ring buffer default size (25,000) may need tuning for constrained hosts.
- Windows Event Log requires administrative privileges; adapter degrades gracefully when unavailable.
- Journald adapter is skipped automatically when `systemd` bindings are missing.
- Graylog remains disabled by default; operators can rely on existing collectors if preferred.

## 12. API Snapshot
```python
import lib_log_rich as log

log.init(
    service="billing",
    environment="staging",
    console_level="debug",
    backend_level="warning",
    enable_journald=True,
    enable_eventlog=False,
    queue_enabled=True,
)

with log.bind(job_id="billing-worker-17", request_id="req-123", user_id="svc", trace_id="trace-1", span_id="span-1"):
    log.get("billing.worker").info("processed batch", extra={"batch": 17, "tenant": "acme"})

html_dump = log.dump(dump_format="html", level="info")
log.shutdown()
```

This architecture guide stays aligned with the module reference; any divergence requires updating both documents.
