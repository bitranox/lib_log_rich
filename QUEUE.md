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
- **Full policy**: choose `"block"` to apply backpressure or `"drop"` to shed
  load once the queue is full.
- **Producer timeout**: `queue_put_timeout` controls how long blocking producers
  wait for space; the default is 1 second to avoid unbounded stalls.
- **Stop timeout**: `queue_stop_timeout` governs how long shutdown waits for
  drain operations before forcing drop behaviour. The runtime enforces a
  five-second default; pass `None` or a value `<= 0` when you intentionally want
  to block indefinitely.
- **Diagnostics**: optional `diagnostic_hook` receives queue lifecycle events
  (drops, worker failures, degraded mode, drop-handler crashes).

## 2. Configuration reference

| Option/Env var                                 | Default   | Description                                                                                                                                               |
|------------------------------------------------|-----------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `queue_enabled`, `LOG_QUEUE_ENABLED`           | `True`    | Disable to run fan-out inline. Useful only when you are certain all adapters are thread-safe for your workload.                                           |
| `queue_maxsize`, `LOG_QUEUE_MAXSIZE`           | `2048`    | Maximum number of queued events before the full policy applies. Increase for bursty producers with sustained consumer capacity.                           |
| `queue_full_policy`, `LOG_QUEUE_FULL_POLICY`   | `"block"` | `"block"` waits for space, `"drop"` rejects additional events immediately.                                                                                       |
| `queue_put_timeout`, `LOG_QUEUE_PUT_TIMEOUT`   | `1.0`     | How long blocking producers wait before the queue reports failure. Values `<= 0` make the queue wait indefinitely.                             |
| `queue_stop_timeout`, `LOG_QUEUE_STOP_TIMEOUT` | `5.0`     | Drain deadline during shutdown; values `<= 0` wait indefinitely.                                                                                          |
| `diagnostic_hook`                              | `None`    | Optional callable `Callable[[str, dict[str, Any]], None]` invoked with diagnostic events.                                                                 |
| `failure_reset_after`                          | `30.0`    | Cooldown window: after a failure the adapter keeps `worker_failed=True` and degraded drop mode active until it observes this many seconds of successful fan-out. Set `None` to disable auto-reset; you must stop/start the queue manually to clear the flag. |

### CLI / Environment overrides

With the default `failure_reset_after=30.0`, a burst of errors places the queue in degraded mode. If the worker handles clean events for half a minute the adapter clears `worker_failed` automatically. Lower values shorten the recovery window; higher values keep backpressure in place longer so operators can intervene before blocking resumes.

All options above honour both keyword arguments to `init` and the corresponding
`LOG_QUEUE_*` environment variables. Command-line tools such as `lib_log_rich
logdemo` expose `--queue-enabled`, `--queue-maxsize`, and similar flags.

## 3. Worker lifecycle

1. **Start-up** – `QueueAdapter.start()` launches a daemon thread, resets the
   `worker_failed` flag, and ensures the queue is empty.
2. **Normal operation** – Producers call `queue.put(event)`; the worker consumes
   events and fans out to the configured adapters. `worker_failed` remains
   `False` and diagnostics stay quiet.
3. **Producer backpressure** – When `queue_full_policy="block"`, producers wait
   up to `queue_put_timeout` seconds for space. On timeout a diagnostic
   `queue_dropped` event is emitted (via the drop callback) and `put` returns
   `False`.
4. **Worker failure** – Exceptions raised by the worker are logged once, the
   adapter sets `worker_failed=True`, records the timestamp, and emits a
   `queue_worker_error` diagnostic containing the event id, logger name, and the
   exception representation.
5. **Degraded mode** – While `worker_failed=True`, blocking producers
   automatically switch to drop mode. The first time this happens the adapter
   emits `queue_degraded_drop_mode` so you can alert or restart the worker. The
   worker thread keeps running; auto-reset just clears `worker_failed` after the
   cooldown when subsequent events complete successfully. Once the worker
   recovers, the adapter clears the flag, resets degraded mode, and resumes the
   configured full policy.

