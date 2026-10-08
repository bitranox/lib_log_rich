# Ring-buffer dumps and the lib_log_rich CLI

How to read recent log events back out of the process (`dump()`), how to turn that into a crash
report, and what the `lib_log_rich` command line is for. Config fields and stdlib bridging live in
other reference files.

## The ring buffer

Every accepted event is also appended to an in-memory ring buffer; `dump()` renders that buffer. The append
happens in the logging call itself, before the queue, so a `dump()` right after a log call already contains it;
no `flush()` is needed first.
Memory is bounded: the oldest events fall off first.

| Setting                            | Default        | Notes                                                                   |
|------------------------------------|----------------|-------------------------------------------------------------------------|
| `RuntimeConfig.enable_ring_buffer` | `True`         | env `LOG_RING_BUFFER_ENABLED` overrides                                 |
| `RuntimeConfig.ring_buffer_size`   | `25000` events | must be `> 0` (else `ValueError`); env `LOG_RING_BUFFER_SIZE` overrides |

- Retention is by event COUNT, not age: with `ring_buffer_size=3` and 10 events, a dump holds the last 3.
- When retention is disabled the buffer is not removed: a fallback buffer of 1024 events is used
  (so `dump()` still works, and the CLI demos use it). Disabled means "size is ignored", not "dump is empty".
- Events are stored whole (message, context, `extra`, `exc_info`), after scrubbing and payload limits.
- The buffer is only reachable through `dump()`; there is no accessor for raw events.

## `dump()`

```python
from lib_log_rich import dump

text = dump()  # monochrome text, whole buffer
payload = dump(dump_format="json", level="ERROR", path="/var/log/myapp/errors.json")
```

Signature (all keyword-only, returns `str`):

```text
dump(*, dump_format="text", path=None, level=None,
     console_format_preset=None, console_format_template=None,
     theme=None, console_styles=None,
     context_filters=None, context_extra_filters=None, extra_filters=None,
     color=False) -> str
```

| Parameter                 | Behaviour                                                                                                                                           |
|---------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| `dump_format`             | `"text"`, `"json"`, `"html_table"`, `"html_txt"` (or a `DumpFormat`). Anything else: `ValueError: Unsupported dump format: 'xml'`                   |
| `path`                    | Writes the result to this file AND returns it. Missing parent directories are created. The file content equals the return value.                    |
| `level`                   | Minimum severity, case-insensitive name or `LogLevel`: `level="ERROR"` keeps ERROR and CRITICAL.                                                    |
| `console_format_preset`   | Text/`html_txt` layout preset: `full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`. Unknown: `ValueError: Unknown text dump preset: 'bogus'` |
| `console_format_template` | Custom `str.format` layout; wins over the preset.                                                                                                   |
| `theme`, `console_styles` | Colouring for text/`html_txt`; default to the runtime's theme/styles. Only visible with `color=True` (or in `html_txt`).                            |
| `color`                   | `False` (default): text has no ANSI codes. `True`: text carries ANSI escapes.                                                                       |
| filters                   | See below. All must match (AND).                                                                                                                    |

Format notes:

- `json`: a JSON ARRAY of event objects (oldest first). `[]` when nothing matches.
- `text`: one line per event. With no preset/template set, the layout is the long `full` form:
  ISO timestamp, level, logger, event id, message, then sorted `key=value` context and extra fields.
- `html_table`: a standalone HTML table, no colours. `html_txt`: a full `<!DOCTYPE html>` page with the text layout in a `<pre>`.
- Presets and templates apply to `text` and `html_txt` only; `json` and `html_table` ignore them.

### Default layout resolution (text dumps)

For `dump()` with no `console_format_*` argument, the layout comes from the runtime's dump settings:

1. `LOG_DUMP_FORMAT_TEMPLATE` env, else `RuntimeConfig.dump_format_template`
2. `LOG_DUMP_FORMAT_PRESET` env, else `RuntimeConfig.dump_format_preset`, else `full`

