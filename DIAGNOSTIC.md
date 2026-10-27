# Diagnostic Hook Guide

`lib_log_rich.init(RuntimeConfig(...))` accepts an optional `diagnostic_hook` callback that observes the runtime without modifying it. The hook lets you wire internal telemetry (queue events, rate limiting) into metrics systems, health checks, or debugging dashboards while keeping the logging pipeline decoupled from specific monitoring stacks.

```python
from collections import Counter
from collections.abc import Mapping

import lib_log_rich as log

STATS: Counter[str] = Counter()


def diagnostic(event: str, payload: Mapping[str, object]) -> None:
    STATS[event] += 1
    if event == "rate_limited":
        print("rate limiter engaged", payload)


config = log.RuntimeConfig(
    service="svc",
    environment="dev",
    queue_enabled=False,
    diagnostic_hook=diagnostic,
)
log.init(config)
```

Every time the runtime enqueues, emits, or drops an event, your callback receives a short identifier (`event`) and a payload dictionary with useful fields (for example, `event_id`, `logger`, `level`).

---

## Event catalogue

| Event                                                                                     | When it fires                                                                                                                     | Payload keys                                                                                                                                    | Notes                                                                                                                                                                                                                                                                                             |
|-------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `queued`                                                                                  | The queueing path accepts the event                                                                                               | `event_id`, `logger`                                                                                                                            | Only emitted when `queue_enabled=True`; the hook fires before the background worker processes the event.                                                                                                                                                                                          |
| `emitted`                                                                                 | Synchronous fan-out finishes successfully                                                                                         | `event_id`, `logger`, `level`                                                                                                                   | Sent only when `queue_enabled=False` (immediate fan-out).                                                                                                                                                                                                                                         |
| `rate_limited`                                                                            | The rate limiter rejected the event                                                                                               | `event_id`, `logger`, `level`                                                                                                                   | Triggered regardless of queueing; the logger call returns a `ProcessResult` with `ok=False, reason=DropReason.RATE_LIMITED`.                                                                                                                                                                      |
| `queue_worker_error`                                                                      | The asynchronous worker raised while processing a queued log event                                                                | `event_id`, `logger`, `exception`                                                                                                               | Also flips `QueueAdapter.worker_failed=True`. The flag auto-resets on the first successful event after the cooldown, on clean shutdowns, or when the adapter restarts. The runtime's worker catches sink exceptions itself (they are `adapter_error`), so this needs a failure outside the sinks. |
| `queue_drop_callback_error`                                                               | The `on_drop` callback of a `QueueAdapter` raised                                                                                 | `event_id`, `logger`, `level`, `exception`                                                                                                      | Only for a `QueueAdapter` you build yourself with `on_drop=`: `init()` sets no `on_drop`, so the runtime's queue never emits it. The queue continues draining.                                                                                                                                    |
| `queue_full`                                                                              | A producer's put was refused (drop policy, or a block-policy put that timed out)                                                  | `event_id`, `logger`, `level`                                                                                                                   | The logger call returns `ok=False, reason=DropReason.QUEUE_FULL`; counted in `severity_snapshot()` drops under `DropReason.QUEUE_FULL`.                                                                                                                                                           |
| `queue_dropped`                                                                           | The queue discarded an event                                                                                                      | `event_id`, `logger`, `level`                                                                                                                   | Refused at put: fires just before `queue_full` for the same event. Still pending when `stop()` dropped or could not drain the queue: no `queue_full` follows.                                                                                                                                     |
| `queue_degraded_drop_mode`                                                                | Blocking producers were switched to drop mode because the worker is still failed                                                  | `reason`                                                                                                                                        | Emitted once per degradation; the configured policy returns after recovery.                                                                                                                                                                                                                       |
| `queue_shutdown_timeout`                                                                  | `stop()` could not join the worker within the stop timeout                                                                        | `timeout`, `drain_completed`                                                                                                                    | `shutdown()` then raises `RuntimeError`.                                                                                                                                                                                                                                                          |
| `adapter_error`                                                                           | A sink raised while emitting an event                                                                                             | per sink: `adapter`, `error`, `event_id`, `logger`, `level`; then once per event: `adapters`, `event_id`, `logger`, `level`                     | The other sinks still receive the event and the failure counts as an `adapter_error` drop; the logger call returns `ok=False, reason=DropReason.ADAPTER_ERROR` when the queue is off (with the queue on it already got `queued=True`).                                                            |
| `extra_invalid`                                                                           | `extra` could not be converted to a dict by the process use case                                                                  | `event_id`, `logger`                                                                                                                            | Fires only when the process use case (`runtime.current_runtime().process`) is called directly with an unconvertible `extra`; the event is then logged with an empty `extra`. `getLogger(...).info(..., extra=x)` converts `x` with `dict()` first and raises to the caller, with no diagnostic.   |
| `message_format_failed`                                                                   | `msg % args` raised                                                                                                               | `event_id`, `logger`, `error`, `message`, `args`                                                                                                | The event is still logged, as `<msg> [formatting failed: <error>]`.                                                                                                                                                                                                                               |
| `message_truncated`                                                                       | The message exceeded `payload_limits.message_max_chars`                                                                           | `event_id`, `logger`, `reason`, `original_length`, `new_length`                                                                                 | Only when `truncate_message=True`; otherwise the call raises `ValueError`.                                                                                                                                                                                                                        |
| `exc_info_truncated` / `stack_info_truncated`                                             | A traceback or stack held more than `2 * stacktrace_max_frames` frames, or its text (after that) exceeded `extra_max_value_chars` | `event_id`, `logger`, `frames_removed` (frames compacted); or `event_id`, `logger`, `key`, `reason`, `original_length`, `new_length` (text cut) | The text is compacted, not dropped. Frame compaction keeps the first and last `stacktrace_max_frames` frames and replaces those between with `... truncated K frame(s) ...`. A cut keeps the end of the text (innermost frames and the exception line), which then starts with `[truncated]…`.    |
| `extra_value_truncated` / `context_extra_value_truncated`                                 | An `extra` (or context `extra`) value exceeded its character or depth limit                                                       | `event_id`, `logger`, `key`, `reason`, `original_length`, `new_length`                                                                          | A value inside a nested mapping reports under the same name, `key` being its own key (not a path); `reason` is `length` or `depth`.                                                                                                                                                               |
| `extra_value_truncated_depth_collapsed` / `context_extra_value_truncated_depth_collapsed` | A nested mapping in `extra` (or context `extra`) had a value truncated or collapsed inside it                                     | `event_id`, `logger`, `key`, `depth`                                                                                                            | Fires once per changed nested mapping, after the events for the values inside it; `depth` is its nesting level (1 for a mapping directly under `extra`).                                                                                                                                          |
| `extra_keys_dropped` / `context_extra_keys_dropped`                                       | More keys than `extra_max_keys` (`context_max_keys` for the context variant)                                                      | `event_id`, `logger`, `dropped_keys`, `limit`                                                                                                   | The first keys are kept.                                                                                                                                                                                                                                                                          |
| `extra_total_trimmed`                                                                     | The encoded `extra` mapping exceeded `extra_max_total_bytes`                                                                      | `event_id`, `logger`, `removed`, `limit`                                                                                                        | Keys are removed until it fits. `context_extra_total_trimmed` exists in `DiagnosticEvent` but is not emitted: context `extra` has no byte budget.                                                                                                                                                 |

