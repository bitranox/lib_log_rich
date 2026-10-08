# Runtime lifecycle and application integration

How to start, run, flush and stop the lib_log_rich runtime, bind context, bridge stdlib `logging`, handle queues,
subprocesses, GUI streams and tests. Config field details and dumps/CLI live in the other reference files.

Import map: `init getLogger bind flush flush_async shutdown shutdown_async get_minimum_log_level max_level_seen
severity_snapshot reset_severity_metrics dump validate_config RuntimeConfig` are on `lib_log_rich`.
`is_initialised inspect_runtime attach_std_logging StdlibLoggingHandler current_runtime QueueConsoleAdapter
AsyncQueueConsoleAdapter ConsoleAppearance` are on `lib_log_rich.runtime` only. `ContextBinder` is on
`lib_log_rich.domain`.

## 1. Lifecycle

```python
import lib_log_rich as log
from lib_log_rich.runtime import is_initialised, inspect_runtime

log.init(log.RuntimeConfig(service="orders", environment="prod"))
try:
    log.getLogger(__name__).info("started")
finally:
    log.shutdown()
```

| Call                                              | Behaviour                                                                                                                                                                                                                                                                                   |
|---------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `init(config)`                                    | Builds the one runtime of the process. A second call without `shutdown()` raises `RuntimeError: lib_log_rich.init() cannot be called twice without shutdown(); call lib_log_rich.shutdown() first`. A concurrent `init` from another thread raises `... already running in another thread`. |
| `validate_config(config)`                         | Refuses a bad config exactly as `init` would, without starting anything. Use it for startup checks.                                                                                                                                                                                         |
| `is_initialised()`                                | `True` between `init` and `shutdown`. Guard teardown with it.                                                                                                                                                                                                                               |
| `inspect_runtime()`                               | Frozen `RuntimeSnapshot`: `service environment console_level backend_level graylog_level queue_present theme console_styles`.                                                                                                                                                               |
| `flush(timeout=None, *, flush_ring_buffer=False)` | Drains the queue, flushes console streams, closes the Graylog socket (reconnects lazily). Runtime stays active. Raises `TimeoutError` if the queue does not drain (default wait 5 s, `queue_stop_timeout`).                                                                                 |
| `shutdown()`                                      | Stops the queue worker (drain up to `queue_stop_timeout`), flushes adapters, clears the runtime. Call `init` again afterwards if needed.                                                                                                                                                    |
| `flush_async()` / `shutdown_async()`              | Same, awaitable.                                                                                                                                                                                                                                                                            |

Calling API functions (`getLogger`, `bind`, `flush`, `shutdown`, `get_minimum_log_level`, `inspect_runtime`) before
`init` or after `shutdown` raises `RuntimeError: lib_log_rich.init() must be called before using the logging API`.
`shutdown()` twice therefore raises on the second call; guard with `is_initialised()` when teardown may run twice.

Inside a running event loop the sync variants refuse:
`RuntimeError: lib_log_rich.shutdown() cannot run inside an active event loop; await lib_log_rich.shutdown_async()
instead` (same for `flush`). In async apps use `await log.flush_async()` and `await log.shutdown_async()`.

### Exiting without shutdown loses queued events

`queue_enabled` defaults to `True`. The worker is a daemon thread and no `atexit` hook is registered, so a script that
returns without `shutdown()` (or `flush()`) can drop everything still queued. Measured: a script logging 5 INFO lines
and exiting printed 0 lines without `shutdown()`, 5 lines with it; 2000 lines without `shutdown()` printed 0. With
`queue_enabled=False` the lines are written inline and survive. Always pair `init` with `shutdown` (try/finally, or
a framework exit hook). Use `flush()` as a checkpoint before long work, a fork, or `os._exit`.

## 2. Logging calls

```python
logger = log.getLogger("app.billing")  # LoggerProxy, name is free text
logger.info("charged %s cents for %s", 1250, "u-42", extra={"order": 5})
logger.debug("lazy args are only %-formatted by the pipeline")
logger.log("ERROR", "level by name, int or LogLevel")
try:
    1 / 0
except ZeroDivisionError:
    logger.exception("failed")  # ERROR + exc_info=True
    logger.error("failed", exc_info=True)  # same capture
logger.warning("where am I", stack_info=True)  # attaches the call stack
```

