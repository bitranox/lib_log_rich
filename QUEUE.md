# Queue Behaviour Guide

`lib_log_rich` can fan out log events synchronously or through a dedicated queue
thread. This document collects every queue-related configuration knob, explains
how the worker behaves under normal and failure conditions, and highlights the
diagnostic events you can hook into for monitoring.

## 1. Overview

When `queue_enabled=True` (the default), `lib_log_rich.init(RuntimeConfig(...))` wires
:class:`QueueAdapter` in front of the downstream adapters (Rich console,
structured backends, Graylog). Producers enqueue log events while the background
worker thread performs fan-out. This decouples application threads from I/O latency,
makes batching possible, and keeps thread-unsafe adapters away from call sites in the
rest of the process. For multi-process propagation, apply the recipes in
[SUBPROCESSES.md](SUBPROCESSES.md) so each child can deliver events safely.

Key elements:

- **Queue capacity**: bounded buffer (`queue_maxsize`, default 2048) to absorb
  bursts before the full policy applies.
- **Console compatibility**: queue-backed console adapters reuse the Rich
  formatter and level threshold, so streamed output matches the regular console.
- **Visibility on async console drops**: the asyncio console adapter exposes an
  `on_drop` hook and logs a warning whenever the queue overflows, so GUI/SSE
  consumers can spot backpressure issues.
- **Full policy**: choose `QueuePolicy.BLOCK` to apply backpressure or
  `QueuePolicy.DROP` to shed load once the queue is full (`QueuePolicy` is in
  `lib_log_rich.domain`).
- **Producer timeout**: `queue_put_timeout` controls how long blocking producers
  wait for space; the default is 1 second to avoid unbounded stalls.
- **Stop timeout**: `queue_stop_timeout` governs how long shutdown waits for
  drain operations before forcing drop behaviour. The runtime enforces a
  five-second default; pass `None` or a value `<= 0` when you intentionally want
  to block indefinitely.
- **Diagnostics**: optional `diagnostic_hook` receives queue lifecycle events
  (drops, worker failures, degraded mode, drop-handler crashes).

## 2. Configuration reference

| Option/Env var                                 | Default             | Description                                                                                                                                                                                                                                                  |
|------------------------------------------------|---------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `queue_enabled`, `LOG_QUEUE_ENABLED`           | `True`              | Disable to run fan-out inline. Useful only when you are certain all adapters are thread-safe for your workload.                                                                                                                                              |
| `queue_maxsize`, `LOG_QUEUE_MAXSIZE`           | `2048`              | Maximum number of queued events before the full policy applies. Increase for bursty producers with sustained consumer capacity.                                                                                                                              |
| `queue_full_policy`, `LOG_QUEUE_FULL_POLICY`   | `QueuePolicy.BLOCK` | `QueuePolicy.BLOCK` waits for space, `QueuePolicy.DROP` rejects additional events immediately. `RuntimeConfig` also parses the value strings `"block"` and `"drop"`, which are what the environment variable takes.                                          |
| `queue_put_timeout`, `LOG_QUEUE_PUT_TIMEOUT`   | `1.0`               | How long blocking producers wait before the queue reports failure. Values `<= 0` make the queue wait indefinitely.                                                                                                                                           |
| `queue_stop_timeout`, `LOG_QUEUE_STOP_TIMEOUT` | `5.0`               | Drain deadline during shutdown; values `<= 0` wait indefinitely.                                                                                                                                                                                             |
| `diagnostic_hook`                              | `None`              | Optional callable `Callable[[str, Mapping[str, object]], None]` invoked with diagnostic events.                                                                                                                                                              |
| `failure_reset_after` (`QueueAdapter` only)    | `30.0`              | Cooldown window: after a failure the adapter keeps `worker_failed=True` and degraded drop mode active until it observes this many seconds of successful fan-out. Set `None` to disable auto-reset; you must stop/start the queue manually to clear the flag. |

### CLI / Environment overrides

With the default `failure_reset_after=30.0`, a burst of errors places the queue in degraded mode. If the worker handles clean events for half a minute the adapter clears `worker_failed` automatically. Lower values shorten the recovery window; higher values keep backpressure in place longer so operators can intervene before blocking resumes.

The `queue_*` options above are `RuntimeConfig` fields, and each has the
`LOG_QUEUE_*` environment variable shown beside it, which overrides the field at
`init()`. `diagnostic_hook` has no environment variable. `failure_reset_after` is
a `QueueAdapter` constructor argument: the runtime builds its queue with the
default of 30 seconds. The `lib_log_rich stresstest` TUI offers the queue settings
as form fields, pre-filled from the `LOG_QUEUE_*` variables.

