# Sinks and deployment

How each lib_log_rich sink behaves (console, journald, Windows Event Log, Graylog) and how to configure
production hosts, services and containers per host through `LOG_*` variables without code changes.

## Sink overview

| Sink              | Enabled by                                | Level knob                             | Default               |
|-------------------|-------------------------------------------|----------------------------------------|-----------------------|
| Console           | always                                    | `console_level` / `LOG_CONSOLE_LEVEL`  | INFO, stream `stderr` |
| journald          | `enable_journald` / `LOG_ENABLE_JOURNALD` | `backend_level` / `LOG_BACKEND_LEVEL`  | off, WARNING          |
| Windows Event Log | `enable_eventlog` / `LOG_ENABLE_EVENTLOG` | `backend_level` (shared with journald) | off, WARNING          |
| Graylog (GELF)    | `enable_graylog` / `LOG_ENABLE_GRAYLOG`   | `graylog_level` / `LOG_GRAYLOG_LEVEL`  | off, WARNING          |

Each level gates only its own sink. Environment variables win over the `RuntimeConfig` value
(`LOG_SERVICE` and `LOG_ENVIRONMENT` too).

## Console

Streams: `console_stream` (or `LOG_CONSOLE_STREAM`, which wins) is one of `stdout`, `stderr`, `both`,
`custom`, `none`. Default `stderr`. Anything else is refused:

```
ValueError: Invalid runtime settings: Invalid console stream: 'bogus'; must be one of 'stdout', 'stderr', 'both', 'custom', or 'none'
```

`custom` needs `console_stream_target` (any text IO object):

```
ValueError: Invalid runtime settings: console_stream_target must be provided when console stream is 'custom'
```

`both` writes every line to stdout and stderr. `none` silences the console sink only. The library's own
failure reports (for example a dead Graylog) still go to stderr.

Format presets (`console_format_preset` / `LOG_CONSOLE_FORMAT_PRESET`), from `CONSOLE_PRESETS`:
`full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`. Default is `short_loc` on Linux/macOS and
`short_loc_icon` on Windows. Unknown preset: `Unknown console format preset: 'nope'`. Suffix `_loc` means
local time instead of UTC.

```
short_loc   [15:19:16][WARN]: warn event
short       [13:19:17][INFO <icon>][myapp]: one event      (UTC, logger name, icon glyph)
full        <ISO time> <icon> INFO myapp <em dash> one event environment=prod hostname=... (wraps)
```

`full` and `short` contain icon glyphs and `full` an em dash; for ASCII-only sinks (journal viewers,
legacy consoles, files) use `short_loc` or a custom template.

Custom template (`console_format_template` / `LOG_CONSOLE_FORMAT_TEMPLATE`, overrides the preset),
`str.format` placeholders such as `{level_code}`, `{logger_name}`, `{message}`, `{context_fields}`:

```
$ LOG_CONSOLE_STREAM=stdout LOG_CONSOLE_FORMAT_TEMPLATE='{level_code} {logger_name} {message}' python app.py
INFO myapp one event
WARN myapp warn event
```

Themes and styles: `console_theme` / `LOG_CONSOLE_THEME` (`classic`, `dark`, `neon`, `pastel`; the
config default is `dark`), per-level override `console_styles` mapping or
`LOG_CONSOLE_STYLES="DEBUG=dim,ERROR=bold white on red"` (Rich style strings; see CONSOLESTYLES.md).

Colour and non-tty: Rich decides. With stdout/stderr not a tty (pipe, file, journald, container
runtime) output is plain text. Example: a redirected `short_loc` run contains no escape bytes.
`force_color` / `LOG_FORCE_COLOR=1` forces ANSI even off-tty (output then starts `ESC[97m`...);
`no_color` / `LOG_NO_COLOR=1` removes colour even on a tty.

## journald

Host and Python requirements:

- A systemd host with `/run/systemd/journal/socket` (or `/dev/log`). No Windows, no Alpine/musl
  without systemd, no minimal container.
- The Python bindings are OPTIONAL: extra `pip install "lib_log_rich[journald]"` pulls
  `systemd-python>=235` (Linux only, builds against `libsystemd-dev pkg-config python3-dev` or use the
  distro package `python3-systemd`; see INSTALL_JOURNAL.md).
- Without the bindings the adapter installs a socket fallback (`adapters/structured/journald.py`
  `_send_via_socket`, writes the native datagram to `/run/systemd/journal/socket`, then `/dev/log`).
  Example: with no `systemd` module installed, `init(enable_journald=True)` succeeds and the event
  reaches the journal. If no socket accepts the write the send raises
  `RuntimeError: Unable to write to journald socket...` at emit time; the fan-out catches it and
  reports an `adapter_error` diagnostic instead of crashing the app.