Methods: `debug info warning error critical exception log setLevel`. Keyword-only: `exc_info` (`True`, an exception
instance or a 3-tuple), `stack_info`, `stacklevel`, `extra` (mapping, lands in the event `extra`; its top-level keys go through the scrubber like every other extra).
Every call returns a `ProcessResult(ok, event_id, reason, queued, failed_adapters)`. Do not ignore `ok=False` in
tests: `reason` is `logger_level`, `rate_limited` or `queue_full`.

Three gates, in order:

1. Proxy level (`logger.setLevel(...)`, default DEBUG). Below it: `ok=False, reason="logger_level"`, nothing happens.
2. Rate limiter (`RuntimeConfig.rate_limit`), then the queue.
3. Per-sink thresholds: `console_level` (default INFO), `backend_level` (journald/eventlog, default WARNING),
   `graylog_level`. Measured: a DEBUG call with `console_level=INFO` returns `ok=True`, prints nothing, and still lands
   in the ring buffer (so a dump shows it).

Traps:

- `getLogger` returns a NEW proxy on every call, so `setLevel` on one does not affect another with the same name.
- A proxy is bound to the runtime that existed when it was created. After `shutdown()` + `init()` an old proxy keeps
  writing to the OLD runtime's adapters (measured: lines went to the first runtime's stream, not the second's).
  Create loggers after `init`, never at import time of a module that can be imported before `init`, and re-create
  them when a test re-inits.
- `getLogger` before `init` raises.

## 3. Context: `bind(...)`

`init` seeds a base context: `service`, `environment`, `job_id="bootstrap"`, plus `user_name`, `hostname`,
`process_id`, `process_id_chain`. Every event carries the current context.

```python
with log.bind(job_id="import-7", request_id="r-1", user_id="u-9", trace_id="t-1", span_id="s-1", extra={"tenant": "a"}) as ctx:
    logger.info("in scope")
    with log.bind(request_id="r-2"):  # child frame inherits, overrides request_id
        logger.info("nested")
    logger.info("back to r-1")
```

Fields: `service environment job_id request_id user_id user_name hostname process_id process_id_chain trace_id span_id
extra`. Rules (measured):

- `bind` is a context manager and restores the parent frame on exit. It yields the new `LogContext`.
- A child inherits every field it does not override; `None` values are ignored (cannot clear a field).
- `extra=` on `bind` REPLACES the parent's context `extra` in that frame, it does not merge: after
  `bind(extra={"tenant": "a"})` then a nested `bind(extra={"step": 2})`, the inner frame holds only `{"step": 2}`.
  Pass the merged dict yourself when you want both.
- Blank `service`, `environment` or `job_id` raises `ValueError: service must not be empty`.
- Per-call `logger.x(..., extra={...})` is separate: it goes to the event `extra`, not the context.
- asyncio: tasks copy the context at creation, so `create_task` and `asyncio.run` inside a `bind` see it.
- Threads: a new `threading.Thread` or a `ThreadPoolExecutor.submit` does NOT inherit it; events there carry the
  bootstrap context (`job_id="bootstrap"`). Propagate explicitly:
  `executor.submit(contextvars.copy_context().run, fn, *args)` or `Thread(target=ctx.run, args=(fn, ...))`.
  Measured: `copy_context().run` kept `job_id=j7, request_id=r7`; a plain `submit` fell back to bootstrap.

## 4. Stdlib `logging` bridge

```python
import logging
from lib_log_rich.runtime import attach_std_logging

log.init(log.RuntimeConfig(service="orders", environment="prod", console_level="DEBUG"))
handler = attach_std_logging()  # root logger, idempotent
logging.getLogger("thirdparty.sub").debug("seen %s", "here", extra={"req": 7})
```

`attach_std_logging(*, logger=None, handler_level=None, logger_level=<min level>, propagate=False)`:

- Adds one `StdlibLoggingHandler` to `logger` (root by default); a second call returns the same handler.
- `logger_level` default: sets the target logger's level to `get_minimum_log_level()`, the lowest threshold among the
  ENABLED sinks (console always; backend/Graylog only when enabled). Pass an explicit level, or `None` to leave the
  logger untouched.
