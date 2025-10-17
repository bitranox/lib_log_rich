# Architecture Guide: lib_log_rich Logging Backbone

## 1. Purpose & Context
lib_log_rich packages a layered logging runtime that satisfies the product goals defined in `concept.md`. This guide explains how the Clean Architecture boundaries are implemented, how configuration flows inward from adapters to the domain core, and where diagnostics and throttling hooks sit inside the system.

## 2. Target Architecture & Principles
- **Clean layering:** domain (value objects, invariants) → application (use cases, ports) → adapters (console, structured sinks, dumps, queue, scrubbing, rate limiting).
- **Configurable fan-out:** console, journald, Windows Event Log, and Graylog expose independent thresholds, toggles, and transport choices.
- **Context-first design:** service/environment plus job, request, user, host, and PID lineage traverse every adapter via `ContextBinder`.
- **Operational resilience:** no persistent file handlers; ring buffer + dump adapter cover diagnostics, and adapters fail independently while surfacing diagnostics.
- **Observability hooks:** sliding-window rate limiter and `diagnostic_hook` provide backpressure and health signals (`queued`, `queue_dropped`, `queue_degraded_drop_mode`, `queue_worker_error`, `emitted`, `rate_limited`) without breaking the pipeline.
- **Built-in metrics:** the severity monitor tracks peak level, per-level counts, and drop reasons so operators can check whether actionable events (or drops) occurred before exporting dumps.
- **Explicit configuration:** keyword-only API, environment overrides, and opt-in `.env` loading keep runtime behaviour predictable across hosts.

## 3. High-Level Data Flow
1. Host code calls `lib_log_rich.init(RuntimeConfig(...))`, which merges configuration with environment overrides, optionally loads `.env`, seeds the `ContextBinder` with a bootstrap frame, and constructs adapters.
2. `init` wires the process use case (`create_process_log_event`) with the resolved queue, scrubber, rate limiter, console, structured backends, and optional Graylog adapter.
3. Applications wrap execution inside `with lib_log_rich.bind(...):` to scope job/request metadata and obtain loggers via `lib_log_rich.getLogger(name)`.
4. Each logging call produces a `LogEvent`, refreshes context (PID, hostname, user), and runs through the rate limiter.
5. Rejected events emit `diagnostic_hook("rate_limited", ...)` and stop. Accepted events enter the ring buffer.
6. When the queue is enabled, events are enqueued (`diagnostic_hook("queued", ...)`) and processed asynchronously by `QueueAdapter`; overflows trigger `queue_dropped` diagnostics and worker failures flag `queue_degraded_drop_mode` / `queue_worker_error`. Inline mode fans out synchronously.

   *Multi-process note:* The current adapter spins a daemon **thread** inside the host process. Supporting a dedicated **process** worker would require a new composition root that rehydrates adapters in the child, IPC channels for diagnostics/backpressure, and serialisable `LogEvent` payloads. Until that investment is made, follow `SUBPROCESSES.md` to run one runtime per process when you need true multi-process fan-out.
7. Fan-out emits to Rich console (respecting colour decisions), journald/Event Log (if enabled), and Graylog (if enabled, meeting the threshold). Successful synchronous fan-out raises `diagnostic_hook("emitted", ...)`.
8. `dump(...)` pulls a snapshot from the ring buffer via `create_capture_dump`, applies level/context/extra filters plus format overrides, writes to disk when requested, and flushes the buffer after success.
9. `shutdown()` drains the queue, calls `GraylogAdapter.flush()`, optionally flushes the ring buffer to disk, and clears the runtime singleton.