- Do not name a local module or directory `systemd`; it shadows the bindings.

Enable and level:

```python
import lib_log_rich as log

log.init(
    log.RuntimeConfig(
        service="myapp",
        environment="prod",
        enable_journald=True,
        backend_level="INFO",  # default backend_level is WARNING
        console_stream="none",
    )
)
with log.bind(job_id="j1", request_id="r1"):
    log.getLogger("myapp.worker").info("hello journal", extra={"order_id": 42})
log.shutdown()
```

Result of `journalctl SERVICE=myapp -o verbose`: `MESSAGE=hello journal`, `PRIORITY=6`,
`LOGGER_NAME`, `LOGGER_LEVEL`, `EVENT_ID`, `TIMESTAMP` (ISO UTC), `SERVICE`, `ENVIRONMENT`, `HOSTNAME`,
`USER_NAME`, `PROCESS_ID`, `PROCESS_ID_CHAIN`, optional context (`JOB_ID`, `REQUEST_ID`, `USER_ID`,
`TRACE_ID`, `SPAN_ID`), and each `extra` key upper-cased (`ORDER_ID=42`). An extra key colliding with a
reserved field is stored as `EXTRA_<KEY>`. Priorities follow syslog (DEBUG 7, INFO 6, WARNING 4, ERROR 3,
CRITICAL 2).

The adapter does NOT set `SYSLOG_IDENTIFIER`, so `journalctl -t myapp` finds nothing from it. Query by
field: `journalctl SERVICE=myapp ENVIRONMENT=prod -o json` or `-o verbose`.

## Windows Event Log

Windows only; read from the source, not exercised on Windows:

- Needs `pywin32` (`win32evtlogutil.ReportEvent`), installed by the `eventlog` extra
  (`pip install "lib_log_rich[eventlog]"`, >= 6.5.0; before that, `pip install pywin32`). Missing module: `RuntimeError: pywin32 is required
  for Windows Event Log support`, raised at emit time (caught by the fan-out as an adapter error).
- The event source name is the configured `service`. The adapter never registers the source. Register it
  once per host with admin rights (`win32evtlogutil.AddSourceToRegistry`, an installer step, or
  `New-EventLog -LogName Application -Source myapp` in elevated PowerShell) so Event Viewer renders the
  text; without registration Windows still records the event but shows a "description not found"
  header. Runtime writing needs no admin once the source exists.
- Default event IDs INFO 1000, WARNING 2000, ERROR 3000, CRITICAL 4000; event types map INFO/DEBUG to
  Information, WARNING to Warning, ERROR/CRITICAL to Error. First string is the message, then
  `key=value` context and extra lines.
- Level gate is `backend_level`, same as journald.

## Platform guards

`resolve_feature_flags` forces journald off on Windows and the Event Log off everywhere else, after
applying `LOG_ENABLE_JOURNALD` / `LOG_ENABLE_EVENTLOG`. Example with `sys.platform` patched:

```
win32  -> journald=False eventlog=True    (inputs journald=True, eventlog=True)
linux  -> journald=True  eventlog=False
```

So one config with both flags true is safe on either OS, and `LOG_ENABLE_JOURNALD=1` is simply ignored on
Windows. The guard silently drops the sink; there is no warning.

## Graylog (GELF)

```python
log.init(
    log.RuntimeConfig(
        service="myapp",
        environment="prod",
        enable_graylog=True,
        graylog_endpoint=("graylog.example.com", 12201),
        graylog_protocol="tcp",
        graylog_tls=False,
        graylog_level="ERROR",
    )
)
```

- Endpoint: tuple `(host, port)` or `LOG_GRAYLOG_ENDPOINT=host:port` (env wins). Bad forms fail at
  settings resolution: `LOG_GRAYLOG_ENDPOINT must be HOST:PORT`, `... port must be an integer`,
  `... port must be positive`.
- `graylog_protocol` / `LOG_GRAYLOG_PROTOCOL`: `tcp` (default) or `udp`. TLS (`graylog_tls` /
  `LOG_GRAYLOG_TLS`) is TCP only and uses `ssl.create_default_context()` (system CA bundle, hostname
  verified; no custom CA option).