The env vars beat the config fields. A configured dump template beats a preset passed to
`dump(console_format_preset=...)`; pass `console_format_template=` to override it per call.
With `--use-dotenv` the CLI reads these from `.env`.

### Template placeholders

Standard `str.format` syntax (`{level_code:>6}`, `{message!r}`). Fields built per event:

| Group        | Placeholders                                                                                                             |
|--------------|--------------------------------------------------------------------------------------------------------------------------|
| Time (UTC)   | `timestamp`, `timestamp_trimmed`, `timestamp_no_us`, `timestamp_trimmed_naive`, `YYYY MM DD hh mm ss`                    |
| Time (local) | `timestamp_loc`, `timestamp_trimmed_loc`, `timestamp_trimmed_naive_loc`, `YYYY_loc MM_loc DD_loc hh_loc mm_loc ss_loc`   |
| Level        | `level` / `LEVEL` (upper-case name), `level_name`, `level_code` (`DEBG INFO WARN ERRO CRIT`), `level_icon`, `level_enum` |
| Event        | `logger_name`, `event_id`, `message`                                                                                     |
| Identity     | `user_name`, `hostname`, `process_id`, `process_id_chain`                                                                |
| Structured   | `context`, `extra`, `context_fields` (merged context+extra, as the default layout prints it)                             |
| Source       | `pathname`, `lineno`, `funcName` (from `extra`, `None` when absent), `theme`                                             |

An unknown placeholder raises `ValueError: Unknown placeholder in text template: 'nope'`; it is not
rendered as empty text. Validate templates once at startup rather than inside a crash handler.

```python
print(dump(level="WARNING", console_format_template="{LEVEL:>8} {logger_name} {message} {level_code}"))
#  WARNING app.worker slow WARN
#    ERROR app.worker boom ERRO
# CRITICAL app.worker dead CRIT
```

## Filters

Three mappings, each `{field: spec}`; every key must match (AND). Within one key, a LIST of specs is OR.

| Parameter               | Matches against                                                                                                                                 |
|-------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| `context_filters`       | attributes of the event's `LogContext` (`service`, `environment`, `job_id`, `request_id`, `user_id`, `hostname`, `process_id`, `trace_id`, ...) |
| `context_extra_filters` | entries of `LogContext.extra`                                                                                                                   |
| `extra_filters`         | entries of the event's own `extra` (the `extra={...}` you passed to the log call)                                                               |

Spec forms:

| Form                                  | Meaning                                                                                         |
|---------------------------------------|-------------------------------------------------------------------------------------------------|
| `"batch-42"` (plain value)            | exact equality                                                                                  |
| `{"exact": v}`                        | exact equality                                                                                  |
| `{"contains": "api"}`                 | case-sensitive substring of `str(value)`                                                        |
| `{"icontains": "api"}`                | case-insensitive substring                                                                      |
| `{"pattern": r"^web", "regex": True}` | `re.search`; `regex: True` is mandatory; optional `"flags"` (e.g. `"IGNORECASE"`, int, or list) |
| `re.compile(...)`                     | regex, used directly                                                                            |
| `["a", {"icontains": "b"}]`           | OR across the entries                                                                           |

```python
dump(dump_format="json", context_filters={"job_id": "batch-42"})  # 3 of 4 events
dump(dump_format="json", extra_filters={"request": {"icontains": "api"}})  # matches "API/v1"
dump(dump_format="json", extra_filters={"request": {"contains": "api"}})  # 0: case-sensitive
dump(dump_format="json", extra_filters={"request": ["API/v1", "web"]})  # OR
```

A field missing from the event never matches. A mapping spec with two modes
(`{"contains": .., "exact": ..}`) raises `ValueError`; `pattern` without `regex: True` raises
`ValueError: Field 'request' must set 'regex': True to enable pattern filters`.