- `propagate` is set on the target logger, default `False` (no duplicate output through other handlers).
- A third-party logger with `propagate=False` never reaches the root handler. Attach to it directly:
  `attach_std_logging(logger=logging.getLogger("thatlib"))`.
- `handler_level` sets the handler's own threshold; unset means all records reach the bridge.
- Needs an initialised runtime when `logger_level` is left at its default (it calls `get_minimum_log_level()`, which
  raises before `init`).
- Records from logger names `lib_log_rich` / `lib_log_rich.*` and records with `lib_log_rich_skip=True` are ignored
  (loop protection). Records arriving while no runtime exists are dropped silently.
- Record `extra` fields plus `pathname lineno funcName filename module` go into the event `extra`; `exc_info` and
  `stack_info` are forwarded. Level numbers map to lib_log_rich levels; an unknown custom level maps to INFO with
  `stdlib_levelno` / `stdlib_levelname` in `extra`.
- The handler stays on the root logger after `shutdown()` and then drops records silently. Remove it in teardown:
  `logging.getLogger().removeHandler(handler)`.

### Why stdlib DEBUG records vanish before reaching lib_log_rich

The stdlib root logger defaults to WARNING and filters by logger level BEFORE any handler runs. A bare
`root.addHandler(StdlibLoggingHandler())` therefore only ever sees WARNING and above, whatever `console_level` is.
Measured with `console_level="DEBUG"` (`get_minimum_log_level()` returned `10`, root level `WARNING`):

```
manual handler:   thirdparty.debug("manual-debug")      -> not delivered
                  thirdparty.warning("manual-warning")  -> delivered
attach_std_logging():  root level -> DEBUG
                  thirdparty.sub.debug("attached-debug") -> delivered
```

Also not delivered: a logger whose own level is higher than the record (`logging.getLogger("tp2").setLevel(ERROR)`
then `.warning(...)`), because the stdlib filters at that logger first. Use `attach_std_logging()` (it lowers the root
level for you), or set the level yourself with `get_minimum_log_level()`, and keep the sink thresholds in
`RuntimeConfig` as the only filter. Lowering the root level also lets other root handlers see DEBUG, so remove
`logging.basicConfig` handlers that would print twice.

## 5. Queue

`queue_enabled=True` (default): the calling thread scrubs the event and appends it to the ring buffer, then enqueues
it; one daemon worker thread does the fan-out to console, journald, Graylog and the Event Log. `queue_enabled=False`: fan-out runs inline in the caller (use for tiny scripts and
tests; adapters then must tolerate your threads).

| Option (env `LOG_QUEUE_*`) | Default   | Effect                                                                                                                                                    |
|----------------------------|-----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `queue_maxsize`            | 2048      | Capacity before the full policy applies.                                                                                                                  |
| `queue_full_policy`        | `"block"` | `"block"` waits `queue_put_timeout`, `"drop"` rejects at once. Other values fail validation (`Invalid queue policy: 'bogus'; must be 'block' or 'drop'`). |
| `queue_put_timeout`        | 1.0 s     | Max producer wait under `block`; `<= 0` or `None` waits indefinitely.                                                                                     |
| `queue_stop_timeout`       | 5.0 s     | Drain deadline in `shutdown()`/`flush()`; on expiry remaining events are dropped.                                                                         |

Measured with a 50 ms-per-event console, `queue_maxsize=2`, 20 events:

```
drop,  put_timeout 1.0 : producer 0.00 s, ok=2,  18 x reason "queue_full"
block, put_timeout 0.01: producer 0.17 s, ok=6,  14 x reason "queue_full"
block, put_timeout 1.0 : producer 0.85 s, ok=20, 0 dropped
```

A dropped event returns `ProcessResult(ok=False, reason="queue_full")`, bumps `drops_by_reason["queue_full"]`, and the
diagnostic hook receives `queue_full` and `queue_dropped` (both payload `event_id logger level`). Worker faults emit
`queue_worker_error` and flip the queue to degraded drop mode (`queue_degraded_drop_mode`, payload `reason`); it
recovers after `failure_reset_after` seconds of clean fan-out. Shutdown overrun emits `queue_shutdown_timeout`.
Size `queue_maxsize` for your burst, pick `drop` for latency-critical paths, and alert on `queue_dropped`.