## 4. Ports, Adapters, and Hooks
| Port / Hook | Responsibility | Default Implementation | Notes |
| --- | --- | --- | --- |
| `ConsolePort` | Human-friendly rendering | `RichConsoleAdapter` | honours `force_color`, `no_color`, presets/templates, and theme/style overrides |
| `StructuredBackendPort` | Platform logs (journald/Event Log) | `JournaldAdapter`, `WindowsEventLogAdapter` | journald auto-disables off Linux; Event Log auto-disables off Windows |
| `GraylogPort` | Central GELF output | `GraylogAdapter` | supports TCP/TLS or UDP, validates protocol/TLS pairing, retries once on TCP failure |
| `DumpPort` | Export ring buffer | `DumpAdapter` | renders text/JSON/HTML table/HTML text; flushes buffer after success |
| `QueuePort` | Background worker | `QueueAdapter` | daemon thread + bounded queue (`maxsize=2048`) with one-second `queue_put_timeout`, degraded-drop diagnostics, and inline fallback when disabled |
| `ScrubberPort` | Secret masking | `RegexScrubber` | walks event `extra` and `LogContext.extra`, merges default + custom + `LOG_SCRUB_PATTERNS`, and replaces matches with `***` while keeping originals immutable |
| `RateLimiterPort` | Throughput guard | `SlidingWindowRateLimiter` | accepts `(max_events, window_seconds)` tuples; disabled variant `_AllowAllRateLimiter` |
| `ClockPort` / `IdProvider` | Deterministic time/IDs | `SystemClock`, UUID-lite in `lib_log_rich.lib_log_rich` | injection keeps domain pure and tests deterministic |
| Diagnostic hook | Observability feed | caller-supplied callable | receives queue, fan-out, and throttling events (`queued`, `queue_dropped`, `queue_degraded_drop_mode`, `queue_worker_error`, `emitted`, `rate_limited`); exceptions swallowed |

## 5. Formatting & Layout
- Console templates follow `adapters._formatting` placeholders (`{timestamp}`, `{level_code}`, `{message}`, `{extra}`, etc.).
- Presets (`full`, `short`, `full_loc`, `short_loc`) capture common layouts; custom templates override presets.
- Themes and style maps come from `console_theme`, `console_styles`, `LOG_CONSOLE_THEME`, `LOG_CONSOLE_STYLES`, and built-in palettes (`CONSOLE_STYLE_THEMES`).
- Dump adapter reuses the same templates for text output; JSON/HTML variants ignore colour flags.
- Environment overrides: `LOG_CONSOLE_FORMAT_PRESET`, `LOG_CONSOLE_FORMAT_TEMPLATE`, `LOG_DUMP_FORMAT_PRESET`, `LOG_DUMP_FORMAT_TEMPLATE`.

## 6. Context & Field Management
- `ContextBinder` stores a stack of `LogContext` frames backed by `contextvars` for thread/task isolation.
- `init` seeds the binder with a bootstrap frame (`job_id="bootstrap"`) capturing system identity (`user_name`, `hostname`, `process_id`, `process_id_chain`).
- `refresh_context` (implemented in `src/lib_log_rich/application/use_cases/_pipeline.py` and exported via `process_event.refresh_context`) updates PID, hostname, and user per emit, truncating the PID chain to at most eight entries and replacing the top frame when values change.
- After context refresh, the scrubber sanitises both the transient event `extra` payload and `LogContext.extra`, ensuring sensitive fields never reach adapters while leaving the original caller data structures untouched.
- Context serialisation (`serialize`/`deserialize`) supports multiprocessing hand-off; `.replace_top` keeps the stack immutable for callers.

## 7. Concurrency Model
- `QueueAdapter` starts lazily when the runtime is initialised, enforces a one-second `queue_put_timeout`, and records degraded-drop diagnostics when worker failures force blocking producers into drop mode. `stop(drain=True)` waits for completion; `drain=False` drops pending events via the drop handler.
- Inline mode (`queue_enabled=False`) bypasses the queue and processes fan-out synchronously, useful for CLI demos and tests.
- Rate limiter runs before queueing, preventing floods from entering the queue during storms.
- Diagnostic hook exposes queue usage and health (`queued`, `queue_dropped`, `queue_degraded_drop_mode`, `queue_worker_error`).