## Recipe: write recent errors to a file on crash

Install an excepthook that logs the traceback as text (the JSON `exc_info` field holds a `repr` of
the exc-info tuple, not traceback text, so put the formatted traceback in `extra`) and dumps ERROR and above:

```python
import sys
import traceback
from pathlib import Path

import lib_log_rich as lr

CRASH_FILE = Path("/var/log/myapp/crash.json")


def write_crash_dump(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    lr.getLogger("app").critical("unhandled exception", extra={"traceback": text})
    lr.dump(dump_format="json", level="ERROR", path=CRASH_FILE)
    lr.shutdown()
    sys.__excepthook__(exc_type, exc, tb)  # keep the normal traceback and exit code 1


lr.init(lr.RuntimeConfig(service="myapp", environment="prod"))
sys.excepthook = write_crash_dump
```

Example for a run that logged one `error` and then raised: process exit code 1, file holds
a JSON array of 2 events (the ERROR, then the CRITICAL with `extra["traceback"]`). Top-level keys of
each event:

```text
event_id, timestamp, logger_name, level, level_name, level_value, level_code,
level_icon, message, context, extra, exc_info, stack_info
```

`context` keys: `service, environment, job_id, request_id, user_id, user_name, hostname,
process_id, process_id_chain, trace_id, span_id, extra`. `timestamp` is UTC with a `Z` suffix;
`level` is lower-case (`"error"`), `level_name` upper-case, `level_value` numeric (ERROR is 40).

Notes: an entry-point `try/except Exception:` that calls the same body works identically (the hook
does not see exceptions you catch). `dump()` raises `RuntimeError` if the runtime is not
initialised, so guard a hook that can fire before `init()`. Always `shutdown()` last.

## CLI: `lib_log_rich`

Installed entry point `lib_log_rich`, also `python -m lib_log_rich`. It is a demo and smoke-test
tool for the library, not a log viewer: it cannot read your application's ring buffer (that lives
in your process). Run `lib_log_rich --help` and `lib_log_rich <command> --help` for the full flag
lists; the tables here list only what an agent typically needs.

Global options (go BEFORE the subcommand: `lib_log_rich --no-traceback fail`):

| Option                                                 | Purpose                                                                                                         |
|--------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------|
| `--version`                                            | print `lib_log_rich version X.Y.Z`, exit 0                                                                      |
| `--use-dotenv` / `--no-use-dotenv`                     | load a nearby `.env` (searched upward from cwd) before running; `LOG_*` values from it then apply (default off) |
| `--traceback` / `--no-traceback`                       | full traceback on errors (default on) or only `RuntimeError: ...` on stderr                                     |
| `--console-format-preset`, `--console-format-template` | console layout forwarded to subcommands                                                                         |
| `--queue-stop-timeout SECONDS`                         | queue drain timeout (`<= 0` waits forever; env `LOG_QUEUE_STOP_TIMEOUT`)                                        |
| `--hello`                                              | print the greeting before the banner when no subcommand is given                                                |

Subcommands:

| Command         | Purpose                                                                                              |
|-----------------|------------------------------------------------------------------------------------------------------|
| (none) / `info` | print the metadata banner (name, title, version, homepage, author); exit 0                           |
| `hello`         | print `Hello World`; exit 0 (smoke test)                                                             |
| `fail`          | raise the intentional `RuntimeError: I should fail`; exit 1 (pipeline negative control)              |
| `logdemo`       | preview console presets and themes by emitting 5 sample events per combination, optionally dump them |
| `stresstest`    | interactive Textual TUI for stress-testing runtime settings; no flags of its own                     |

Exit codes: `0` success, `1` runtime failure (`fail`), `2` usage error (unknown command or option,
invalid choice value; Click prints `Error: No such command 'bogus'.` or `Invalid value for
'--dump-format': 'yaml' is not one of ...` to stderr).