## 6. Multiprocessing and subprocesses

One runtime per process; each process must `shutdown()` what it initialised. The context stamps `process_id` and a
`process_id_chain` (parent PID first, child PID appended by the child on its first event).

Spawn (Windows, macOS, `set_start_method("spawn")`): the child is a fresh interpreter. `init` in the child, `shutdown`
in `finally`. Measured, exit 0, event delivered:

```python
def worker(job_id: str) -> None:
    log.init(log.RuntimeConfig(service="svc", environment="prod"))
    try:
        with log.bind(job_id=job_id):
            log.getLogger("worker").info("spawned %s", job_id)
    finally:
        log.shutdown()
```

Fork (Linux default for `multiprocessing` before 3.14): the child inherits the parent's runtime object but NOT its
queue worker thread. With `queue_enabled=True` (default) child events are enqueued and never drained: measured
`TimeoutError: Queue did not drain within 2s` from `flush(timeout=2)`, and a child that logs and exits prints nothing
(exit code 0). Pick one:

- Parent with `queue_enabled=False` (child events are written inline; measured, delivered, `process_id` = child PID,
  `process_id_chain` = `[parent, child]`).
- Re-initialise in the child: `log.shutdown(); log.init(config)` (measured: delivered). Set a small
  `queue_stop_timeout` such as `0.2` in the config, otherwise the child's `shutdown()` waits the full 5 s on the dead
  worker.
- Use `spawn`/`forkserver` and the spawn pattern above.

Propagating a context stack to a child: serialise from the RUNTIME's binder and restore into the child's runtime
binder after the child's `init`:

```python
from lib_log_rich.runtime import current_runtime

with log.bind(job_id="job-9", request_id="r-1"):
    payload = current_runtime().binder.serialize()  # {"version": 1, "stack": [...]}, JSON-safe
# pass payload as a Process arg, then in the child, after log.init(...):
current_runtime().binder.deserialize(payload)
log.getLogger("c").info("restored")  # job_id=job-9, request_id=r-1
```

Measured: the child event carried `job_id=job-9`, `request_id=r-1`, `process_id_chain=[parent_pid, child_pid]`.
A standalone `ContextBinder()` is separate from the runtime and never affects `getLogger` output. Each process logs
to its own sinks; there is no cross-process funnel, so file-like sinks written by several processes need their own
coordination.

## 7. Streaming console for GUIs and async consumers

Replace the terminal console with a queue via `RuntimeConfig.console_adapter_factory`, a callable
`(ConsoleAppearance) -> console adapter`. Pass the appearance fields through so styling stays consistent.
`ConsoleAppearance` has `force_color no_color theme styles format_preset format_template stream stream_target`
(no `console_width`).

```python
import queue
from lib_log_rich.runtime import QueueConsoleAdapter, ConsoleAppearance

lines: "queue.Queue[str]" = queue.Queue(maxsize=1024)


def factory(a: ConsoleAppearance) -> QueueConsoleAdapter:
    return QueueConsoleAdapter(
        lines,
        export_style="ansi",
        force_color=a.force_color,
        no_color=a.no_color,
        styles=a.styles,
        format_preset=a.format_preset,
        format_template=a.format_template,
    )


log.init(log.RuntimeConfig(service="gui", environment="dev", console_adapter_factory=factory, no_color=True))
log.getLogger("stream").info("hello queue")
log.flush()
lines.get_nowait()  # '[15:20:17][INFO]: hello queue'
```

- `QueueConsoleAdapter(queue.Queue)`: `put` blocks while full; drain it on another thread.
- `AsyncQueueConsoleAdapter(asyncio.Queue, ..., on_drop=callable | None)`: uses `put_nowait`; when full the chunk is
  dropped and `on_drop(chunk)` is called (measured: maxsize 2, 4 events, 2 kept, `on_drop` called 2 times).
