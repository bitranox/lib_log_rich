# Usage Playbook

Realistic, end-to-end samples that show *why* each feature exists.
All examples target Python 3.10 or newer. Replace `python` with `python3` where needed.

> TL;DR: the bmk-generated `Makefile` is the command centre (`make help` lists
> its targets). Everything else hangs off the public API in `lib_log_rich` (for
> application code) or the CLI entry `lib_log_rich` (for operators).

---

## 1. Kick the tyres (CLI + automation)

```bash
# Show the automation targets
make help

# Run the full lint/type/test pipeline
make test

# Preview the Ops CLI without writing any code
lib_log_rich --help
lib_log_rich info
```

**Why:** the repo ships with first-class automation through bmk; `make help`
is the authoritative list of what it can run.

---

## 2. Minimal application wiring

```python
import lib_log_rich as log

config = log.RuntimeConfig(service="demo", environment="dev", queue_enabled=False)
log.init(config)
log.getLogger("demo").info("Hello from lib_log_rich!")
log.shutdown()
```

**Why:** a three-liner to prove the install works and colours render.
`queue_enabled=False` keeps the call path synchronous so it works inside REPLs
and notebooks.

---

## 3. Context propagation, structured data, and dumps

```python
import lib_log_rich as log
from lib_log_rich import DumpFormat  # lib_log_rich.domain exports it too

config = log.RuntimeConfig(service="checkout", environment="prod", queue_enabled=False)
log.init(config)

with log.bind(job_id="order-42", request_id="req-9001"):
    logger = log.getLogger("checkout.http")
    logger.info("accepted", extra={"order_total": 199.99, "currency": "USD"})
    logger.warning("charge pending", extra={"provider": "stripe", "attempts": 1})

print("JSON dump:\n", log.dump(dump_format=DumpFormat.JSON))
log.shutdown()
```

**Why:** demonstrates the context stack and shows that dumps work even when the
queue is disabled.

---

## 4. Opt-in `.env` configuration (with precedence)

```
# .env
LOG_SERVICE=dotenv-demo
LOG_ENVIRONMENT=ci
LOG_QUEUE_ENABLED=0
```

```python
import lib_log_rich as log
import lib_log_rich.config as log_config

# Walk upwards from CWD until `.env` is found
log_config.enable_dotenv()

config = log.RuntimeConfig(service="will-be-overridden", environment="ignored")
log.init(config)
log.getLogger("demo").info("service and environment came from .env")
log.shutdown()
```

Running `LOG_SERVICE=real python demo.py` still wins: real environment
variables override `.env`. To let `.env` win, add
`log_config.enable_dotenv(dotenv_override=True)`.

**Why:** keep local defaults in the repo while respecting the documented
precedence chain (CLI flag, then real `os.environ`, then `.env`, then defaults).

---

## 5. Multi-backend runtime (Rich console + Graylog + journald)

```python
from collections.abc import Mapping
from pathlib import Path

import lib_log_rich as log
import lib_log_rich.config as log_config
from lib_log_rich import DumpFormat  # lib_log_rich.domain exports it too

log_config.enable_dotenv(search_from=Path(__file__).parent)


def diagnostic(event: str, payload: Mapping[str, object]) -> None:
    print(f"diagnostic: {event} -> {payload.get('event_id')}")


config = log.RuntimeConfig(
    service="orchestrator",
    environment="prod",
    console_level="info",
    backend_level="warning",
    enable_graylog=True,
    graylog_endpoint=("graylog.internal", 12201),
    enable_journald=True,
    queue_enabled=True,
    diagnostic_hook=diagnostic,
)
log.init(config)

with log.bind(job_id="sync", request_id="batch-7"):
    app_logger = log.getLogger("orch.worker")
    app_logger.info("starting sync", extra={"count": 3})
    try:
        raise RuntimeError("simulated failure")
    except RuntimeError as exc:
        app_logger.error("sync failed", extra={"error": str(exc), "retry": True})

log.dump(dump_format=DumpFormat.TEXT, path=Path("./logs/sync.log"), color=False)
log.shutdown()
```

**Why:** shows how to run multiple adapters at once, wire a diagnostic hook, and
persist dumps without colour for ingestion into other systems.

---

## 6. Queue backpressure and drop diagnostics

```python
import lib_log_rich as log
from lib_log_rich.domain import QueuePolicy

config = log.RuntimeConfig(
    service="worker",
    environment="prod",
    queue_enabled=True,
    queue_maxsize=1,
    queue_full_policy=QueuePolicy.DROP,
    diagnostic_hook=lambda event, payload: print("diagnostic", event, payload),
)
log.init(config)

with log.bind(job_id="demo", request_id="queue"):
    logger = log.getLogger("worker")
    for n in range(50):  # a burst faster than the worker drains a one-slot queue
        logger.info("message", extra={"n": n})

log.shutdown()
```

**Why:** exercises the drop path. The diagnostic hook receives `queue_full` and
`queue_dropped` entries for the events the full queue refused (alongside a
`queued` entry for each accepted one), which you can surface in metrics.

---

## 7. Rich CLI showcase

```bash
# Opt-in .env for defaults, then preview the coloured demo events
lib_log_rich --use-dotenv logdemo --dump-format text

# Route the same preview through Graylog
lib_log_rich --use-dotenv logdemo --enable-graylog --graylog-endpoint graylog.internal:12201

# Drive the CLI through the module entry (respects --use-dotenv and --no-use-dotenv)
python -m lib_log_rich --use-dotenv logdemo --dump-path ./logs --dump-format json
```

**Why:** CLI behaviour mirrors the API: `.env` is opt-in, real environment
variables still win, and any backend can be toggled at the edge.

---

## 8. Automation cheatsheet (`make`)

```bash
# Tests/lint/type (the pre-push gate)
make test

# Every target, with a one-line description each
make help
```

**Why:** the `Makefile` is generated by bmk and rewritten by its targets, so
`make help` (which reads the Makefile itself) is the current list of build,
bump, push and release targets; `bmk --help` documents the tool behind them.

---

## 9. Next steps

- Review [SUBPROCESSES.md](SUBPROCESSES.md) for queue-heavy multiprocessing patterns.
- Explore [CONSOLESTYLES.md](CONSOLESTYLES.md) to customise Rich palettes.
- Read [DOTENV.md](DOTENV.md) for deeper `.env` precedence rules.
- Consult [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md)
  for architectural boundaries.

---

## 10. Streaming console output to Flask

```python
# examples/flask_console_stream.py
# Run `pip install flask` and start the app with `python examples/flask_console_stream.py`.
# Visit http://localhost:5001/logs for an EventSource stream; trigger logs via
# http://localhost:5001/emit/hello.
```

**Why:** demonstrates the queue-backed console adapter and `console_adapter_factory`. The runtime pushes
ANSI/HTML-rendered lines into a queue so web frameworks (Flask, FastAPI, SSE, WebSockets) can broadcast console output without
monkey-patching internals.