Future variants will follow the same pattern. Always treat the `payload` keys as an additive contract: code should guard against missing fields to remain compatible with future releases.

---

## Best practices

1. **Keep callbacks fast** - the hook executes on the logging hot path. Expensive work (network calls, long-running computations) should be deferred to another thread or queue.
2. **Handle errors defensively** - the runtime swallows exceptions raised by the hook to protect application logging. Log your own failures or expose metrics so problems do not go unnoticed.
3. **Avoid re-entrancy** - calling into `lib_log_rich` from inside the hook can deadlock. Gather data and hand it off to other systems instead.
4. **Make payloads optional** - iterate with `.get()` or `payload.get("level")` in case future events omit or rename fields.
5. **Know which thread calls the hook** - with the queue on, `adapter_error` and the `queue_*` diagnostics come from the queue worker's thread. A hook may hand them to an event loop and wait for it (Textual's `call_from_thread`, `asyncio.run_coroutine_threadsafe(...).result()`): `shutdown_async()` and `flush_async()` wait for the worker off the loop's thread, so that loop stays free to serve the hook.

---

## Example: Prometheus counters

```python
from collections.abc import Mapping

import prometheus_client

import lib_log_rich as log

EMITTED = prometheus_client.Counter("log_emitted_total", "Synchronous log events", ["level"])
QUEUED = prometheus_client.Counter("log_queued_total", "Queued log events")
RATE_LIMITED = prometheus_client.Counter("log_rate_limited_total", "Rate limited log events")
QUEUE_WORKER_ERRORS = prometheus_client.Counter("log_queue_worker_errors_total", "Queue worker failures")
QUEUE_DROP_ERRORS = prometheus_client.Counter("log_queue_drop_callback_errors_total", "Drop callback failures")


def diagnostic(event: str, payload: Mapping[str, object]) -> None:
    if event == "emitted":
        EMITTED.labels(level=str(payload.get("level", "unknown"))).inc()
    elif event == "queued":
        QUEUED.inc()
    elif event == "rate_limited":
        RATE_LIMITED.inc()
    elif event == "queue_worker_error":
        QUEUE_WORKER_ERRORS.inc()
    elif event == "queue_drop_callback_error":
        QUEUE_DROP_ERRORS.inc()


config = log.RuntimeConfig(
    service="svc",
    environment="prod",
    queue_enabled=True,
    diagnostic_hook=diagnostic,
)
log.init(config)
```

