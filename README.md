# lib_log_rich

<!-- Badges -->
[![CI](https://github.com/bitranox/lib_log_rich/actions/workflows/ci.yml/badge.svg)](https://github.com/bitranox/lib_log_rich/actions/workflows/ci.yml)
[![CodeQL](https://github.com/bitranox/lib_log_rich/actions/workflows/codeql.yml/badge.svg)](https://github.com/bitranox/lib_log_rich/actions/workflows/codeql.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Open in Codespaces](https://img.shields.io/badge/Codespaces-Open-blue?logo=github&logoColor=white&style=flat-square)](https://codespaces.new/bitranox/lib_log_rich?quickstart=1)
[![PyPI](https://img.shields.io/pypi/v/lib_log_rich.svg)](https://pypi.org/project/lib_log_rich/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/lib_log_rich.svg)](https://pypi.org/project/lib_log_rich/)
[![Code Style: Ruff](https://img.shields.io/badge/Code%20Style-Ruff-46A3FF?logo=ruff&labelColor=000)](https://docs.astral.sh/ruff/)
[![codecov](https://codecov.io/gh/bitranox/lib_log_rich/graph/badge.svg?token=UFBaUDIgRk)](https://codecov.io/gh/bitranox/lib_log_rich)
[![Maintainability](https://qlty.sh/badges/041ba2c1-37d6-40bb-85a0-ec5a8a0aca0c/maintainability.svg)](https://qlty.sh/gh/bitranox/projects/lib_log_rich)
[![Known Vulnerabilities](https://snyk.io/test/github/bitranox/lib_log_rich/badge.svg)](https://snyk.io/test/github/bitranox/lib_log_rich)

Rich-powered logging backbone with contextual metadata, multi-target fan-out (console, journald, Windows Event Log, Graylog), ring-buffer dumps, and 
queue-based decoupling for multi-process workloads.  
Rich renders multi-colour output tuned to each terminal, while adapters and dump exporters support configurable formats and templates.  
Each runtime captures the active user, short hostname, process id, and PID chain automatically, so every sink receives consistent system identity fields.  
The public API stays intentionally small: initialise once, bind context, emit logs (with per-event `extra` payloads), dump history in text/JSON/HTML, and shut down cleanly.

> **Python requirement:** lib_log_rich targets Python 3.13 and newer.

- colored terminal logs via rich, with UTC or local timestamps
- supports journald
- supports Windows Event Logs
- supports Graylog via Gelf (and gRPC after adding Open Telemetry Support)
- supports quick log-dump with filtering from the ringbuffer without leaving the application
- opt-in `.env` loading (same precedence for CLI and programmatic use)
- Open Telemetry Support on user (Your) request - not implemented yet (because I do not need it myself). If You need it, let me know.
- optional `diagnostic_hook` callback that observes the runtime without modifying it. The hook lets you wire internal telemetry (queue events, rate limiting), health checks, or debugging dashboards into metrics systems like grafana, while keeping the logging pipeline decoupled from specific monitoring stacks.
- [EXAMPLES.md](EXAMPLES.md) — runnable snippets from Hello World to multi-backend wiring.

---

## Installation

For a quick start from PyPI:

```bash
pip install lib_log_rich
```

Detailed installation options (venv, pipx, uv, Poetry/PDM, Conda/mamba, Git installs, and packaging notes) live in [INSTALL.md](INSTALL.md).

---

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

# Inspect the recent history (text/json/html_table/html_txt)
print(log.dump(dump_format="json"))

log.shutdown()
```

### Contextual metadata (`extra=`)

The optional `extra` mapping travels alongside each event. The runtime copies it into the structured payload, scrubs matching keys, retains it in the ring buffer, and forwards it to every adapter (console, Graylog, journald, Windows Event Log, dumps). Use `extra` for request-specific fields such as ports, tenant IDs, or feature flags—anything that helps downstream tooling interpret the log entry.

Quick smoke-test helpers ship with the package:

```python
log.hello_world()
try:
    log.i_should_fail()
except RuntimeError as exc:
    print(exc)
```

### Opt-in `.env` loading

`lib_log_rich` has always honoured real environment variables over function arguments (`LOG_SERVICE`, `LOG_CONSOLE_LEVEL`, and friends). The new `.env` helpers let you keep that precedence while sourcing defaults from a project-local file:

```python
import lib_log_rich as log
import lib_log_rich.config as log_config

log_config.enable_dotenv()  # walk upwards from cwd, load the first .env found
log.init(service="svc", environment="dev", queue_enabled=False)
...
log.shutdown()
```

Key points:

- `.env` loading is explicit – nothing is read unless you call `enable_dotenv()` (or `load_dotenv()`).
- Precedence stays intact: CLI flag ➝ real `os.environ` ➝ discovered `.env` ➝ defaults.
- Search uses `python-dotenv.find_dotenv(usecwd=True)` and stops once `.env` appears or the filesystem root is reached.
- Pass `dotenv_override=True` when you intentionally want `.env` values to win over real environment variables.

See [DOTENV.md](DOTENV.md) for more detail, examples, and CLI usage.

---

## CLI entry point

`lib_log_rich` ships with a rich-click interface for quick diagnostics, demos, and automation. See [CLI.md](CLI.md) for the full command breakdown, option tables, and usage examples. Quick highlight: run `python -m lib_log_rich` for the metadata banner, or use `lib_log_rich logdemo` to preview console themes and generate text/JSON/HTML dumps (with optional Graylog, journald, or Event Log fan-out).
Filtering options such as `--context-exact job_id=batch` and `--extra-regex request=^api` flow through `logdemo` so CLI dumps can focus on specific workloads without post-processing.

---

## log dump

`log.dump(...)` bridges the in-memory ring buffer to structured exports. See [LOGDUMP.md](LOGDUMP.md) for parameter tables, text placeholder references, and usage notes covering text/JSON/HTML dumps.
When you need to isolate specific events, provide mapping-based filters such as ``context_filters={"job_id": "batch-42"}`` or ``extra_filters={"request": {"icontains": "api"}}``. Entries accept exact values, substring predicates (`contains`/`icontains`), or regex dictionaries (`{"pattern": r"^prefix", "regex": True}`), and multiple keys combine with logical AND while repeated keys OR together.

---


## Public API

| Symbol          | Signature (abridged)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Description                                                                                                                                                                                                                                                                                                                                                                                                          |
|-----------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `init`          | `init(*, service: str, environment: str, console_level="info", backend_level="warning", graylog_endpoint=None, graylog_level="warning", enable_ring_buffer=True, enable_journald=False, enable_eventlog=False, enable_graylog=False, graylog_protocol="tcp", graylog_tls=False, queue_enabled=True, queue_maxsize=2048, queue_full_policy="block", queue_put_timeout=None, queue_stop_timeout=None, force_color=False, no_color=False, console_styles=None, console_format_preset="full", console_format_template=None, dump_format_preset="full", dump_format_template=None, scrub_patterns=None, rate_limit=None, diagnostic_hook=None)` | Composition root. Wires adapters, queue, scrubber, and rate limiter. Must run before calling `bind`, `get`, or `dump`. Environment variables listed below override matching arguments.                                                                                                                                                                                                                               |
| `bind`          | `bind(**fields)` (context manager)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Binds contextual metadata. Requires `service`, `environment`, and `job_id` when no parent context exists; nested scopes merge overrides. Yields the active `LogContext`.                                                                                                                                                                                                                                             |
| `get`           | `get(name: str) -> LoggerProxy`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Returns a `LoggerProxy` exposing `.debug/.info/.warning/.error/.critical`. Each call returns a dict (e.g. `{"ok": True, "event_id": "..."}` or `{ "ok": False, "reason": "rate_limited" }`).                                                                                                                                                                                                                         |
| `LoggerProxy`   | created via `get(name)`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Lightweight facade around the process use case. Methods: `.debug(message, extra=None)`, `.info(...)`, `.warning(...)`, `.error(...)`, `.critical(...)`. All accept a string message plus optional mutable mapping for `extra`.                                                                                                                                                                                       |
| `dump`          | `dump(*, dump_format=\"text\", path=None, level=None, console_format_preset=None, console_format_template=None, theme=None, console_styles=None, context_filters=None, context_extra_filters=None, extra_filters=None, color=False) -> str`                                                                                                                                                                                                                                                                                                                                                                                                | Serialises the ring buffer (text/json/html_table/html_txt). `level` filters events by severity, presets/templates customise text rendering (template wins), `theme`/`console_styles` reuse or override the runtime palette, the new `context_*`/`extra_*` filter mappings narrow results by metadata, and `color` toggles ANSI output for text dumps. Payloads are always returned and optionally written to `path`. |
| `shutdown`      | `shutdown() -> None`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | Flushes adapters, drains/stops the queue, and clears global state. Safe to call repeatedly after initialisation.                                                                                                                                                                                                                                                                                                     |
| `hello_world`   | `hello_world() -> None`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Prints the canonical “Hello World” message for smoke tests.                                                                                                                                                                                                                                                                                                                                                          |
| `i_should_fail` | `i_should_fail() -> None`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Raises `RuntimeError("I should fail")` to exercise failure handling paths.                                                                                                                                                                                                                                                                                                                                           |
| `summary_info`  | `summary_info() -> str`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | Returns the CLI metadata banner as a string without printing it.                                                                                                                                                                                                                                                                                                                                                     |
| `logdemo`       | `logdemo(*, theme="classic", service=None, environment=None, dump_format=None, dump_path=None, color=None, enable_graylog=False, graylog_endpoint=None, graylog_protocol="tcp", graylog_tls=False, enable_journald=False, enable_eventlog=False) -> dict[str, Any]`                                                                                                                                                                                                                                                                                                                                                                        | Spins up a temporary runtime, emits one sample event per level, optionally renders a dump, and records which backends were requested via the `backends` mapping. Use the boolean flags to exercise Graylog, journald, or Windows Event Log sinks from the CLI or API.                                                                                                                                                |

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



### Runtime configuration

`lib_log_rich.init` wires the entire runtime. All parameters are keyword-only and may be overridden by environment variables shown in the last column.

| Parameter                       | Type                        | Default                                             | Purpose                                                                                                    | Environment variable                                 |
|---------------------------------|-----------------------------|-----------------------------------------------------|------------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| `service`                       | `str`                       | *(required)*                                        | Logical service name recorded in each event and used by adapters.                                          | `LOG_SERVICE`                                        |
| `environment`                   | `str`                       | *(required)*                                        | Deployment environment (e.g., `dev`, `prod`).                                                              | `LOG_ENVIRONMENT`                                    |
| `console_level`                 | `str \| LogLevel`           | `LogLevel.INFO`                                     | Lowest level emitted to the Rich console adapter. Accepts names (`"warning"`) or `LogLevel` instances.     | `LOG_CONSOLE_LEVEL`                                  |
| `backend_level`                 | `str \| LogLevel`           | `LogLevel.WARNING`                                  | Threshold shared by structured backends (journald, Windows Event Log).                                     | `LOG_BACKEND_LEVEL`                                  |
| `graylog_endpoint`              | `tuple[str, int] \| None`   | `None`                                              | Host/port for GELF over TCP. When set, combine with `enable_graylog=True`.                                 | `LOG_GRAYLOG_ENDPOINT` (`host:port` form)            |
| `graylog_protocol`              | `str`                       | `"tcp"`                                             | Transport to reach Graylog (`"tcp"` or `"udp"`).                                                           | `LOG_GRAYLOG_PROTOCOL`                               |
| `graylog_tls`                   | `bool`                      | `False`                                             | Enables TLS when using TCP transport.                                                                      | `LOG_GRAYLOG_TLS`                                    |
| `graylog_level`                 | `str \| LogLevel`           | `LogLevel.WARNING`                                  | Severity threshold for Graylog fan-out (applies when `enable_graylog=True`).                               | `LOG_GRAYLOG_LEVEL`                                  |
| `enable_ring_buffer`            | `bool`                      | `True`                                              | Toggles the in-memory ring buffer. When disabled the system retains a small fallback buffer (1024 events). | `LOG_RING_BUFFER_ENABLED`                            |
| `ring_buffer_size`              | `int`                       | `25_000`                                            | Max events retained in the ring buffer when enabled.                                                       | `LOG_RING_BUFFER_SIZE`                               |
| `enable_journald`               | `bool`                      | `False`                                             | Adds the journald adapter (Linux/systemd). Ignored on Windows hosts.                                       | `LOG_ENABLE_JOURNALD`                                |
| `enable_eventlog`               | `bool`                      | `False`                                             | Adds the Windows Event Log adapter. Ignored on non-Windows platforms.                                      | `LOG_ENABLE_EVENTLOG`                                |
| `enable_graylog`                | `bool`                      | `False`                                             | Enables the Graylog adapter (requires `graylog_endpoint`).                                                 | `LOG_ENABLE_GRAYLOG`                                 |
| `queue_enabled`                 | `bool`                      | `True`                                              | Routes events through a background queue for multi-process safety. Disable for simple scripts/tests.       | `LOG_QUEUE_ENABLED`                                  |
| `queue_maxsize`                 | `int`                       | `2048`                                              | Max number of pending events before the full-policy applies.                                               | `LOG_QUEUE_MAXSIZE`                                  |
| `queue_full_policy`             | `str` (`"block"`/`"drop"`)  | `"block"`                                           | Choose whether producers block when the queue is full or drop new events.                                  | `LOG_QUEUE_FULL_POLICY`                              |
| `queue_put_timeout`             | `float` \| `None`           | `None`                                              | Timeout (seconds) for blocking queue puts; ignored when full policy is `"drop"`.                           | `LOG_QUEUE_PUT_TIMEOUT`                              |
| `queue_stop_timeout`            | `float` \| `None`           | `5.0`                                               | Deadline for draining the queue during `shutdown()`; `None` waits indefinitely.                            | `LOG_QUEUE_STOP_TIMEOUT`                             |
| `force_color`                   | `bool`                      | `False`                                             | Forces Rich console colour output even when `stderr` isn’t a TTY.                                          | `LOG_FORCE_COLOR`                                    |
| `no_color`                      | `bool`                      | `False`                                             | Disables colour output regardless of terminal support.                                                     | `LOG_NO_COLOR`                                       |
| `console_styles`                | `mapping[str, str] \| None` | `None`                                              | Optional Rich style overrides per level (e.g. `{ "INFO": "bright_green" }`).                               | `LOG_CONSOLE_STYLES` (comma-separated `LEVEL=style`) |
| `console_theme`                 | `str \| None`               | `None`                                              | Built-in palette name applied to the console and inherited by dumps when unset.                            | `LOG_CONSOLE_THEME`                                  |
| `console_format_preset`         | `str \| None`               | `"full"`                                            | Preset used for console lines (and reused as the default text dump preset when no template is provided).   | `LOG_CONSOLE_FORMAT_PRESET` (defaults to `"full"`)   |
| `console_format_template`       | `str \| None`               | `None`                                              | Custom console template overriding the preset and cascading to text dumps by default.                      | `LOG_CONSOLE_FORMAT_TEMPLATE`                        |
| `dump_format_preset`            | `str \| None`               | `"full"`                                            | Default preset for text dumps when callers do not provide one explicitly.                                  | `LOG_DUMP_FORMAT_PRESET` (defaults to `"full"`)      |
| `dump_format_template`          | `str \| None`               | `None`                                              | Default text dump template overriding the preset.                                                          | `LOG_DUMP_FORMAT_TEMPLATE`                           |
| `scrub_patterns`                | `dict[str, str] \| None`    | `{"password": ".+", "secret": ".+", "token": ".+"}` | Regex patterns scrubbed from payloads before fan-out.                                                      | `LOG_SCRUB_PATTERNS` (comma-separated `field=regex`) |
| `rate_limit`                    | `tuple[int, float] \| None` | `None`                                              | `(max_events, window_seconds)` throttling applied before fan-out.                                          | `LOG_RATE_LIMIT` (`"100/60"` format)                 |
| `diagnostic_hook`               | `Callable`                  | `None`                                              | Optional callback the runtime invokes for internal telemetry (`queued`, `emitted`, `rate_limited`).        | *(code-only)*                                        |
| `config.enable_dotenv()` helper | *(call before `init()`)*    | *(opt-in)*                                          | Walks upwards from a starting directory, loads the first `.env`, and caches the result.                    | `LOG_USE_DOTENV` (CLI/entry points only)             |

Graylog fan-out uses the configured `graylog_level` (default `WARNING` when enabled, automatically tightened to `CRITICAL` when Graylog is disabled). Presets/templates cascade: console settings become the defaults for text dumps unless you provide dump-specific overrides.

The initializer also honours `LOG_BACKEND_LEVEL`, `LOG_FORCE_COLOR`, and `LOG_NO_COLOR` simultaneously—environment variables always win over supplied keyword arguments. When `enable_journald` is requested on Windows hosts or `enable_eventlog` on non-Windows hosts the runtime silently disables those adapters so cross-platform deployments never fail during initialisation.

> **Note:** TLS is only supported with the TCP transport. Combining `graylog_protocol="udp"` with TLS (or setting `LOG_GRAYLOG_PROTOCOL=udp` alongside `LOG_GRAYLOG_TLS=1`) raises a `ValueError` during initialisation.

---

## Environment-only overrides

Set these, restart your process, and the runtime will merge them with the arguments you pass to `init(...)`.

| Variable                      | Default                                 | Effect                                                           |
|-------------------------------|-----------------------------------------|------------------------------------------------------------------|
| `LOG_SERVICE`                 | value passed to `init(service=...)`     | Override the advertised service name.                            |
| `LOG_ENVIRONMENT`             | value passed to `init(environment=...)` | Override the deployment/stage label.                             |
| `LOG_CONSOLE_LEVEL`           | `info`                                  | Minimum level emitted to the console adapter.                    |
| `LOG_BACKEND_LEVEL`           | `warning`                               | Threshold for journald/Event Log adapters.                       |
| `LOG_GRAYLOG_LEVEL`           | `warning`                               | Threshold for Graylog emission.                                  |
| `LOG_RING_BUFFER_ENABLED`     | `true`                                  | Disable (`0`) to skip ring-buffer retention.                     |
| `LOG_RING_BUFFER_SIZE`        | `25000`                                 | Resize the in-memory ring buffer (must stay > 0).                |
| `LOG_ENABLE_JOURNALD`         | `false`                                 | Toggle the journald adapter (ignored on Windows).                |
| `LOG_ENABLE_EVENTLOG`         | `false`                                 | Toggle the Windows Event Log adapter (ignored elsewhere).        |
| `LOG_ENABLE_GRAYLOG`          | `false`                                 | Enable the Graylog adapter; requires `LOG_GRAYLOG_ENDPOINT`.     |
| `LOG_GRAYLOG_ENDPOINT`        | none                                    | Host and port for GELF (`host:port`).                            |
| `LOG_GRAYLOG_PROTOCOL`        | `tcp`                                   | Choose `tcp` or `udp` transport for Graylog.                     |
| `LOG_GRAYLOG_TLS`             | `false`                                 | Wrap the TCP connection in TLS.                                  |
| `LOG_QUEUE_ENABLED`           | `true`                                  | Disable to process fan-out inline without a queue.               |
| `LOG_QUEUE_MAXSIZE`           | `2048`                                  | Queue capacity before the full-policy applies.                   |
| `LOG_QUEUE_FULL_POLICY`       | `block`                                 | `block` waits for space, `drop` rejects new events.              |
| `LOG_QUEUE_PUT_TIMEOUT`       | none                                    | Timeout (seconds) for blocking puts; `<=0` clears it.            |
| `LOG_QUEUE_STOP_TIMEOUT`      | `5.0`                                   | Drain deadline during shutdown; `<=0` waits indefinitely.        |
| `LOG_FORCE_COLOR`             | `false`                                 | Force ANSI colour even when stderr is not a TTY.                 |
| `LOG_NO_COLOR`                | `false`                                 | Strip colour output entirely.                                    |
| `LOG_CONSOLE_THEME`           | none                                    | Apply a built-in Rich theme (`classic`, `dark`, `neon`, …).      |
| `LOG_CONSOLE_STYLES`          | none                                    | Comma-separated overrides such as `INFO=green,ERROR="bold red"`. |
| `LOG_CONSOLE_FORMAT_PRESET`   | `full`                                  | Default Rich preset for console lines and text dumps.            |
| `LOG_CONSOLE_FORMAT_TEMPLATE` | none                                    | Custom template that overrides the preset.                       |
| `LOG_DUMP_FORMAT_PRESET`      | `full`                                  | Default preset when dumping with `dump_format="text"`.           |
| `LOG_DUMP_FORMAT_TEMPLATE`    | none                                    | Custom text-dump template.                                       |
| `LOG_SCRUB_PATTERNS`          | `password=.+,secret=.+,token=.+`        | Extra `field=regex` pairs merged with defaults.                  |
| `LOG_RATE_LIMIT`              | none                                    | Rate limit as `MAX/WINDOW_SECONDS` (e.g., `500/60`).             |
| `LOG_USE_DOTENV`              | `false`                                 | Allow the CLI/module entry point to load a nearby `.env`.        |

Boolean variables treat `1`, `true`, `yes`, or `on` (case-insensitive) as truthy; everything else falls back to the default or provided argument.

---

## Terminal compatibility

Rich automatically detects whether the target is 16-colour, 256-colour, or truecolor, and adjusts the style to the nearest supported palette. For truly minimal environments (plain logs, CI artefacts), set `no_color=True` (or `LOG_NO_COLOR=1`) and Rich suppresses ANSI escapes entirely. Conversely, `force_color=True` (or `LOG_FORCE_COLOR=1`) forces colouring even if `stderr` isn’t a tty (useful in some container setups).

---

## Customising per-level colours

Override the default Rich styles by passing a dictionary to `init(console_styles=...)` or by exporting `LOG_CONSOLE_STYLES` as a comma-separated list, for example:

```
export LOG_CONSOLE_STYLES="DEBUG=dim,INFO=bright_green,WARNING=bold yellow,ERROR=bold white on red,CRITICAL=bold magenta"
```

Values use Rich’s style grammar (named colours, modifiers like `bold`/`dim`, or hex RGB). Omitted keys fall back to the built-in theme. `logdemo` cycles through the built-in palettes (`classic`, `dark`, `neon`, `pastel`) so you can preview styles before committing to overrides.

---

## Further documentation
- [docs/systemdesign/concept.md](docs/systemdesign/concept.md) — product concept and goals.
- [docs/systemdesign/concept_architecture.md](docs/systemdesign/concept_architecture.md) — layered architecture guide.
- [docs/systemdesign/concept_architecture_plan.md](docs/systemdesign/concept_architecture_plan.md) — TDD implementation roadmap.
- [docs/systemdesign/module_reference.md](docs/systemdesign/module_reference.md) — authoritative design reference.
- [INSTALL.md](INSTALL.md) — detailed installation paths.
- [README.md](README.md) — quick overview and parameters.
- [CLI.md](CLI.md) — command reference, options, and CLI usage examples.
- [LOGDUMP.md](LOGDUMP.md) — dump API parameters, placeholders, and usage guidance.
- [CONSOLESTYLES.md](CONSOLESTYLES.md) — palette syntax, themes, and overrides.
- [DOTENV.md](DOTENV.md) — opt-in `.env` loading flow, CLI flags, and precedence rules.
- [SUBPROCESSES.md](SUBPROCESSES.md) — multi-process logging guidance.
- [EXAMPLES.md](EXAMPLES.md) — runnable snippets from Hello World to multi-backend wiring.
- [DEVELOPMENT.md](DEVELOPMENT.md) — contributor workflow.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution expectations, coding standards, and review process.
- [CHANGELOG.md](CHANGELOG.md) — release history and noteworthy changes.
- [DIAGNOSTIC.md](DIAGNOSTIC.md) — diagnostic hook semantics, event catalogue, and instrumentation patterns.

---

## Development

Contributor workflows, make targets, CI automation, packaging sync, and release guidance are documented in [DEVELOPMENT.md](DEVELOPMENT.md).

---

## License

[MIT](LICENSE)
