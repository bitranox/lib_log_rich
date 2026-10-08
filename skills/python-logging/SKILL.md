---
name: python-logging
description: Use when a Python application or CLI logs through lib_log_rich, or needs structured logging to several sinks at once - Rich console, systemd journald, the Windows Event Log, Graylog/GELF - with context binding, secret scrubbing and ring-buffer dumps; when deploying such an app to a systemd host, a container or a Windows service and setting LOG_* overrides; when stdlib records or the last lines at exit go missing, or a reloaded logging config must be checked without restarting logging.
---

# lib_log_rich

One logging runtime per process fans every event out to the console (Rich), journald, the
Windows Event Log and Graylog, each with its own threshold, after scrubbing secrets and
recording it in a ring buffer you can dump. Configuration is one `RuntimeConfig`; every field
has a `LOG_*` environment override, and **the environment always wins over code**.

Use the Read tool to load the reference file for the area you are working in; this page is the
common path and the traps.

## Install

```bash
uv add lib_log_rich                 # or: uv pip install lib_log_rich
uv add "lib_log_rich[journald]"     # optional systemd-python bindings; journald works without them
uv add "lib_log_rich[eventlog]"     # Windows Event Log: pulls pywin32 on Windows (>= 6.5.0)
```

## The correct shape

```python
import lib_log_rich as log

config = log.RuntimeConfig(service="billing", environment="prod")
log.init(config)  # once per process; a second init() raises RuntimeError
try:
    logger = log.getLogger(__name__)
    with log.bind(job_id="nightly", request_id="r-42"):
        logger.info("charged %s", "acme", extra={"amount": 12})
finally:
    log.shutdown()  # drains the queue; inside an event loop: await log.shutdown_async()
```

`attach_std_logging`, `is_initialised` and `inspect_runtime` live in `lib_log_rich.runtime`, not
on the package root.

## Defaults that bite

| Symptom                                   | Cause and fix                                                                                                                                                                                                                                                     |
|-------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Last lines (or all lines) missing at exit | The queue is on by default and nothing drains it at exit (no atexit hook). Always `shutdown()` in `finally`, or `queue_enabled=False` for short scripts.                                                                                                          |
| A code change to a level has no effect    | A `LOG_*` variable is set; environment beats keyword arguments.                                                                                                                                                                                                   |
| Stdlib `DEBUG` records never arrive       | The stdlib root logger stays at `WARNING`. Call `lib_log_rich.runtime.attach_std_logging()` after `init()`; it lowers the root level to the lowest threshold among enabled sinks. A logger with `propagate=False` needs `attach_std_logging(logger=that_logger)`. |
| `api_key`, `api_token` reach the sinks    | Default scrubbing covers only the field names `password`, `secret`, `token` (exact, case-insensitive). Add `scrub_patterns={"api_key": r".+"}`. Messages are never scrubbed.                                                                                      |
| Forked worker logs nothing                | A forked child inherits a dead queue worker. Use `spawn`, call `init()` in each worker, carry context with `current_runtime().binder.serialize()`.                                                                                                                |
| Same line twice under systemd             | systemd copies the service's stderr into the journal. With journald on, set `LOG_CONSOLE_STREAM=none`, or accept that records at or above `console_level` appear twice (plain and structured).                                                                    |
| Graylog enabled, nothing arrives          | `enable_graylog` without an endpoint builds no sink and raises nothing; an unreachable server drops events. Set `LOG_GRAYLOG_ENDPOINT=host:port`.                                                                                                                 |

Thresholds: `console_level` (default INFO), `backend_level` for journald and the Event Log
(WARNING), `graylog_level` (WARNING). Level names are case-insensitive.

## Deploy

One configuration is safe everywhere: journald is forced off on Windows and the Event Log off
elsewhere. Set per host through the environment, never by editing code:

```ini
# /etc/myapp/logging.env, loaded by EnvironmentFile= in myapp.service
LOG_ENVIRONMENT=prod
LOG_ENABLE_JOURNALD=1
LOG_BACKEND_LEVEL=WARNING
LOG_CONSOLE_STREAM=none
LOG_ENABLE_GRAYLOG=1
LOG_GRAYLOG_ENDPOINT=graylog.example.com:12201
LOG_GRAYLOG_TLS=1
LOG_SCRUB_PATTERNS=api_key=.+,authorization=.+
```

Container: `LOG_CONSOLE_STREAM=stdout`, `LOG_NO_COLOR=1`, journald off, Graylog as the central
sink. Windows service: `LOG_ENABLE_EVENTLOG=1` plus the `eventlog` extra (`pywin32`), and register the event source once
with admin rights (the adapter never does). TLS needs TCP; TLS over UDP is refused (>= 6.4.2).

## Check a config without stopping logging

```python
try:
    candidate = log.RuntimeConfig(**new_settings)  # pydantic ValidationError is a ValueError too
    log.validate_config(candidate)  # lib_log_rich >= 6.4.0: init()'s verdict, no runtime touched
except ValueError as exc:  # "Invalid runtime settings: console_level: Unknown log level: 'loud'"
    logger.error("reload refused: %s", exc)
else:
    log.shutdown()
    log.init(candidate)
    logger = log.getLogger(__name__)  # proxies from before shutdown() keep writing to the old runtime
```

It applies the same `LOG_*` overrides `init()` would, read from the current `os.environ`. There is
no in-place reconfigure: switching means `shutdown()` then `init()`. Do that from the main loop, not
inside the signal handler: set a flag in the handler.

## Reference files

| Topic                                                                                                                                                                                                            | Distilled reference        | Upstream (latest docs)                                                                                                                                                                                                                                                                                                                                   |
|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Configuration - every RuntimeConfig field and LOG_* variable, precedence, env parsing, levels, .env loading, validate_config, scrubbing, payload limits, rate limiting, ring buffer size                         | configuration.md           | [README Runtime Reference](https://github.com/bitranox/lib_log_rich/blob/master/README.md#runtime-reference), [DOTENV.md](https://github.com/bitranox/lib_log_rich/blob/master/DOTENV.md)                                                                                                                                                                |
| Sinks and deployment - console streams, presets, colour, journald, Windows Event Log, Graylog/GELF, platform guards, OpenTelemetry status, systemd unit, container, Windows service, production checklist        | sinks-and-deployment.md    | [INSTALL.md](https://github.com/bitranox/lib_log_rich/blob/master/INSTALL.md), [INSTALL_JOURNAL.md](https://github.com/bitranox/lib_log_rich/blob/master/INSTALL_JOURNAL.md), [CONSOLESTYLES.md](https://github.com/bitranox/lib_log_rich/blob/master/CONSOLESTYLES.md)                                                                                  |
| Runtime and integration - init/shutdown/flush lifecycle, LoggerProxy, bind context in threads and asyncio, stdlib bridge, queue policies, multiprocessing, streaming console adapters, severity metrics, testing | runtime-and-integration.md | [QUEUE.md](https://github.com/bitranox/lib_log_rich/blob/master/QUEUE.md), [SUBPROCESSES.md](https://github.com/bitranox/lib_log_rich/blob/master/SUBPROCESSES.md), [STREAMINGCONSOLE.md](https://github.com/bitranox/lib_log_rich/blob/master/STREAMINGCONSOLE.md), [DIAGNOSTIC.md](https://github.com/bitranox/lib_log_rich/blob/master/DIAGNOSTIC.md) |
| Dumps and CLI - ring buffer, dump() formats, filters, template placeholders, crash-dump recipe, lib_log_rich CLI, logdemo, stress test                                                                           | dumps-and-cli.md           | [LOGDUMP.md](https://github.com/bitranox/lib_log_rich/blob/master/LOGDUMP.md), [CLI.md](https://github.com/bitranox/lib_log_rich/blob/master/CLI.md)                                                                                                                                                                                                     |

For the installed version's own truth, run `lib_log_rich --help` and
`python -c "import lib_log_rich; help(lib_log_rich.RuntimeConfig)"`. Where an upstream page and a
reference file here disagree, run the behaviour: the reference files are written against the code.