If you need to clear `worker_failed` immediately (e.g., after a manual fix), call `queue.stop(drain=True)` followed by `queue.start()`, or rebuild the runtime.
6. **Shutdown** – `queue.stop(drain=True)` drains pending events up to
   `queue_stop_timeout`. If the deadline expires, the adapter flips into drop
   mode, drains the remaining events locally (invoking the on-drop handler), and
   emits `queue_dropped` diagnostics.

## 4. Diagnostics catalogue

Hook `diagnostic_hook` to surface the following events in metrics/alerts:

| Event name | Payload fields | Meaning |
| --- | --- | --- |
| `queue_dropped` | `event_id`, `logger`, `level` | Triggered when an event is dropped due to capacity exhaustion. |
| `queue_worker_error` | `event_id`, `logger`, `level`, `exception` | Worker’s fan-out callback raised. Worker remains alive but `worker_failed=True`. |
| `queue_degraded_drop_mode` | `reason` | Blocking producers switched to drop mode because the worker is still failed. |
| `queue_drop_callback_error` | `event_id`, `logger`, `exception` | The on-drop callback raised; the adapter logs the failure and continues. |
| `queue_shutdown_timeout` | `timeout`, `drain_completed` | `stop()` could not join the worker within the allotted timeout. Shutdown fails fast so callers can retry or escalate. |
| `queue_worker_recovered` | *(emitted via your own hook if desired)* | Not currently emitted automatically; observe `worker_failed` becoming `False` via health checks when needed. |

Combine these diagnostics with either a direct handle on the adapter (when you
compose via `_factories`) or the runtime API `runtime.current_runtime().queue`
for visibility in health checks.

## 5. Selecting policies

| Scenario | Recommended settings |
| --- | --- |
| Latency-sensitive, low-volume | Keep `queue_enabled=True`, `queue_maxsize` small, `queue_full_policy="block"`, `queue_put_timeout` near default. |
| Burst workloads with tolerable loss | Increase `queue_maxsize`, set `queue_full_policy="drop"`, and allow the drop handler to record metrics. |
| Offline batch with large fan-out | Increase `queue_stop_timeout` to allow drains, consider `queue_put_timeout=None` only if you can monitor degraded mode carefully. |

## 6. Monitoring templates

```python
from collections import Counter
from typing import Any

import lib_log_rich as log

failures = Counter()

def diagnostics(name: str, payload: dict[str, Any]) -> None:
    failures[name] += 1
    if name == "queue_degraded_drop_mode":
        # escalate to alerting (PagerDuty, Slack, etc.)
        ...

config = log.RuntimeConfig(..., queue_enabled=True, diagnostic_hook=diagnostics)
log.init(config)
```

Combine diagnostic counters with the `QueueAdapter.worker_failed` property or
exported metrics to warn operators before log fan-out is silently degraded.

## 7. Advanced integration

When you embed adapters manually (e.g., through `_composition` for custom
composition roots), you can inject your own queue worker or wrap the provided
`QueueAdapter`:

```python
from lib_log_rich.adapters.queue import QueueAdapter

adapter = QueueAdapter(
    worker=my_worker,
    maxsize=8192,
    drop_policy="block",
    timeout=2.5,
    diagnostic=my_diagnostics,
)
adapter.start()
```

Remember to call `adapter.stop(drain=True)` during shutdown and to respect the
default timeout behaviour if you rely on the blocking policy.

## 8. Migrating from earlier versions

- Prior versions defaulted `queue_put_timeout` to `None`, which could hang
  producers indefinitely. Audit any workloads that depended on the old
  behaviour and set `queue_put_timeout=None` explicitly if necessary.
- Update monitoring to watch for `queue_degraded_drop_mode`; this is the primary
  indicator that the worker is unhealthy but still running.
- Ensure your `diagnostic_hook` is idempotent and defensive—exceptions raised by
  diagnostics are logged and suppressed to keep the queue alive.

With these additions the queue offers clearer failure semantics, bounded
backpressure, and actionable signals when downstream adapters misbehave.