- `graylog_level` default is WARNING (config) and CRITICAL whenever no adapter was created.
- UDP plus TLS with Graylog enabled and an endpoint set: `validate_config` and `init` both refuse it with
  `Invalid runtime settings: TLS is only supported for TCP Graylog transport` (lib_log_rich >= 6.4.2). A
  failed `init` leaves no runtime, so the next `init` works.
- Enabled without an endpoint: `validate_config` and `init` both refuse it with `Invalid runtime
  settings: Graylog is enabled but no endpoint is set (graylog_endpoint or LOG_GRAYLOG_ENDPOINT)`
  (lib_log_rich >= 6.5.1). Earlier versions accepted it and built no Graylog sink, with no error.
- Wire format: one JSON object per event, GELF 1.1, NUL
  terminated: `version`, `short_message`, `host`, `timestamp`, `level` (syslog number, ERROR = 3),
  `logger`, and underscore fields `_service`, `_environment`, `_job_id`, `_user`, `_hostname`, `_pid`,
  `_process_id_chain`, plus each `extra` key (`_k`).
- Level gate: with `graylog_level="ERROR"` a WARNING was not sent, the ERROR was.
- Delivery: 1 second socket timeout. TCP keeps one connection and on a failed send reconnects and
  retries once. After that the event is dropped. No disk buffer, no backoff, no later resend. UDP is
  fire-and-forget, one datagram per event.
- Unreachable server (connection refused): the app keeps running, `init`/`shutdown` return
  normally, the library prints `Adapter GraylogAdapter failed while emitting event <id>: [Errno 111]
  Connection refused` plus a traceback on stderr (even with `console_stream="none"`), and the
  `diagnostic_hook` receives `adapter_error`. Each failed event costs up to two connect attempts of 1 s
  each, run in the queue worker thread when `queue_enabled` (default), so the app is not blocked. With
  `queue_enabled=False` the call blocks the caller.

## OpenTelemetry

Not implemented. `OPENTELEMETRY.md` is a design and test plan (an OTLP handler is proposed); no module in
`src/` references OpenTelemetry or OTLP, there is no extra and no setting. `trace_id` / `span_id` can
be carried in the bound context and are forwarded to journald (`TRACE_ID`, `SPAN_ID`). To feed an OTel
backend today, use Graylog's inputs or a collector reading the journal.

## Deployment recipes

### systemd service (Linux)

Install in the app's venv (the interpreter the unit runs):

```
/opt/myapp/.venv/bin/pip install "lib_log_rich[journald]"    # or rely on the socket fallback
```

`/etc/systemd/system/myapp.service`:

