# CLI Reference

This document gathers the command reference, options, and examples for the `lib_log_rich` rich-click interface.

## Commands

| Command | Usage | Description | Key Options |
|---------|-------|-------------|-------------|
| `lib_log_rich` (root) | `lib_log_rich [OPTIONS] [COMMAND]` or `python -m lib_log_rich` | Prints the metadata banner when no subcommand is provided; stores traceback and preset defaults for child commands. | `--use-dotenv/--no-use-dotenv`, `--traceback/--no-traceback`, `--console-format-preset`, `--console-format-template` (forwarded to subcommands). |
| `info` | `lib_log_rich info` | Writes the installation metadata banner for automation. | Inherits root options. |
| `hello` | `lib_log_rich hello` | Emits the hello-world smoke test message. | Inherits root options. |
| `fail` | `lib_log_rich fail [--no-traceback]` | Triggers the intentional failure path, returning a non-zero exit for pipeline tests. | Inherits root options; `--no-traceback` suppresses the stack trace. |
| `logdemo` | `lib_log_rich logdemo [OPTIONS]` | Previews console themes, emits sample events, and optionally persists or streams dumps while exercising optional backends. | `--theme`, `--dump-format {text,json,html_table,html_txt}`, `--dump-path`, `--console-format-preset`, `--console-format-template`, `--dump-format-preset`, `--dump-format-template`, `--enable-graylog/--graylog-*`, `--enable-journald`, `--enable-eventlog`, context/extra filtering options. |
| `stresstest` | `lib_log_rich stresstest` | Launches an interactive Textual TUI to stress-test runtime settings, payload limits, and adapters while streaming diagnostics. | Reads defaults from `.env`/`LOG_*`; all runtime knobs configurable via the UI. |

## `logdemo` Options

| Option | Type / Default | Description |
|--------|----------------|-------------|
| `--theme` | Repeatable string; defaults to all themes | Limits previews to specific console palettes (case-insensitive). |
| `--service`, `--environment` | Strings; default `logdemo` / `demo-<theme>` | Override the metadata stamped on demo events. |
| `--dump-format` | `text`, `json`, `html_table`, `html_txt` | Chooses the dump renderer executed after emitting demo events. |
| `--dump-path` | File or directory path | Persists dumps per theme (pattern `logdemo-<theme>.<ext>`); stdout when omitted. |
| `--console-format-preset`, `--console-format-template` | Preset (`full`, `short`, `*_loc`) or custom string | Control the Rich console layout during the demo (template takes precedence). |
| `--dump-format-preset`, `--dump-format-template` | Preset or custom string | Customise text / HTML text dump layout when applicable. |
| `--enable-graylog`, `--graylog-endpoint`, `--graylog-protocol`, `--graylog-tls` | Flags / strings (`tcp`, `udp`; TLS off) | Exercise the Graylog adapter with optional endpoint override and TLS. |
| `--enable-journald` | Flag | Sends demo events to systemd-journald (silently ignored on non-Linux hosts). |
| `--enable-eventlog` | Flag | Sends demo events to the Windows Event Log (ignored on non-Windows hosts). |
| `--context-exact`, `--context-contains`, `--context-icontains`, `--context-regex` | `KEY=VALUE` (repeatable) | Filter `LogContext` attributes using exact, substring, or regex predicates (AND across keys, OR across repeated keys). |
| `--context-extra-exact`, `--context-extra-contains`, `--context-extra-icontains`, `--context-extra-regex` | `KEY=VALUE` (repeatable) | Apply the same predicate family to `LogContext.extra`. |
| `--extra-exact`, `--extra-contains`, `--extra-icontains`, `--extra-regex` | `KEY=VALUE` (repeatable) | Filter `LogEvent.extra` fields before dump rendering. |

## Examples

```
# Quick sanity checks for adapters, presets, and formats
python -m lib_log_rich
lib_log_rich info

# Trigger the smoke helpers (structured adapters stay disabled unless you opt in)
lib_log_rich hello
lib_log_rich fail
lib_log_rich --no-traceback fail

# Preview console colour themes, Graylog, journald, Event Log
lib_log_rich logdemo
lib_log_rich --use-dotenv logdemo --theme classic --dump-format json --service my-service --environment prod
lib_log_rich logdemo --dump-format html_table --dump-path ./logs
lib_log_rich logdemo --enable-graylog --graylog-endpoint 127.0.0.1:12201
lib_log_rich logdemo --enable-journald
lib_log_rich logdemo --enable-eventlog
lib_log_rich logdemo --dump-format json --context-exact job_id=alpha

# Override console/dump layouts to test presets or custom templates
lib_log_rich logdemo --console-format-preset short
lib_log_rich logdemo --console-format-preset short_loc
lib_log_rich logdemo --dump-format text --console-format-preset short_loc --dump-format-template "{hh_loc}:{mm_loc}:{ss_loc} [{theme}] {message}" --console-format--template "{message}"
```

## `stresstest` Command

The `stresstest` subcommand launches a whiptail-inspired Textual interface that can emit large volumes of synthetic log events against the configured runtime. It relies on the public `console_adapter_factory` hook and the queue-backed console adapters — `QueueConsoleAdapter` (threaded) and `AsyncQueueConsoleAdapter` (asyncio) with ANSI export by default — so the console panes stream live logs without monkey-patching the runtime, while preserving the same console level filtering and Rich styling as the default adapter. Every knob exposed by `lib_log_rich.init` and the payload limit settings can be adjusted on-screen; defaults are populated from the active environment and `.env` (via `LOG_*` variables). While the stress run is active, the sidebar reports progress (records emitted, throughput, elapsed time) and streams diagnostic hook events (queue drops, truncations, worker failures, etc.) in real time. Use it to exercise queue policies, Graylog/journald/Event Log adapters, scrubbing patterns, and payload limits before enabling them in production.

> **Requirement:** The UI depends on [Textual](https://textual.textualize.io/). Install dev extras (`pip install -e .[dev]`) or add `textual>=0.50` to your environment to use this command.

Use `--enable-graylog` to send the sample events to a running Graylog instance; combine it with `--graylog-endpoint` (defaults to `127.0.0.1:12201`), `--graylog-protocol`, and `--graylog-tls` when you need alternative transports.

Behind the scenes the stresstest feeds both `QueueConsoleAdapter` and `AsyncQueueConsoleAdapter`, so queued log lines are available to thread-based consumers and asyncio tasks simultaneously. Switch the adapters to `export_style="html"` (or copy the Flask SSE example) when you want to surface the same stream inside a browser UI. Platform-specific sinks are equally easy to exercise: `--enable-journald` uses `systemd.journal.send` on Linux hosts, while `--enable-eventlog` binds the Windows Event Log adapter (both flags are safely ignored when the host does not support the backend).

`.env` support follows the same precedence as the library API: `--use-dotenv` (or `LOG_USE_DOTENV=1`) triggers a search before command dispatch; `--no-use-dotenv` forces the CLI to skip `.env` even when the toggle is set.
