# lib_log_rich

<!-- Badges -->
[![CI](https://github.com/bitranox/lib_log_rich/actions/workflows/ci.yml/badge.svg)](https://github.com/bitranox/lib_log_rich/actions/workflows/ci.yml)
[![CodeQL](https://github.com/bitranox/lib_log_rich/actions/workflows/codeql.yml/badge.svg)](https://github.com/bitranox/lib_log_rich/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Jupyter](https://img.shields.io/badge/Jupyter-Launch-orange?logo=jupyter)](https://mybinder.org/v2/gh/bitranox/lib_log_rich/HEAD?labpath=notebooks%2FQuickstart.ipynb)
[![PyPI](https://img.shields.io/pypi/v/lib_log_rich.svg)](https://pypi.org/project/lib_log_rich/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/lib_log_rich.svg)](https://pypi.org/project/lib_log_rich/)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-46A3FF?logo=ruff&labelColor=000)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/bitranox/lib_log_rich/graph/badge.svg?token=UFBaUDIgRk)](https://codecov.io/gh/bitranox/lib_log_rich)
[![Maintainability](https://qlty.sh/badges/041ba2c1-37d6-40bb-85a0-ec5a8a0aca0c/maintainability.svg)](https://qlty.sh/gh/bitranox/projects/lib_log_rich)
[![Known Vulnerabilities](https://snyk.io/test/github/bitranox/lib_log_rich/badge.svg)](https://snyk.io/test/github/bitranox/lib_log_rich)

Rich-powered logging backbone with contextual metadata, multi-target fan-out (console, journald, Windows Event Log, Graylog), ring-buffer dumps, and queue-based decoupling for multi-process workloads. Rich renders multi-colour output tuned to each terminal, while adapters and dump exporters support configurable formats and templates. Each runtime captures the active user, short hostname, process id, and PID chain automatically, so every sink receives consistent system identity fields. The public API stays intentionally small: initialise once, bind context, emit logs (with per-event `extra` payloads), dump history in text/JSON/HTML, and shut down cleanly.

## Installation

For a quick start from PyPI:

```bash
pip install lib_log_rich
```

Detailed installation options (venv, pipx, uv, Poetry/PDM, Conda/mamba, Git installs, and packaging notes) live in [INSTALL.md](INSTALL.md).
## Usage

```python
import lib_log_rich as log

log.init(
    service="my-service",
    environment="dev",
    queue_enabled=False,
    enable_graylog=False,
)

with log.bind(job_id="startup", request_id="req-001"):
    logger = log.get("app.http")
    logger.info("ready", extra={"port": 8080})

# Inspect the recent history (text/json/html)
print(log.dump(dump_format="json"))

log.shutdown()
```

### Contextual metadata (`extra=`)

The optional `extra` mapping travels alongside each event. The runtime copies it into the structured payload, scrubs matching keys, retains it in the ring buffer, and forwards it to every adapter (console, Graylog, journald, Windows Event Log, dumps). Use `extra` for request-specific fields such as ports, tenant IDs, or feature flags—anything that helps downstream tooling interpret the log entry.

Legacy helpers remain available for smoke tests:

```python
log.hello_world()
try:
    log.i_should_fail()
except RuntimeError as exc:
    print(exc)
```

### CLI entry point

```
# Print the metadata banner
python -m lib_log_rich
# Or use the rich-click adapter directly
lib_log_rich info

# Trigger the smoke helpers
lib_log_rich hello
lib_log_rich --traceback fail

# Preview console colour themes (optionally render dumps)
lib_log_rich logdemo
lib_log_rich logdemo --theme classic --dump-format json --service my-service --environment prod
lib_log_rich logdemo --dump-format html --dump-path ./logs
lib_log_rich logdemo --enable-graylog --graylog-endpoint 127.0.0.1:12201
lib_log_rich logdemo --enable-journald --enable-eventlog
```

Use `--enable-graylog` to send the sample events to a running Graylog instance; combine it with `--graylog-endpoint` (defaults to `127.0.0.1:12201`), `--graylog-protocol`, and `--graylog-tls` when you need alternative transports. Platform-specific sinks are equally easy to exercise: `--enable-journald` uses `systemd.journal.send` on Linux hosts, while `--enable-eventlog` binds the Windows Event Log adapter (both flags are safely ignored when the host does not support the backend).

For TCP targets the Graylog adapter keeps the socket open between events and transparently reconnects after network failures, so iterative demos behave like long-lived services.

When `--dump-format` is provided, the command prints the rendered dump to stdout by default. Supplying `--dump-path` writes one file per theme using the pattern `logdemo-<theme>.<ext>` (the directory is created on demand).

## Public API

| Symbol          | Signature (abridged)                                                                                                                                                                                                                                                                                                                                                       | Description                                                                                                                                                                                                                                                 |
|-----------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `init`          | `init(*, service: str, environment: str, console_level="info", backend_level="warning", graylog_endpoint=None, enable_ring_buffer=True, enable_journald=False, enable_eventlog=False, enable_graylog=False, graylog_protocol="tcp", graylog_tls=False, queue_enabled=True, force_color=False, no_color=False, scrub_patterns=None, rate_limit=None, diagnostic_hook=None)` | Composition root. Wires adapters, queue, scrubber, and rate limiter. Must run before calling `bind`, `get`, or `dump`. Environment variables listed below override matching arguments.                                                                      |
| `bind`          | `bind(**fields)` (context manager)                                                                                                                                                                                                                                                                                                                                         | Binds contextual metadata. Requires `service`, `environment`, and `job_id` when no parent context exists; nested scopes merge overrides. Yields the active `LogContext`.                                                                                    |
| `get`           | `get(name: str) -> LoggerProxy`                                                                                                                                                                                                                                                                                                                                            | Returns a `LoggerProxy` exposing `.debug/.info/.warning/.error/.critical`. Each call returns a dict (e.g. `{"ok": True, "event_id": "..."}` or `{ "ok": False, "reason": "rate_limited" }`).                                                                |
| `LoggerProxy`   | created via `get(name)`                                                                                                                                                                                                                                                                                                                                                    | Lightweight facade around the process use case. Methods: `.debug(message, extra=None)`, `.info(...)`, `.warning(...)`, `.error(...)`, `.critical(...)`. All accept a string message plus optional mutable mapping for `extra`.                              |
| `dump`          | `dump(*, dump_format="text", path=None, level=None, text_format=None, color=False) -> str`                                                                                                                                                                                                                                                                                      | Serialises the ring buffer (text/json/html). `level` filters events below a threshold, `text_format` customises text rendering, and `color` toggles ANSI colouring for text dumps. The rendered output is always returned and optionally written to `path`. |
| `shutdown`      | `shutdown() -> None`                                                                                                                                                                                                                                                                                                                                                       | Flushes adapters, drains/stops the queue, and clears global state. Safe to call repeatedly after initialisation.                                                                                                                                            |
| `hello_world`   | `hello_world() -> None`                                                                                                                                                                                                                                                                                                                                                    | Prints the canonical “Hello World” message for smoke tests.                                                                                                                                                                                                 |
| `i_should_fail` | `i_should_fail() -> None`                                                                                                                                                                                                                                                                                                                                                  | Raises `RuntimeError("I should fail")` to exercise failure handling paths.                                                                                                                                                                                  |
| `summary_info`  | `summary_info() -> str`                                                                                                                                                                                                                                                                                                                                                    | Returns the CLI metadata banner as a string without printing it.                                                                                                                                                                                            |
| `logdemo`       | `logdemo(*, theme="classic", service=None, environment=None, dump_format=None, dump_path=None, color=None, enable_graylog=False, graylog_endpoint=None, graylog_protocol="tcp", graylog_tls=False, enable_journald=False, enable_eventlog=False) -> dict[str, Any]` | Spins up a temporary runtime, emits one sample event per level, optionally renders a dump, and records which backends were requested via the `backends` mapping. Use the boolean flags to exercise Graylog, journald, or Windows Event Log sinks from the CLI or API. |

`LoggerProxy` instances returned by `get()` support the standard logging-level methods:

```python
logger = log.get("app.component")
logger.info("payload", extra={"user": "alice"})
logger.error("boom", extra={"secret": "***"})
```

Each call returns a dictionary describing the outcome (success + event id, `{ "queued": True }`, or `{ "reason": "rate_limited" }`).

The optional `extra` mapping is copied into the structured event and travels end-to-end: it is scrubbed, persisted in the ring buffer, and forwarded to every adapter (Rich console, journald, Windows Event Log, Graylog, dump exporters). Use it to attach contextual fields such as port numbers, tenant IDs, or feature flags.

Need a quick preview of console colours? Call:

```python
import lib_log_rich as log

result = log.logdemo(theme="neon", dump_format="json")
print(result["events"])   # list of per-level emission results
print(result["dump"])     # rendered dump string (or None when not requested)
print(result["backends"]) # {'graylog': False, 'journald': False, 'eventlog': False}
```

The helper initialises a throwaway runtime, emits one message per level using the selected theme, optionally renders a text/JSON/HTML dump via the `dump_format` argument, and then shuts itself down. Themes are defined in [CONSOLESTYLES.md](CONSOLESTYLES.md) and include `classic`, `dark`, `neon`, and `pastel` (you can add more via `console_styles`).

The optional backend flags (`enable_graylog`, `enable_journald`, `enable_eventlog`) let you route the demo events to real adapters during manual testing—the return payload exposes the chosen targets via `result["backends"]`.

## log dump

`log.dump(...)` bridges the in-memory ring buffer to structured exports. Use it for on-demand snapshots (human-readable text, machine-friendly JSON, or HTML tables) without stopping the runtime. You can filter, colourise text output, and write the dump to disk or keep it in memory for quick inspection. Each event now carries `user_name`, `hostname`, and `process_id` in the context so templates (e.g. `{user_name}`, `{hostname}`, `{process_id}`) and structured outputs expose system identity automatically.

`log.dump(...)` parameters in detail:

- `dump_format`: one of `"text"`, `"json"`, or `"html"` (or the `DumpFormat` enum). Determines the renderer used by the dump adapter.
- `path`: optional `Path` or string. When provided the rendered output is written to disk; the function still returns the same string.
- `level`: optional minimum severity (accepts a `LogLevel` or case-insensitive level name such as `"warning"`). Events below this threshold are filtered out before rendering.
- `text_format`: optional custom template for text dumps. The default is `"{timestamp} {level:<8} {logger_name} {event_id} {message}"`. Available placeholders: `timestamp`, `level`, `logger_name`, `event_id`, `message`, `context`, `extra`.
- `color`: `False` by default. When `True`, text dumps use ANSI colour codes mapped to log levels (no effect for JSON/HTML).

### Text format placeholders

Text dumps use Python's `str.format` syntax. Available keys perform string substitution over pre-rendered values:

- `timestamp` – ISO8601 UTC string (e.g. `2025-09-24T10:15:24.123456+00:00`). Use slicing or `datetime` parsing downstream if you need custom formatting.
- `level` – upper-case level name (`DEBUG`, `INFO`, ...).
- `logger_name` – the logical logger identifier passed to `get(...)`.
- `event_id` – unique identifier generated per event.
- `message` – raw log message string.
- `user_name`, `hostname`, `process_id` – system identity fields captured automatically during `init()`.
- `context` – full context dictionary (service, environment, job, request id, etc.).
- `extra` – shallow copy of the extra mapping provided to the logger call.

Standard `str.format` features apply (`{level:<8}`, `{message!r}`, etc.), and undefined keys raise a `ValueError`.

HTML dumps render a simple table structure intended for reports or quick sharing; colours are intentionally omitted so the output remains readable in any viewer.

For multi-process logging patterns (fork/spawn), follow the recipes in [SUBPROCESSES.md](SUBPROCESSES.md).

## Runtime configuration

`lib_log_rich.init` wires the entire runtime. All parameters are keyword-only and may be overridden by environment variables shown in the last column.

| Parameter            | Type                        | Default                                             | Purpose                                                                                                    | Environment variable                                 |
|----------------------|-----------------------------|-----------------------------------------------------|------------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| `service`            | `str`                       | *(required)*                                        | Logical service name recorded in each event and used by adapters.                                          | `LOG_SERVICE`                                        |
| `environment`        | `str`                       | *(required)*                                        | Deployment environment (e.g., `dev`, `prod`).                                                              | `LOG_ENVIRONMENT`                                    |
| `console_level`      | `str \| LogLevel`           | `LogLevel.INFO`                                     | Lowest level emitted to the Rich console adapter. Accepts names (`"warning"`) or `LogLevel` instances.     | `LOG_CONSOLE_LEVEL`                                  |
| `backend_level`      | `str \| LogLevel`           | `LogLevel.WARNING`                                  | Threshold shared by structured backends (journald, Windows Event Log).                                     | `LOG_BACKEND_LEVEL`                                  |
| `graylog_endpoint`   | `tuple[str, int] \| None`   | `None`                                              | Host/port for GELF over TCP. When set, combine with `enable_graylog=True`.                                 | `LOG_GRAYLOG_ENDPOINT` (`host:port` form)            |
| `graylog_protocol`   | `str`                       | `"tcp"`                                             | Transport to reach Graylog (`"tcp"` or `"udp"`).                                                           | `LOG_GRAYLOG_PROTOCOL`                               |
| `graylog_tls`        | `bool`                      | `False`                                             | Enables TLS when using TCP transport.                                                                      | `LOG_GRAYLOG_TLS`                                    |
| `enable_ring_buffer` | `bool`                      | `True`                                              | Toggles the in-memory ring buffer. When disabled the system retains a small fallback buffer (1024 events). | `LOG_RING_BUFFER_ENABLED`                            |
| `ring_buffer_size`   | `int`                       | `25_000`                                            | Max events retained in the ring buffer when enabled.                                                       | `LOG_RING_BUFFER_SIZE`                               |
| `enable_journald`    | `bool`                      | `False`                                             | Adds the journald adapter (Linux/systemd). Ignored on Windows hosts.                                       | `LOG_ENABLE_JOURNALD`                                |
| `enable_eventlog`    | `bool`                      | `False`                                             | Adds the Windows Event Log adapter. Ignored on non-Windows platforms.                                      | `LOG_ENABLE_EVENTLOG`                                |
| `enable_graylog`     | `bool`                      | `False`                                             | Enables the Graylog adapter (requires `graylog_endpoint`).                                                 | `LOG_ENABLE_GRAYLOG`                                 |
| `queue_enabled`      | `bool`                      | `True`                                              | Routes events through a background queue for multi-process safety. Disable for simple scripts/tests.       | `LOG_QUEUE_ENABLED`                                  |
| `force_color`        | `bool`                      | `False`                                             | Forces Rich console colour output even when `stderr` isn’t a TTY.                                          | `LOG_FORCE_COLOR`                                    |
| `no_color`           | `bool`                      | `False`                                             | Disables colour output regardless of terminal support.                                                     | `LOG_NO_COLOR`                                       |
| `console_styles`     | `mapping[str, str] \| None` | `None`                                              | Optional Rich style overrides per level (e.g. `{ "INFO": "bright_green" }`).                               | `LOG_CONSOLE_STYLES` (comma-separated `LEVEL=style`) |
| `text_format`        | `str \| None`            | `{timestamp} {level:<8} {logger_name} {event_id} {message}` | Default template applied to text dumps when none is supplied at call time. | `LOG_DUMP_TEXT_FORMAT`                               |
| `scrub_patterns`     | `dict[str, str] \| None`    | `{"password": ".+", "secret": ".+", "token": ".+"}` | Regex patterns scrubbed from payloads before fan-out.                                                      | `LOG_SCRUB_PATTERNS` (comma-separated `field=regex`) |
| `rate_limit`         | `tuple[int, float] \| None` | `None`                                              | `(max_events, window_seconds)` throttling applied before fan-out.                                          | `LOG_RATE_LIMIT` (`"100/60"` format)                 |
| `diagnostic_hook`    | `Callable`                  | `None`                                              | Optional callback the runtime invokes for internal telemetry (`queued`, `emitted`, `rate_limited`).        | *(code-only)*                                        |

The initializer also honours `LOG_BACKEND_LEVEL`, `LOG_FORCE_COLOR`, and `LOG_NO_COLOR` simultaneously—environment variables always win over supplied keyword arguments. When `enable_journald` is requested on Windows hosts or `enable_eventlog` on non-Windows hosts the runtime silently disables those adapters so cross-platform deployments never fail during initialisation.

> **Note:** TLS is only supported with the TCP transport. Combining `graylog_protocol="udp"` with TLS (or setting `LOG_GRAYLOG_PROTOCOL=udp` alongside `LOG_GRAYLOG_TLS=1`) raises a `ValueError` during initialisation.

### Environment-only overrides

Set these without touching application code:

```
export LOG_SERVICE="billing"
export LOG_ENVIRONMENT="prod"
export LOG_QUEUE_ENABLED=0
export LOG_GRAYLOG_ENDPOINT="graylog.example.internal:12201"
export LOG_ENABLE_GRAYLOG=1
export LOG_GRAYLOG_PROTOCOL="tcp"
export LOG_GRAYLOG_TLS=1
export LOG_CONSOLE_STYLES="INFO=bright_green,ERROR=bold white on red"
export LOG_RATE_LIMIT="500/60"
```

Boolean variables treat `1`, `true`, `yes`, or `on` (case-insensitive) as truthy; anything else falls back to the default/parameter value.

### Terminal compatibility

Rich automatically detects whether the target is 16-colour, 256-colour, or truecolor, and adjusts the style to the nearest supported palette. For truly minimal environments (plain logs, CI artefacts), set `no_color=True` (or `LOG_NO_COLOR=1`) and Rich suppresses ANSI escapes entirely. Conversely, `force_color=True` (or `LOG_FORCE_COLOR=1`) forces colouring even if `stderr` isn’t a tty (useful in some container setups).

#### Customising per-level colours

Override the default Rich styles by passing a dictionary to `init(console_styles=...)` or by exporting `LOG_CONSOLE_STYLES` as a comma-separated list, for example:

```
export LOG_CONSOLE_STYLES="DEBUG=dim,INFO=bright_green,WARNING=bold yellow,ERROR=bold white on red,CRITICAL=bold magenta"
```

Values use Rich’s style grammar (named colours, modifiers like `bold`/`dim`, or hex RGB). Omitted keys fall back to the built-in theme. `logdemo` cycles through the built-in palettes (`classic`, `dark`, `neon`, `pastel`) so you can preview styles before committing to overrides.

## Further documentation

- [README.md](README.md) — quick overview and parameters.
- [INSTALL.md](INSTALL.md) — detailed installation paths.
- [DEVELOPMENT.md](DEVELOPMENT.md) — contributor workflow.
- [SUBPROCESSES.md](SUBPROCESSES.md) — multi-process logging guidance.
- [CONSOLESTYLES.md](CONSOLESTYLES.md) — palette syntax, themes, and overrides.
- [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md) — authoritative design reference.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution expectations, coding standards, and review process.
- [CHANGELOG.md](CHANGELOG.md) — release history and noteworthy changes.

## Development

Contributor workflows, make targets, CI automation, packaging sync, and release guidance are documented in [DEVELOPMENT.md](DEVELOPMENT.md).

## License

[MIT](LICENSE)
