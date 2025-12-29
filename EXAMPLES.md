# Usage Playbook

Realistic, end-to-end samples that show *why* each feature exists.
All examples target Python ≥ 3.10. Replace `python` with `python3` where needed.

> TL;DR: `python -m scripts` is the command centre. Everything else hangs off the
> public API in `lib_log_rich` (for application code) or the CLI entry
> `lib_log_rich` (for operators).

---

## 1. Kick the tyres (CLI + automation)

```bash
# Show available sub-commands (rich-coloured help)
python -m scripts --help

# Run the full lint/type/test pipeline with coverage & Codecov upload
python -m scripts test --coverage on

# Preview the Ops CLI without writing any code
lib_log_rich --help
lib_log_rich info
```

**Why:** the repo ships with first-class automation. Once you trust
`python -m scripts`, the Makefile is little more than syntactic sugar.

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

config = log.RuntimeConfig(service="checkout", environment="prod", queue_enabled=False)
log.init(config)

with log.bind(job_id="order-42", request_id="req-9001"):
    logger = log.getLogger("checkout.http")
    logger.info("accepted", extra={"order_total": 199.99, "currency": "USD"})
    logger.warning("charge pending", extra={"provider": "stripe", "attempts": 1})

print("JSON dump: 
", log.dump(dump_format="json"))
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

Running `LOG_SERVICE=real python demo.py` still wins — real environment
variables override `.env`. To let `.env` win, add
`log_config.enable_dotenv(dotenv_override=True)`.

**Why:** keep local defaults in the repo while respecting the documented
precedence chain (CLI flag → real `os.environ` → `.env` → defaults).

---

## 5. Multi-backend runtime (Rich console + Graylog + journald)

```python
from pathlib import Path
from typing import Any

import lib_log_rich as log
import lib_log_rich.config as log_config

log_config.enable_dotenv(search_from=Path(__file__).parent)


def diagnostic(event: str, payload: dict[str, Any]) -> None:
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

log.dump(dump_format="text", path=Path("./logs/sync.log"), color=False)
log.shutdown()
```

**Why:** shows how to run multiple adapters at once, wire a diagnostic hook, and
persist dumps without colour for ingestion into other systems.

---

## 6. Queue backpressure and drop diagnostics

```python
import time
import lib_log_rich as log

config = log.RuntimeConfig(
    service="worker",
    environment="prod",
    queue_enabled=True,
    queue_maxsize=1,
    queue_full_policy="drop",
    diagnostic_hook=lambda event, payload: print("diagnostic", event, payload),
)
log.init(config)

with log.bind(job_id="demo", request_id="queue"):
    logger = log.getLogger("worker")
    for n in range(5):
        logger.info("message", extra={"n": n})
        time.sleep(0.05)

log.shutdown()
```

**Why:** exercises the drop path. The diagnostic hook emits `queue_dropped`
entries which you can surface in metrics.

---

## 7. Rich CLI showcase

```bash
# Opt-in .env for defaults, then preview the coloured demo events
lib_log_rich --use-dotenv logdemo --dump-format text

# Route the same preview through Graylog
lib_log_rich --use-dotenv logdemo --enable-graylog --graylog-endpoint graylog.internal:12201

# Drive the CLI via the helper (respects --use-dotenv and --no-use-dotenv)
python -m scripts run --use-dotenv logdemo --dump-path ./logs --dump-format json
```

**Why:** CLI behaviour mirrors the API: `.env` is opt-in, real environment
variables still win, and any backend can be toggled at the edge.

---

## 8. Automation cheatsheet (`python -m scripts`)

```bash
# Tests/lint/type (with all defaults)
python -m scripts test

# Enforce --check formatting
python -m scripts test --strict-format

# Fresh editable installs
python -m scripts install
python -m scripts dev --dry-run

# Version choreography
python -m scripts bump --part minor
python -m scripts bump --version 1.2.3
python -m scripts version-current

# Build artefacts
python -m scripts build

# Release workflow (uses git + gh CLI when available)
python -m scripts release --remote origin

# Push helper (runs tests first, commits with provided message)
python -m scripts push --remote origin --message "chore: sync"
```

**Why:** the CLI consolidates every automation entry point; Makefile targets
serve as thin pass-throughs for folks who prefer `make` muscle memory.

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