Expose the metrics endpoint with `prometheus_client.start_http_server()` in the surrounding application, and you have immediate visibility into logging flow control.

---

## Example: Health probe

```python
import time
from collections.abc import Mapping

import lib_log_rich as log

last_emit = 0.0


def diagnostic(event: str, payload: Mapping[str, object]) -> None:
    global last_emit
    if event in {"queued", "emitted"}:
        last_emit = time.time()


def is_logging_alive(threshold: float = 30.0) -> bool:
    return time.time() - last_emit <= threshold


config = log.RuntimeConfig(service="svc", environment="live", diagnostic_hook=diagnostic)
log.init(config)
```

`is_logging_alive()` can feed a liveness probe: if no events move through the system for longer than the threshold, flag the service for investigation.

---

## Interplay with queueing

When `queue_enabled=True`, the hook gets `queued` immediately and no `emitted` later: the background worker reports only failures, as `adapter_error` (per sink, then once per event), and counts each failed event as an `adapter_error` drop. Those calls run on the worker thread, so a hook that hands them to another thread must not block on it while that thread waits for `shutdown()`. If the worker raises an exception, `queue_worker_error` fires, `QueueAdapter.worker_failed` flips to `True`, and the queue keeps draining. The runtime's queue clears the flag on the next successful event 30 seconds or more after the last failure (`failure_reset_after` is configurable only on a `QueueAdapter` you build yourself), or call `stop(drain=True)` / `start()` to clear it. A failing sink does not set the flag: the worker reports it as `adapter_error`. On a hand-built `QueueAdapter` with `on_drop=`, a drop handler that explodes triggers `queue_drop_callback_error`, so you can monitor overflow policies separately from worker health.

---

## Troubleshooting

- **No events arrive** - ensure you actually log something and that your callback signature matches `Callable[[str, Mapping[str, object]], None]`. Annotate the payload as `Mapping[str, object]`: a strict type checker rejects a hook declared with `dict[str, Any]`, because the runtime promises only a read-only mapping.
- **My hook raised an exception** - the runtime swallows it; log locally or expose metrics so the error is visible.
- **I need more detail** - augment the hook by inspecting `payload` and, if necessary, capture additional context (for example, request IDs) from your own code before logging.

For the complete runtime wiring and port definitions, see [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md).
