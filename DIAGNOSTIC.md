# Diagnostic Hook Guide

`lib_log_rich.init(RuntimeConfig(...))` accepts an optional `diagnostic_hook` callback that observes the runtime without modifying it. The hook lets you wire internal telemetry (queue events, rate limiting) into metrics systems, health checks, or debugging dashboards while keeping the logging pipeline decoupled from specific monitoring stacks.

```python
from collections import Counter
from typing import Any

import lib_log_rich as log

STATS = Counter()


def diagnostic(event: str, payload: dict[str, Any]) -> None:
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

| Event                      | When it fires                                                          | Payload keys                                              | Notes |
|----------------------------|------------------------------------------------------------------------|-----------------------------------------------------------|-------|
| `queued`                   | The queueing path accepts the event                                   | `event_id`, `logger`                                      | Only emitted when `queue_enabled=True`; the hook fires before the background worker processes the event. |
| `emitted`                  | Synchronous fan-out finishes successfully                             | `event_id`, `logger`, `level`                             | Sent only when `queue_enabled=False` (immediate fan-out). |
| `rate_limited`             | The rate limiter rejected the event                                   | `event_id`, `logger`, `level`                             | Triggered regardless of queueing; the caller receives `{"ok": False, "reason": "rate_limited"}`. |
| `queue_worker_error`       | The asynchronous worker raised while processing a queued log event    | `event_id`, `logger`, `exception`                         | Also flips `QueueAdapter.worker_failed=True`. The flag auto-resets after the configured cooldown, on clean shutdowns, or when the adapter restarts. |
| `queue_drop_callback_error`| The drop callback raised while handling an overflowed queue           | `event_id`, `logger`, `exception`                         | The queue continues draining; use this signal to alert operators that the drop handler needs attention. |

Future variants will follow the same pattern. Always treat the `payload` keys as an additive contract: code should guard against missing fields to remain compatible with future releases.

---

## Best practices

1. **Keep callbacks fast** – the hook executes on the logging hot path. Expensive work (network calls, long-running computations) should be deferred to another thread or queue.
2. **Handle errors defensively** – the runtime swallows exceptions raised by the hook to protect application logging. Log your own failures or expose metrics so problems do not go unnoticed.
3. **Avoid re-entrancy** – calling into `lib_log_rich` from inside the hook can deadlock. Gather data and hand it off to other systems instead.
4. **Make payloads optional** – iterate with `.get()` or `payload.get("level")` in case future events omit or rename fields.

---

## Example: Prometheus counters

```python
import prometheus_client
from typing import Any

import lib_log_rich as log

EMITTED = prometheus_client.Counter("log_emitted_total", "Synchronous log events", ["level"])
QUEUED = prometheus_client.Counter("log_queued_total", "Queued log events")
RATE_LIMITED = prometheus_client.Counter("log_rate_limited_total", "Rate limited log events")
QUEUE_WORKER_ERRORS = prometheus_client.Counter("log_queue_worker_errors_total", "Queue worker failures")
QUEUE_DROP_ERRORS = prometheus_client.Counter("log_queue_drop_callback_errors_total", "Drop callback failures")


def diagnostic(event: str, payload: dict[str, Any]) -> None:
    if event == "emitted":
        EMITTED.labels(level=payload.get("level", "unknown")).inc()
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
from typing import Any

import lib_log_rich as log

LAST_EMIT = 0.0


def diagnostic(event: str, payload: dict[str, Any]) -> None:
    global LAST_EMIT
    if event in {"queued", "emitted"}:
        LAST_EMIT = time.time()


def is_logging_alive(threshold: float = 30.0) -> bool:
    return time.time() - LAST_EMIT <= threshold

config = log.RuntimeConfig(service="svc", environment="live", diagnostic_hook=diagnostic)
log.init(config)
```

`is_logging_alive()` can feed a liveness probe: if no events move through the system for longer than the threshold, flag the service for investigation.

---

## Interplay with queueing

When `queue_enabled=True`, the hook emits `queued` immediately, but `emitted` occurs inside the background worker. If the worker raises an exception, `queue_worker_error` fires, `QueueAdapter.worker_failed` flips to `True`, and the queue keeps draining. Configure the adapter’s cooldown (default 30 seconds) or call `stop(drain=True)` / `start()` to clear the flag once downstream sinks stabilise. Drop handlers that explode trigger `queue_drop_callback_error`, so you can monitor overflow policies separately from worker health.

---

## Troubleshooting

- **No events arrive** – ensure you actually log something and that your callback signature matches `Callable[[str, Mapping[str, object]], None]` (using `dict[str, Any]` in your code works fine).
- **My hook raised an exception** – the runtime swallows it; log locally or expose metrics so the error is visible.
- **I need more detail** – augment the hook by inspecting `payload` and, if necessary, capture additional context (for example, request IDs) from your own code before logging.

For the complete runtime wiring and port definitions, see [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md).
