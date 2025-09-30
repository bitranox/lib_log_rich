# Usage Examples

This document collects copy-pasteable snippets that grow in complexity. Each example explains *why* you might use it, not just *how*.

> All samples target Python ≥ 3.10 and assume `pip install lib_log_rich` (or equivalent) has already run.

---

## 1. Hello World

The smallest possible integration: initialise the runtime, emit a message, shut down.

```python
import lib_log_rich as log

log.init(service="demo", environment="dev", queue_enabled=False)
log.get("demo").info("Hello from lib_log_rich!")
log.shutdown()
```

### Why
- Good sanity check that the package is installed and terminal colours render correctly.
- `queue_enabled=False` keeps the example synchronous, so it works inside REPLs and notebooks.

---

## 2. Context binding and structured payloads

Bind request/job metadata once and include additional `extra` payloads per log entry.

```python
import lib_log_rich as log

log.init(service="checkout", environment="prod", queue_enabled=False)

with log.bind(job_id="order-42", request_id="req-9001"):
    logger = log.get("checkout.http")
    logger.info("accepted", extra={"order_total": 199.99, "currency": "USD"})
    logger.warning("charge pending", extra={"provider": "stripe", "attempts": 1})

print("Recent events as JSON:\n", log.dump(dump_format="json"))
log.shutdown()
```

### Why
- Demonstrates how the context stack (`bind`) keeps job metadata attached to every event without duplicating arguments.
- Shows that `dump()` is available even when the queue is disabled.

---

## 3. Opt-in `.env` configuration and precedence

Load settings from a nearby `.env` file while preserving environment variable precedence.

```
# .env
LOG_SERVICE=dotenv-demo
LOG_ENVIRONMENT=ci
LOG_QUEUE_ENABLED=0
```

```python
import lib_log_rich as log
import lib_log_rich.config as log_config

# Walk upwards from the current working directory until `.env` is found
log_config.enable_dotenv()

# LOG_SERVICE/LOG_ENVIRONMENT from `.env` now populate os.environ
log.init(service="will-be-overridden", environment="ignored")
log.get("demo").info("service and environment come from .env")
log.shutdown()
```

Explicit environment variables still win. For example:

```bash
LOG_SERVICE=real LOG_ENVIRONMENT=prod python demo.py
```

will set the service to `real`, even if `.env` provided another value. To let `.env` override real environment variables, call `log_config.enable_dotenv(dotenv_override=True)`.

### Why
- Keeps configuration with the project while following the documented precedence chain.
- Makes it trivial to provide defaults for local development without affecting production.

---

## 4. CLI with `.env` toggle and Graylog

Run the CLI using `.env` for defaults, then override selectively with flags.

```
# .env
LOG_SERVICE=logdemo
LOG_ENVIRONMENT=staging
LOG_GRAYLOG_ENDPOINT=graylog.internal:12201
LOG_ENABLE_GRAYLOG=1
```

```bash
# Print the banner with .env-derived defaults
lib_log_rich --use-dotenv info

# Preview demo events and upload them to Graylog
lib_log_rich --use-dotenv logdemo --dump-format json

# Temporarily disable .env even if LOG_USE_DOTENV=1
lib_log_rich --no-use-dotenv logdemo

# Drive the CLI through the helper script (also supports --use-dotenv)
python scripts/run_cli.py --use-dotenv logdemo --dump-path ./logs --dump-format text
```

### Why
- Makes CLI behaviour match library behaviour: opt-in load, environment overrides remain authoritative.
- Graylog flags show how transport defaults from `.env` can be combined with per-run parameters (e.g., `--dump-format`).

---

## 5. Multi-backend configuration (console + Graylog + journald)

A more complete application wiring multiple adapters and using a diagnostic hook.

```python
from pathlib import Path
from typing import Any

import lib_log_rich as log
import lib_log_rich.config as log_config

LOG_PATH = Path("./logs")
LOG_PATH.mkdir(exist_ok=True)

log_config.enable_dotenv(search_from=Path(__file__).parent)

def diagnostic(event: str, payload: dict[str, Any]) -> None:
    print(f"diagnostic: {event} -> {payload.get('event_id')}")

log.init(
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

with log.bind(job_id="sync", request_id="batch-7"):
    app_logger = log.get("orch.worker")
    app_logger.info("starting sync", extra={"count": 3})
    try:
        raise RuntimeError("simulated failure")
    except RuntimeError as exc:
        app_logger.error("sync failed", extra={"error": str(exc), "retry": True})

log.dump(dump_format="text", path=LOG_PATH / "sync.log", color=False)
log.shutdown()
```

### Why
- Shows how `.env` can provide defaults while the code still commits to programmatic values (Graylog endpoint, journald toggle).
- Demonstrates `diagnostic_hook` reacting to internal events (queue state, rate limiting, etc.).
- Writes a dump to disk without colour so it’s suitable for log shipping.

---

## Next steps

- Review [SUBPROCESSES.md](SUBPROCESSES.md) for end-to-end multiprocessing patterns that rely on the queue adapter.
- Read [DOTENV.md](DOTENV.md) for a detailed walkthrough of `.env` loading semantics.
- Explore [CONSOLESTYLES.md](CONSOLESTYLES.md) to customise Rich themes.
- Check [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md) for architectural context, ports, and adapters.