## 3. Flush vs Shutdown

`lib_log_rich` provides two ways to ensure all pending events are processed:

| Aspect           | `flush()`                                                                           | `shutdown()`                                     |
|------------------|-------------------------------------------------------------------------------------|--------------------------------------------------|
| Queue            | `wait_until_idle()` - waits for drain, keeps running                                | `stop(drain=True)` - drains and stops the worker |
| Console          | Flushes streams (stdout/stderr/custom)                                              | Flushes streams                                  |
| Graylog          | Closes TCP connection (reconnects lazily on next emit)                              | Closes TCP connection                            |
| Ring buffer      | Optional via `flush_ring_buffer=True` - appends to the checkpoint and clears buffer | Appends to the checkpoint and clears buffer      |
| Runtime state    | **Preserved** - logging remains active                                              | **Cleared** - must call `init()` again           |
| Typical use case | Checkpoint before long operation, ensure delivery                                   | Application shutdown, end of process             |

The ring-buffer rows apply only to a ring buffer that has a checkpoint. `init()`
configures none, so through the runtime both ring-buffer flushes do nothing and
the buffer keeps its events. Persistence is available to code that builds
`RingBuffer(checkpoint=JsonlCheckpoint(path))` itself (`RingBuffer` from
`lib_log_rich.domain`, `JsonlCheckpoint` from `lib_log_rich.adapters`).

### flush() API

```python
import lib_log_rich as log

# Synchronous flush (blocks until queue drains or timeout)
log.flush(timeout=10.0)  # Wait up to 10s for queue to drain
log.flush(flush_ring_buffer=True)  # Also persist the ring buffer (no-op through init(): no checkpoint)

# Async flush (for use within async contexts)
await log.flush_async(timeout=5.0)
```

**Parameters:**
- `timeout` (`float | None`, default: `None`) - Maximum seconds to wait for queue to drain. `None` uses the runtime's `queue_stop_timeout`, or 5 seconds when that is unset.
- `flush_ring_buffer` (`bool`, default: `False`) - When `True`, appends ring buffer events to the checkpoint and clears the buffer, so each event is written to the checkpoint only once. No-op when no checkpoint is configured (buffer preserved), which is always the case for the runtime `init()` builds.

**Raises:**
- `TimeoutError` - If the queue doesn't drain within the timeout.
- `RuntimeError` - If `flush()` (sync variant) is called from within an active event loop. Use `flush_async()` instead.

**Execution order:**
1. Wait for queue to drain (all pending events processed)
2. Flush console streams (stdout, stderr, custom streams)
3. Flush Graylog adapter (close TCP socket; reconnects lazily on next emit)
4. Flush ring buffer (if `flush_ring_buffer=True`)

## 4. Worker lifecycle

1. **Start-up** - `QueueAdapter.start()` launches a daemon thread, resets the
   `worker_failed` flag, and ensures the queue is empty.
2. **Normal operation** - Producers call `queue.put(event)`; the worker consumes
   events and fans out to the configured adapters. `worker_failed` remains
   `False` and diagnostics stay quiet.
3. **Producer backpressure** - When `queue_full_policy=QueuePolicy.BLOCK`, producers wait
   up to `queue_put_timeout` seconds for space. On timeout a diagnostic
   `queue_dropped` event is emitted (via the drop callback) and `put` returns
   `False`.
4. **Worker failure** - Exceptions raised by the worker are logged once, the
   adapter sets `worker_failed=True`, records the timestamp, and emits a
   `queue_worker_error` diagnostic containing the event id, logger name, and the
   exception representation.
5. **Degraded mode** - While `worker_failed=True`, blocking producers
   automatically switch to drop mode. The first time this happens the adapter
   emits `queue_degraded_drop_mode` so you can alert or restart the worker. The
   worker thread keeps running; auto-reset just clears `worker_failed` after the
   cooldown when subsequent events complete successfully. Once the worker
   recovers, the adapter clears the flag, resets degraded mode, and resumes the
   configured full policy.

If you need to clear `worker_failed` immediately (e.g., after a manual fix), call `queue.stop(drain=True)` followed by `queue.start()`, or rebuild the runtime.
6. **Shutdown** - `queue.stop(drain=True)` drains pending events up to
   `queue_stop_timeout`. If the deadline expires, the adapter flips into drop
   mode, drains the remaining events locally (invoking the on-drop handler), and
   emits `queue_dropped` diagnostics.