## 8. Error Handling & Resilience
- Console adapter never raises; formatting failures emit diagnostics via the hook and continue.
- Journald and Event Log adapters swallow platform errors and signal via diagnostics.
- Graylog adapter validates protocol/TLS upfront, retries a failed TCP send once, and closes sockets during `flush()`.
- Rate-limited events skip the ring buffer and fan-out entirely, guaranteeing downstream adapters never see rejected events.
- Dump adapter validates templates/presets and raises `ValueError` for unknown placeholders, preventing silent data loss.

## 9. Configuration & Deployment
- Keyword-only parameters correspond to environment variables (see `concept.md` for the full matrix).
- `.env` loading requires explicit opt-in through `lib_log_rich.config.enable_dotenv()` or `LOG_USE_DOTENV`; helpers search upwards until hitting `pyproject.toml`/`.git` markers.
- CLI (`lib_log_rich.cli`) exposes `--use-dotenv`, `--traceback/--no-traceback`, `--console-format-*`, and dump toggles, delegating to `lib_cli_exit_tools` for exit semantics.
- Platform extras (`systemd-python`, `pywin32`) are optional dependencies gated behind extras.

## 10. Testing Strategy
- Domain: doctests + pytest cover `LogLevel`, `LogContext`, `LogEvent`, and `RingBuffer` invariants.
- Ports: `tests/application/test_ports_contracts.py`, `tests/application/test_use_cases.py`, and `tests/adapters/test_queue_adapter.py` exercise the adapter contracts (console, structured backends, Graylog, queue, scrubber, rate limiter, clock, ID provider) to guarantee substitutability.
- Use cases: `tests/application/test_use_cases.py` verifies rate limiting, queue wiring, dump flushing, diagnostic hook invocations, and shutdown semantics.
- Adapters: journald/Event Log rely on fakes/mocks; Graylog tests use in-memory sockets; queue tests ensure drain/stop behaviour.
- CLI: snapshot via rich-click runner; docstrings include runnable examples to keep docs honest.
- Coverage target ≥ 85% enforced by `make test`; doctest modules run via pytest configuration.

## 11. Known Risks & Decisions
- Queue is bounded; sustained saturation blocks producers. Operators should monitor diagnostics and adjust `rate_limit`/queue size if required.
- Queue shutdown waits honour `queue_stop_timeout` (`LOG_QUEUE_STOP_TIMEOUT`); increase it when adapters need more than five seconds to flush or set `None` to wait indefinitely.
- Windows Event Log requires administrative privileges on some hosts; adapter degrades gracefully but should be validated in CI on Windows.
- Journald adapter assumes `systemd` availability; fallback is to disable the adapter.
- Ring buffer default size (25,000) may be heavy for small containers; provide guidance in docs for tuning.
- Diagnostic hook currently emits best-effort metadata; potential future work includes structured metrics exporters.

## 12. Future Ideas
- Layer richer severity analytics on top of the new monitor: peak-transition counters, sliding-window histograms, and streak detection once operators ask for trend visualisations.

## 13. API Snapshot
```python
import lib_log_rich as log

config = log.RuntimeConfig(
    service="billing",
    environment="staging",
    console_level="debug",
    backend_level="warning",
    enable_journald=True,
    enable_eventlog=False,
    enable_graylog=False,
    queue_enabled=True,
    enable_ring_buffer=True,
    console_theme="dark",
    console_format_preset="full",
    dump_format_template="{timestamp} {level} {message}",
    rate_limit=(60, 60.0),
    diagnostic_hook=lambda event, payload: print(f"diag {event}: {payload}"),
)
log.init(config)

with log.bind(job_id="billing-worker-17", request_id="req-123", user_id="svc", trace_id="trace-1", span_id="span-1"):
    log.getLogger("billing.worker").info("processed batch", extra={"batch": 17, "tenant": "acme"})

html_dump = log.dump(dump_format="html_table", level="info")
log.shutdown()
```

This architecture guide stays aligned with the module reference; update both documents when behaviour changes.