### `logdemo` flags worth knowing

| Flag                                                                                                | Meaning                                                                                                                          |
|-----------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| `--preset P` / `--theme T`                                                                          | repeatable; restrict the preset x theme grid (default: all 5 presets x 4 themes: `classic dark neon pastel`)                     |
| `--dump-format {text,json,html_table,html_txt}`                                                     | render a dump after emitting; printed to stdout unless `--dump-path`                                                             |
| `--dump-path PATH`                                                                                  | write per-combination files: a directory gets `logdemo-<preset>-<theme>.<ext>`; a file path gets `<stem>-<preset>-<theme>.<ext>` |
| `--dump-format-preset`, `--dump-format-template`                                                    | text-dump layout                                                                                                                 |
| `--service`, `--environment`                                                                        | context values stamped on the sample events                                                                                      |
| `--context-exact/-contains/-icontains/-regex KEY=VALUE`                                             | `context_filters` (also `--context-extra-*` and `--extra-*` families); repeatable                                                |
| `--enable-graylog`, `--graylog-endpoint HOST:PORT`, `--graylog-protocol {tcp,udp}`, `--graylog-tls` | send the demo events to a real Graylog                                                                                           |
| `--enable-journald`, `--enable-eventlog`                                                            | send to journald (Linux) or Windows Event Log; ignored elsewhere                                                                 |

```bash
lib_log_rich logdemo --preset short --theme classic --dump-format json --dump-path /path/to/out
# writes /path/to/out/logdemo-short-classic.json
lib_log_rich logdemo --preset short --theme classic --dump-format json --extra-regex 'theme=^zz'
# dump section prints []   (filters applied to the dump)
```

Emission goes to the console; the dump section is printed after a `--- dump (text) preset=... ---`
header. A filter that matches nothing yields an empty dump (`[]` for json), exit 0.

## `logdemo()` Python function

```python
result = lr.logdemo(theme="classic", dump_format="json")
```

Keyword-only; mirrors the CLI (`theme`, `service`, `environment`, `dump_format`, `dump_path`,
`color`, `console_format_preset/template`, `dump_format_preset/template`, the three filter
mappings, and the Graylog/journald/eventlog switches). It initialises a private runtime, emits
5 events (DEBUG to CRITICAL, bound `job_id="logdemo-<theme>"`), renders a dump if `dump_format` is
given, calls `shutdown()`, and returns a `LogDemoResult`:

| Field                             | Value                                                                                       |
|-----------------------------------|---------------------------------------------------------------------------------------------|
| `theme`, `service`, `environment` | resolved values (`service` defaults to `logdemo`, `environment` to `demo-<theme>`)          |
| `styles`                          | level to Rich style mapping, e.g. `{'DEBUG': 'dim', ..., 'CRITICAL': 'bold red'}`           |
| `events`                          | five `ProcessResult` objects (`ok`, `event_id`, `reason`, `queued`, `failed_adapters`)      |
| `dump`                            | the dump string, or `None` when `dump_format` was not given                                 |
| `backends`                        | `BackendStatus(graylog=False, journald=False, eventlog=False)` flags for what was exercised |

It raises `RuntimeError: logdemo() requires lib_log_rich to be uninitialised. Call shutdown() first.`
when a runtime is already active, so do not call it from inside an initialised application. It prints
sample lines to the console as a side effect.

## Stress test TUI

`lib_log_rich stresstest` opens a Textual full-screen interface that emits large volumes of
synthetic events against a runtime you configure on screen (queue policy, payload limits, scrubbing,
Graylog/journald/Event Log adapters), showing throughput and diagnostic hook events (queue drops,
truncations, worker failures). Defaults come from the environment and `.env` (`LOG_*`). Use it to
tune settings before production. It needs an interactive terminal and the `textual` package, so do
not run it from an agent shell, CI step or pipe; there is nothing to script.
