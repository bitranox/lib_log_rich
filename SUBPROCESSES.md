# Logging from Subprocesses

`lib_log_rich` is designed to fan out log events across multiple processes safely. This guide shows the recommended patterns for both `fork`-style workers (default on Linux) and `spawn`-style workers (Windows/macOS).

By default `lib_log_rich.init(RuntimeConfig(...))` stamps each context with the current user name, short hostname, and process id, so every subprocess automatically carries structured identity fields alongside your domain metadata.

## 1. Initialise in the parent before spawning

```python
import lib_log_rich as log

config = log.RuntimeConfig(
    service="video-encoder",
    environment="prod",
    queue_enabled=True,            # ensures adapters run on a single background thread
    queue_maxsize=4096,            # buffer bursts before enforcing the policy
    queue_full_policy="block",   # block producers; use "drop" to shed load
    queue_put_timeout=0.5,         # optional timeout when blocking
    enable_graylog=True,
    graylog_endpoint=("graylog.internal", 12201),
    diagnostic_hook=lambda name, payload: ...
)
log.init(config)
```

`queue_enabled=True` is important whenever multiple processes emit events—adapters such as journald or WinEventLog remain single-threaded, avoiding concurrent writes.

## 2. Prepare context you want every worker to inherit

```python
base_context = {
    "service": "video-encoder",
    "environment": "prod",
    "job_id": "batch-742",
    "request_id": "req-18",
    "user_id": "system",
}
```

If you need to serialise an entire context stack, use `ContextBinder.serialize()` in the parent and `ContextBinder.deserialize()` in the child (see below).

## 3. Worker pattern (default fork on Linux)

When the OS uses `fork`, the child inherits the parent runtime. Simply re-bind the context inside the worker:

```python
import os
from multiprocessing import Process

base_context = {... as above ...}


def worker(ctx: dict[str, str]) -> None:
    import lib_log_rich as log  # import inside the child

    with log.bind(**ctx):
        logger = log.get("worker")
        logger.info("started", extra={"pid": os.getpid()})
        # ... do the work ...
        logger.info("done")


if __name__ == "__main__":
    config = log.RuntimeConfig(...)
    log.init(config)
    processes = [Process(target=worker, args=(base_context,)) for _ in range(4)]
    for proc in processes:
        proc.start()
    for proc in processes:
        proc.join()
    log.shutdown()
```

## 4. Worker pattern (spawn or Windows/macOS)

With `spawn`, each child gets a fresh interpreter and must call `log.init()` on its own:

```python
import os
from multiprocessing import Process, set_start_method

base_context = {... as above ...}


def worker(ctx: dict[str, str]) -> None:
    import lib_log_rich as log

    config = log.RuntimeConfig(
        service=ctx["service"],
        environment=ctx["environment"],
        queue_enabled=True,
    )
    log.init(config)
    try:
        with log.bind(**ctx):
            logger = log.get("worker")
            logger.info("started", extra={"pid": os.getpid()})
            # ...
            logger.info("done")
    finally:
        log.shutdown()


if __name__ == "__main__":
    set_start_method("spawn")  # Windows uses spawn automatically
    processes = [Process(target=worker, args=(base_context,)) for _ in range(4)]
    for proc in processes:
        proc.start()
    for proc in processes:
        proc.join()
```

## 5. Passing entire context stacks

If you need to propagate nested contexts, serialise and restore the stack explicitly:

```python
from lib_log_rich.domain import ContextBinder

# Parent process
binder = ContextBinder()
with binder.bind(service="svc", environment="prod", job_id="job"):
    with binder.bind(request_id="req-1", user_id="alice"):
        payload = binder.serialize()
        # pass "payload" to the child (e.g. via Process args)

# Child process
child_binder = ContextBinder()
child_binder.deserialize(payload)
with child_binder.bind():  # current() already set; bind() without args keeps the top frame
    logger = log.get("worker")
    logger.info("processed", extra={"pid": os.getpid()})
```

## 6. Additional tips

Refer to [QUEUE.md](QUEUE.md) for a full breakdown of queue configuration, diagnostics, and failure modes.

- Always call `log.shutdown()` in every process that called `log.init()` so queues flush and adapters close cleanly.
- Use the `extra={...}` parameter on logger methods to attach per-event metadata (chunk numbers, timer values, etc.). This payload survives scrubbing, lands in the ring buffer, and is forwarded to every adapter.
- Each event automatically records `process_id` and a bounded `process_id_chain`, so dumps and structured sinks expose the parent/child lineage for debugging across forked or spawned workers.
- If you create long-lived worker pools, initialise once per worker and reuse the same `LoggerProxy` from `log.get(...)` for efficiency.
- Tune queue behaviour per workload: increase `queue_maxsize` for bursty producers, flip `queue_full_policy` to "drop" when you prefer to shed log load, set `queue_put_timeout` to bound how long a process blocks, and adjust `queue_stop_timeout` to cap how long shutdown waits for draining (`LOG_QUEUE_STOP_TIMEOUT`).
- The runtime defaults `queue_put_timeout` to 1 second. When the worker stays in a failed state, the adapter automatically switches blocking producers into drop mode and emits a `queue_degraded_drop_mode` diagnostic. Alert on that signal so you can restart the worker, or override `queue_put_timeout`/`LOG_QUEUE_PUT_TIMEOUT` if longer waits are acceptable.
- The queue worker keeps running even if the fan-out callable raises. The failure is logged once, the adapter flips its `worker_failed` flag (auto-clearing after the cooldown, a clean `stop(drain=True)`, or a fresh `start()`), and the diagnostic hook receives a `queue_worker_error` payload so you can surface the crash to metrics or alerting before you restart the worker or process.
- Drop callbacks that explode are also guarded: the runtime logs the exception and emits a `queue_drop_callback_error` diagnostic, making it obvious that the drop handler needs attention while allowing the queue to continue draining.
- When using the drop policy, attach a `diagnostic_hook` that watches for `queue_dropped` events so you can alert or increment a metric when logs are skipped.
- For short-lived subprocesses that emit only a few events, you can disable the queue (`queue_enabled=False`) to keep things simple—just make sure adapters you rely on are safe for concurrent use.

With these patterns, multi-process workloads get structured logging, consistent context propagation, and reliable fan-out across Rich, journald, Windows Event Log, Graylog, and dump exporters.

### Monitoring queue health

```python
import lib_log_rich as log

failures = {"dropped": 0, "worker": 0, "drop_callback": 0}

def diagnostics(name: str, payload: dict[str, object]) -> None:
    if name == "queue_dropped":
        failures["dropped"] += 1
    elif name == "queue_worker_error":
        failures["worker"] += 1
    elif name == "queue_drop_callback_error":
        failures["drop_callback"] += 1

config = log.RuntimeConfig(..., queue_enabled=True, queue_full_policy="drop", diagnostic_hook=diagnostics)
log.init(config)
```

The hook observes both skipped events and background worker faults. Combine these signals with `QueueAdapter.worker_failed` (available when you embed the adapter directly) or process-level health checks to trigger restarts before log fan-out stalls silently.