## 5. Diagnostics catalogue

Hook `diagnostic_hook` to surface the following queue events in metrics/alerts (the full list, including adapter and payload-limit events, is in [DIAGNOSTIC.md](DIAGNOSTIC.md#event-catalogue)):

| Event name                  | Payload fields                             | Meaning                                                                                                                                     |
|-----------------------------|--------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| `queue_full`                | `event_id`, `logger`, `level`              | A producer's put was refused (drop policy, or a block-policy put that timed out). The caller gets `ok=False, reason=DropReason.QUEUE_FULL`. |
| `queue_dropped`             | `event_id`, `logger`, `level`              | Triggered when an event is dropped due to capacity exhaustion; fires alongside `queue_full`.                                                |
| `queue_worker_error`        | `event_id`, `logger`, `exception`          | Worker's fan-out callback raised. Worker remains alive but `worker_failed=True`.                                                            |
| `queue_degraded_drop_mode`  | `reason`                                   | Blocking producers switched to drop mode because the worker is still failed.                                                                |
| `queue_drop_callback_error` | `event_id`, `logger`, `level`, `exception` | The on-drop callback raised; the adapter logs the failure and continues.                                                                    |
| `queue_shutdown_timeout`    | `timeout`, `drain_completed`               | `stop()` could not join the worker within the allotted timeout. Shutdown fails fast so callers can retry or escalate.                       |
| `queue_worker_recovered`    | *(emitted via your own hook if desired)*   | Not currently emitted automatically; observe `worker_failed` becoming `False` via health checks when needed.                                |

Combine these diagnostics with either a direct handle on the adapter (when you
compose via `_factories`) or the runtime API `runtime.current_runtime().queue`
for visibility in health checks.

## 6. Selecting policies

| Scenario                            | Recommended settings                                                                                                              |
|-------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| Latency-sensitive, low-volume       | Keep `queue_enabled=True`, `queue_maxsize` small, `queue_full_policy=QueuePolicy.BLOCK`, `queue_put_timeout` near default.        |
| Burst workloads with tolerable loss | Increase `queue_maxsize`, set `queue_full_policy=QueuePolicy.DROP`, and allow the drop handler to record metrics.                 |
| Offline batch with large fan-out    | Increase `queue_stop_timeout` to allow drains, consider `queue_put_timeout=None` only if you can monitor degraded mode carefully. |

## 7. Monitoring templates

```python
from collections import Counter
from collections.abc import Mapping

import lib_log_rich as log

failures: Counter[str] = Counter()


def diagnostics(name: str, payload: Mapping[str, object]) -> None:
    failures[name] += 1
    if name == "queue_degraded_drop_mode":
        # escalate to alerting (PagerDuty, Slack, etc.)
        ...


config = log.RuntimeConfig(service="worker", environment="prod", queue_enabled=True, diagnostic_hook=diagnostics)
log.init(config)
```

Combine diagnostic counters with the `QueueAdapter.worker_failed` property or
exported metrics to warn operators before log fan-out is silently degraded.

## 8. Advanced integration

When you embed adapters manually (e.g., through `_composition` for custom
composition roots), you can inject your own queue worker or wrap the provided
`QueueAdapter`:

```python
from collections.abc import Mapping

from lib_log_rich.adapters.queue import QueueAdapter
from lib_log_rich.domain import LogEvent, QueuePolicy


def my_worker(event: LogEvent) -> None: ...  # fan the event out to your adapters


def my_diagnostics(name: str, payload: Mapping[str, object]) -> None: ...  # forward to metrics


adapter = QueueAdapter(
    worker=my_worker,
    maxsize=8192,
    drop_policy=QueuePolicy.BLOCK,
    timeout=2.5,
    diagnostic=my_diagnostics,
)
adapter.start()
```

Remember to call `adapter.stop(drain=True)` during shutdown and to respect the
default timeout behaviour if you rely on the blocking policy.

## 9. Operational checklist

- `queue_put_timeout` defaults to 1 second, so a stalled worker cannot hang
  producers indefinitely. Set `queue_put_timeout=None` only for workloads that
  must block until space frees up.
- Watch for `queue_degraded_drop_mode` in monitoring; it is the primary
  indicator that the worker is unhealthy but still running.
- Keep your `diagnostic_hook` idempotent and defensive - exceptions raised by
  diagnostics are logged and suppressed to keep the queue alive.

Together these give the queue clear failure semantics, bounded backpressure,
and actionable signals when downstream adapters misbehave.