- `export_style="html"` emits a complete HTML document per event (`<!DOCTYPE html>...`), not a bare fragment; strip or
  wrap it in the consumer. Treat it as trusted output only.
- A custom adapter needs `emit(event, *, colorize)` and `flush()`; without `flush` `shutdown()` raises
  `AttributeError`.
- In async code call `await log.flush_async()` and `await log.shutdown_async()`.

## 8. Observability

```python
log.max_level_seen()  # LogLevel or None; counts only events that passed the proxy and rate gates
snap = log.severity_snapshot()
snap.highest, snap.total_events, snap.counts, snap.thresholds
snap.dropped_total, snap.drops_by_reason, snap.drops_by_level, snap.drops_by_reason_and_level
log.reset_severity_metrics()  # zero the counters (e.g. per batch)
```

Measured with `rate_limit=(2, 60.0)`, `queue_enabled=False`, 4 INFO + 1 WARNING + 1 ERROR:
`max_level_seen()=ERROR(40)`, `total_events=4`, `counts={INFO: 2, WARNING: 1, ERROR: 1}`,
`thresholds={WARNING: 2, ERROR: 1}` (cumulative "greater or equal" counts), `dropped_total=2`,
`drops_by_reason={rate_limited: 2, queue_full: 0, adapter_error: 0}`; after `reset_severity_metrics()` both
`max_level_seen()` is `None` and `total_events` is 0. Typical use: set the process exit code from
`max_level_seen() >= LogLevel.ERROR` at the end of a batch job. The counters live on the runtime; they die with
`shutdown()`.

Diagnostic hook: `RuntimeConfig(diagnostic_hook=fn)` with `fn(event: str, payload: dict) -> None`. Keep it fast and
never call lib_log_rich from inside it; exceptions it raises are swallowed. Events seen: `queued` (queue on, payload
`event_id logger`), `emitted` (queue off, adds `level`), `rate_limited`, `queue_full`, `queue_dropped`,
`queue_worker_error`, `queue_degraded_drop_mode`, `queue_drop_callback_error`, `queue_shutdown_timeout`, plus payload
truncation notices (`message_truncated`, `exc_info_truncated`, `*_value_truncated`). Measured with queue off:
`{'emitted': 4, 'rate_limited': 2}`. Read payload keys with `.get()`; the set is additive.

## 9. Testing code that uses lib_log_rich

The runtime is process-global. A test that calls `init` and fails or returns before `shutdown` poisons every later
test with `RuntimeError: ... cannot be called twice without shutdown()`. Always tear down in a fixture.

```python
import io, pytest, lib_log_rich as log
from lib_log_rich.runtime import is_initialised


@pytest.fixture
def captured():
    buf = io.StringIO()
    log.init(log.RuntimeConfig(service="test", environment="ci", console_stream="custom", console_stream_target=buf, no_color=True, queue_enabled=False))
    try:
        yield buf
    finally:
        if is_initialised():
            log.shutdown()


def test_output(captured):
    log.getLogger("app").warning("disk %s", "full")
    assert "disk full" in captured.getvalue()
```

Measured: this fixture passes; a test that called `init` without `shutdown` made the next `init` fail with the
duplicate-init error (`1 failed, 3 passed`).

- Prefer `queue_enabled=False` in tests so output is synchronous. With the queue on, call `log.flush()` before
  asserting; measured: `flush()` then the buffer contained the line.
- `console_stream="none"` discards console output (use when only asserting severity counters or a `dump`);
  `console_stream="custom"` plus `console_stream_target=<StringIO>` captures it. `console_stream="stderr"` (default)
  binds the stream at `init`.
- Click `CliRunner`: do `init` and `shutdown` inside the command (try/finally). Measured: stderr then holds the log
  line and stdout only `done\n`. A command that inits without shutdown passes once and fails with exit code 1
  (the duplicate-init `RuntimeError`) on the second `invoke`.
- Loggers created in module scope outlive a re-init (section 2 trap); create them inside the code under test after
  `init`.
- Remove any `attach_std_logging()` handler from the root logger in teardown, or later tests inherit a bridge with no
  runtime behind it (it drops silently).
- Use `validate_config(config)` in a test to assert a configuration is refused without touching the global runtime.