```ini
[Unit]
Description=myapp
After=network-online.target

[Service]
User=myapp
WorkingDirectory=/opt/myapp
EnvironmentFile=-/etc/myapp/logging.env
Environment=LOG_SERVICE=myapp
Environment=LOG_ENVIRONMENT=prod
ExecStart=/opt/myapp/.venv/bin/python -m myapp
Restart=on-failure
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

`/etc/myapp/logging.env` (no quotes needed, one `KEY=value` per line):

```
LOG_ENABLE_JOURNALD=1
LOG_BACKEND_LEVEL=INFO
LOG_CONSOLE_STREAM=none
LOG_NO_COLOR=1
LOG_ENABLE_GRAYLOG=1
LOG_GRAYLOG_ENDPOINT=graylog.example.com:12201
LOG_GRAYLOG_LEVEL=WARNING
LOG_SCRUB_PATTERNS=dsn=postgres://\S+,session=.+
```

Per-host change without code: edit the env file, `systemctl restart myapp`. All names above exist in the
settings resolver; `LOG_SCRUB_PATTERNS` merges with the defaults (result:
`password, secret, token, dsn, session`).

Console versus journald: systemd captures a service's stdout/stderr into the journal too
(`_TRANSPORT=stdout`, identifier = unit name). With both the console sink and `enable_journald` on, every
event lands twice, once as an unstructured line and once with fields. Pick one:

- structured fields (queryable `SERVICE=`, `JOB_ID=`, extras): `LOG_ENABLE_JOURNALD=1` and
  `LOG_CONSOLE_STREAM=none`;
- or plain stream only: console to stderr/stdout with `LOG_CONSOLE_FORMAT_PRESET=short_loc`, journald off;
- or keep both but separate by level: `LOG_CONSOLE_LEVEL=ERROR`, `LOG_BACKEND_LEVEL=INFO`.

Run as a service user that can write the journal socket (any user can; it is world-writable on a default
install). Start the unit and check `journalctl -u myapp` to confirm it runs.

### Container

No journald, no Event Log. Console to stdout, plain, Graylog as the central sink:

```
LOG_SERVICE=myapp
LOG_ENVIRONMENT=prod
LOG_CONSOLE_STREAM=stdout
LOG_NO_COLOR=1
LOG_CONSOLE_FORMAT_PRESET=short_loc
LOG_CONSOLE_LEVEL=INFO
LOG_ENABLE_GRAYLOG=1
LOG_GRAYLOG_ENDPOINT=graylog.example.com:12201
LOG_GRAYLOG_PROTOCOL=tcp
LOG_GRAYLOG_TLS=1
LOG_GRAYLOG_LEVEL=WARNING
```

With `LOG_CONSOLE_STREAM=stdout LOG_NO_COLOR=1 LOG_CONSOLE_FORMAT_PRESET=short_loc`: lines like
`[15:19:16][INFO]: one event` on stdout, nothing on stderr. Keep `LOG_ENABLE_JOURNALD` unset. Handle
SIGTERM in the app and call `shutdown()` before exit (the runtime does not install signal handlers);
container stop waits only for the grace period, so keep `queue_stop_timeout` (default 5 s) below it.

### Windows service

Windows only; read from the source, not exercised on Windows:

```
pip install "lib_log_rich[eventlog]"
LOG_ENABLE_EVENTLOG=1   LOG_BACKEND_LEVEL=WARNING   LOG_CONSOLE_STREAM=none
```

Register the event source named like `service` once, elevated, at install time (see Windows Event Log).
Setting `LOG_ENABLE_JOURNALD` in the same environment is harmless (forced off). A service has no console;
use `none` or a file-backed `custom` stream. Call `shutdown()` in the service stop handler.

### Production checklist

1. Set `service` and `environment` (or `LOG_SERVICE` / `LOG_ENVIRONMENT`); both are required non-empty
   (`service must not be empty`). They become `SERVICE`/`ENVIRONMENT` in journald and `_service` /
   `_environment` in GELF.
2. Choose levels per sink: console `INFO` or `ERROR`, backend `INFO`, Graylog `WARNING`. The defaults
   (backend WARNING) drop INFO from journald.
3. Scrubbing matches FIELD NAMES, case-insensitively and as a whole name, and redacts the value to `***`.
   Example: with `{"password":..., "api_token":..., "dsn":..., "user":...}` as extras, defaults redacted
   `password` but NOT `api_token` (the key `token` does not match `api_token`; add `api_token` itself).
   Message text is never scrubbed: `lg.info("password=hunter2")` prints in clear. Put secrets in
   `extra`, never in the message. Add names with `scrub_patterns={"dsn": r"postgres://\S+"}` or
   `LOG_SCRUB_PATTERNS=name=regex,name2=regex`.
4. Keep the queue on (`queue_enabled=True`, default; `queue_maxsize` 2048, `LOG_QUEUE_MAXSIZE`,
   `queue_full_policy` `block` with `queue_put_timeout` 1.0 s). Call `lib_log_rich.shutdown()` on every
   exit path (`finally`, signal handler, `atexit`): it drains the queue and flushes adapters, so queued
   Graylog events are not lost. A second `shutdown()` raises `RuntimeError: ...init() must be called
   before using the logging API`; a second `init()` without shutdown raises `RuntimeError: ...cannot be
   called twice without shutdown()`. Use `flush()` to drain without stopping.
5. Reload safely: `validate_config(new)` refuses exactly what `init` would refuse (environment overrides
   included) and starts nothing, so the running runtime survives a bad config:

   ```python
   def reload(cfg: log.RuntimeConfig) -> None:
       log.validate_config(cfg)  # ValueError "Invalid runtime settings: ..." leaves the old runtime
       log.shutdown()
       log.init(cfg)
   ```

   Example: `console_stream="bogus"` raised and the old runtime kept logging. From 6.5.1 it also catches
   Graylog enabled without an endpoint (above).
6. Ring buffer: `enable_ring_buffer` default on, `ring_buffer_size` default 25000 events held in memory
   (`LOG_RING_BUFFER_SIZE`, `LOG_RING_BUFFER_ENABLED=0` to disable). Size it for the dump window you
   need on failure; shrink it on small containers, since payload limits cap each event (message 4096
   chars, 25 extra keys, 8192 extra bytes).
7. Rate limit noisy loggers with `rate_limit=(max_events, window_seconds)` or `LOG_RATE_LIMIT=100:60`.
8. Smoke-test after deploy: `journalctl SERVICE=myapp -n 5 -o verbose` for journald, a test ERROR for
   Graylog, and check stderr of the service for `Adapter ... failed while emitting` lines.
