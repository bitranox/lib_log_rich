# Changelog

All notable changes to this project will be documented in this file, following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

## [8.0.0] 2026-10-26 20:47:09

### Changed (breaking)

- `RuntimeConfig` refuses a keyword that is not one of its fields with a pydantic `ValidationError` naming it
  (`extra="forbid"`). It used to drop an unknown keyword silently, so a misspelled setting left the intended value at
  its default. Code that passed extra keys, for example a whole config mapping with `RuntimeConfig(**mapping)`, must
  pass only the fields. A `payload_limits` mapping (or `PayloadLimits(...)`) refuses an unknown key the same way.
- `dump()` refuses a plain string for `theme` and `console_format_preset`, and `logdemo()` for `theme`,
  `console_format_preset` and `dump_format_preset`, with the `TypeError` the console adapters already raise
  (`theme must be a member of ConsoleTheme, got 'neon'`). Their declared types were enum-only, but a valid value
  string slipped through because these enums subclass `str`. `dump_format` and `level` still take value strings.

### Changed

- The bundled `python-logging` skill describes the 8.0 API: the top-level enum exports, the string refusals in
  `dump()`/`logdemo()` and `RuntimeConfig`, the complete root CLI option list, the `AsyncQueueConsoleAdapter` loop
  binding, and how `flush()` and `shutdown()` treat queued events when their deadline passes.

### Removed

- The root CLI options `--console-format-preset` and `--queue-stop-timeout`. They were accepted and stored, and their
  help said they were forwarded, but no subcommand read them; passing one is now a usage error (exit 2). Use the
  subcommand's own options or `LOG_CONSOLE_FORMAT_PRESET` / `LOG_QUEUE_STOP_TIMEOUT`. The root
  `--console-format-template` stays and still reaches `logdemo`.

### Added

- `ConsoleTheme`, `DumpFormat`, `FormatPreset` and `GraylogProtocol` are exported from `lib_log_rich` itself, so a
  `dump()` or `logdemo()` call no longer needs an import from `lib_log_rich.domain` (which still exports them).
- `lib_log_rich.application.use_cases.dump.CaptureDump`: the Protocol `create_capture_dump()` now returns instead of
  `Callable[..., str]`, so a type checker checks the capture call's keywords.
- `AsyncQueueConsoleAdapter(..., loop=...)`: the event loop that owns the queue, for an adapter built outside that
  loop. Without it the adapter uses the loop running at construction.
- `QueueAdapter.debug().wait_for_stop_request(timeout)`: blocks until `stop()` has begun, so a test can hold the
  worker until the stop has chosen to drop or drain. The queue test for `stop(drain=False)` raced without it and
  failed on free-threaded Python 3.14t on Windows.

### Fixed

- `lib_log_rich stresstest`: the dump format list offers the formats the library renders (text, JSON, HTML table,
  HTML text). It offered a `yaml` choice that every run reported as `Dump failed: Unsupported dump format`.
- `lib_log_rich stresstest` stored the package as `self._log`, which shadows the `_log` method of Textual's `App`;
  with Textual devtools connected, Textual's own logging would call the package and fail. Renamed. (Found by reading
  Textual's source, not reproduced with devtools.)
- `AsyncQueueConsoleAdapter` wakes a consumer awaiting `queue.get()` when the runtime's queue worker thread emits
  (the default `queue_enabled=True`). It put segments with `put_nowait` from that thread, which `asyncio.Queue` does
  not support: the segment landed, but the consumer slept until something else woke its loop (measured: 2 s, the
  next timer). An emit off the loop's thread now goes through `call_soon_threadsafe`, so the put and any `on_drop`
  call run on the loop's thread; a segment emitted after the loop closed counts as dropped.

## [7.3.0] 2026-10-26 18:42:16

### Added

- Free-threaded Python 3.14 (`python3.14t`): lib_log_rich installs and runs there with the GIL off, as on 3.15t.
  CI runs the suite on `3.14t` (Linux and Windows) through `[tool.ci] free-threaded = ["3.14", "3.15"]`. The README
  lists which free-threaded builds are supported. 3.13t is not: pydantic-core ships no 3.13t wheel since 2.41.5, its
  build tooling (PyO3 0.29) dropped 3.13t, and orjson has no free-threaded build at all.

### Changed

- `orjson` is a core dependency only below Python 3.14 (`orjson>=3.13.0; python_version < "3.14"`); from 3.14 it is
  the optional `orjson` extra, because no installer marker tells 3.14t from 3.14. A normal (GIL) 3.14 install without
  the extra now encodes with stdlib `json`; add `lib_log_rich[orjson]` to keep the faster encoder.

## [7.2.0] 2026-10-26 16:50:51

### Added

- Free-threaded Python 3.15 (`python3.15t`): lib_log_rich installs and runs there, and the GIL stays off. orjson has
  no free-threaded build (its source build refuses one), so `lib_log_rich.domain.json_codec` encodes events,
  checkpoints and dumps with stdlib `json` wherever orjson cannot run, matching orjson's output (`null` for NaN and
  infinity, ISO datetimes, UUID strings, Enum values); only a float exponent's spelling and ints beyond 64 bits differ.
- The package declares `Programming Language :: Python :: Free Threading :: 2 - Beta`, and CI runs the suite on
  `3.15t` (Linux and Windows) through `[tool.ci] free-threaded`.

### Changed

- `orjson` is a core dependency only below Python 3.15 (`orjson>=3.13.0; python_version < "3.15"`). From 3.15 it is
  the optional `orjson` extra: no installer marker tells a free-threaded build from a normal one. A normal 3.15
  install without the extra runs on stdlib `json`; add `lib_log_rich[orjson]` for the faster encoder.

### Fixed

- `QueueAdapter.stop()` could hang forever when `stop_timeout` is `None`, and with a timeout it could wait the whole
  timeout out. `stop()` raises its stop flag before it queues the stop signal; a worker that finished an event in
  between treated "flag set, queue empty" as the end and quit without taking the signal, which `stop()` then waited
  on. The worker now leaves only on the stop signal. The gap is a few bytecodes wide, so the GIL build rarely hit
  it; on free-threaded 3.15 it hung the Windows CI cell.

## [7.1.0] 2026-10-26 15:31:10

### Added

- `RuntimeConfig.ring_buffer_checkpoint` (a path, default `None`) and its override `LOG_RING_BUFFER_CHECKPOINT`
  give the runtime's ring buffer a JSONL checkpoint (`adapters.JsonlCheckpoint`). `init()` loads the file into the
  buffer, and `flush(flush_ring_buffer=True)`, `shutdown()` and the flush after a dump append to it; before, through
  `init()` all three did nothing. An empty value means none, an existing directory is refused at `init()` /
  `validate_config()`, and a corrupt line makes `init()` raise `ValueError` naming the file and line
  (`validate_config()` does not read the file). The file only grows; rotating it is up to the owner.

### Fixed

- `dump(theme=X)` now colours a dump with `X`'s palette. Because `init()` filled the runtime theme's palette into
  the console styles and a dump used those as explicit overrides, a dump's theme changed nothing on any runtime
  with a theme (the default `DARK` included). Styles the user configured (`console_styles` /
  `LOG_CONSOLE_STYLES`) still win for their levels, and a per-call `console_styles` still beats both. The
  runtime keeps the configured styles apart (`ConsoleAppearance.configured_styles`); `inspect_runtime()` still
  reports the theme-filled styles.
- A console-only dump preset is refused where it is configured. `dump_format_preset=FormatPreset.SHORT_LOC_ICON`
  and `LOG_DUMP_FORMAT_PRESET=short_loc_icon` passed `init()` and `validate_config()`, and every text dump then
  raised `Unknown text dump preset: 'short_loc_icon'`; `init()` and `validate_config()` now refuse them with
  `ValueError` naming the source and the four dump presets. The CLI's `--dump-format-preset` and the stress-test
  TUI no longer offer it: the CLI now exits 2 with an invalid-value error before logging, where it exited 1 at
  the dump. New `FormatPreset.renders_in_text_dump`. `short_loc_icon` stays a valid console preset; a per-call
  `dump(console_format_preset=FormatPreset.SHORT_LOC_ICON)` still raises `ValueError` as before.

## [7.0.1] 2026-10-26 13:31:17

### Fixed

- `getLogger(name).info("%(key)s", {"key": value})` now formats like `logging.Logger`: a
  single non-empty mapping argument is passed through as the format mapping. Before, the
  message rendered as `%(key)s [formatting failed: format requires a mapping, not tuple]`
  (also in 6.x). The stdlib bridge already handled this.

## [7.0.0] 2026-10-26 12:30:04

7.0 types the public API end to end: every fixed choice is an enum, every structured value a typed
model, and input is parsed where it enters. Value strings still work wherever outside input arrives
(`RuntimeConfig`, `LOG_*` variables, the CLI); code that calls the library passes enum members, and
pyright strict now checks those calls.

### Removed

- `config.load_dotenv(search_from, markers, override)`: use
  `enable_dotenv(search_from, markers=..., dotenv_override=...)`.
- The `text_template` keyword (an alias of `format_template`) on `DumpAdapter.dump`,
  `DumpPort.dump` and the capture callable from `create_capture_dump`.
- The `"html"` dump-format alias: `DumpFormat.from_name("html")` and `dump(dump_format="html")`
  raise `ValueError("Unsupported dump format: 'html'")`; use `html_table`.
- The `filters` argument of `DumpPort.dump` / `DumpAdapter.dump` (it was ignored); a custom port
  drops it.
- `FormatPayload.to_dict()` keys `level.icon` and `level.severity`; use `level_icon` and `LEVEL`.
- `rich_console.CONSOLE_PRESETS`: use `FormatPreset` (`[p.value for p in FormatPreset]`).
- `GELFPayload.to_dict()`: `GELFPayload` is a frozen Pydantic model (exported from
  `adapters.graylog`); use `model_dump()` / `model_dump_json()`. The bytes on the wire are unchanged.
- `QueueAdapter`'s hidden-internals redirect (`__getattr__` pointing at `QueueAdapter.debug()`): a
  missing attribute is a plain `AttributeError`.
- The retry in `create_console` without `stream` / `stream_target` when a substituted console
  adapter raised a `TypeError` mentioning `stream`: a substituted adapter class must accept both.
- `LogEvent.from_dict(dict)`. Stored events are read by `adapters.JsonlCheckpoint`.
- `ContextSnapshot.to_json()` / `from_json()`: use
  `lib_log_rich.adapters.context_snapshot.dump_context_snapshot(snapshot)` and
  `load_context_snapshot(text)` (same bytes).
- `ContextBinder.deserialize(dict)` accepting a dict: it takes a `ContextSnapshot`.
- `lib_log_rich.cli.TRACEBACK_SUMMARY_LIMIT` / `TRACEBACK_VERBOSE_LIMIT` (the limits, 500 and
  10000, are unchanged) and the underscore spellings of the root CLI options
  (`--console_format_preset`, `--console_format_template`, `--queue_stop_timeout`): they are now
  an unknown option, exit 2. The kebab-case options are unchanged.
- `runtime._factories.coerce_level` and `_ensure_log_level` (use `LogLevel.coerce`), the
  `runtime._settings` re-export module, the `LoggerProxy` / `coerce_level` re-exports from
  `runtime._composition`, and the runtime-time `FeatureFlags` name in `runtime._factories` (import
  it from `lib_log_rich.runtime.settings`).
- The `ExportStyle` re-export from `lib_log_rich.adapters` and its console modules: import it from
  `lib_log_rich.domain`.

### Changed

- Enums replace strings on every public signature that takes a fixed choice:
  - `RichConsoleAdapter(stream=)` takes `ConsoleStream` (default `STDERR`); `format_preset=` on the
    console, queue-console and dump adapters takes `FormatPreset | None`;
    `QueueConsoleAdapter` / `AsyncQueueConsoleAdapter(export_style=)` take `ExportStyle` (default
    `ANSI`, now an enum in `lib_log_rich.domain`). A plain string there raises
    `TypeError("<param> must be a member of <Enum>, got '<value>'")`.
  - `dump()` / `logdemo()` take `theme: ConsoleTheme`, `console_format_preset` /
    `dump_format_preset: FormatPreset | None`; `logdemo()` takes `dump_format: DumpFormat | None`
    and `graylog_protocol: GraylogProtocol` (default `TCP`).
  - New `ConsoleTheme` (`CLASSIC`, `DARK`, `NEON`, `PASTEL`); `CONSOLE_STYLE_THEMES` is keyed by it.
  - `ProcessResult.reason` is a `DropReason | None`; `SeverityMonitor.record_drop(level, reason)`
    and `SeverityMonitor(drop_reasons=...)` take `DropReason`; `drops_by_reason` and
    `drops_by_reason_and_level` are keyed by `DropReason` (string lookups still work);
    `DROP_REASON_LABELS` is a tuple of `DropReason`. Free-string reasons are no longer normalised.
  - `QueueAdapterDebug.emit_diagnostic(event: DiagnosticEvent, payload)`.
  - All string enums (`QueuePolicy`, `ConsoleStream`, `GraylogProtocol`, `FormatPreset`,
    `ExportStyle`, `DropReason`, `DiagnosticEvent`, `ConsoleTheme`, `PredicateKind`) format as
    their value with `str()` and f-strings on every Python version.
- `RuntimeConfig` parses its enum fields when it is built: `graylog_protocol` (`GraylogProtocol`),
  `queue_full_policy` (`QueuePolicy`), `console_stream` (`ConsoleStream`), `console_theme`
  (`ConsoleTheme`, default `DARK`), `console_format_preset` / `dump_format_preset`
  (`FormatPreset | None`), and `payload_limits` (`PayloadLimits | None`). Value strings parse in
  any case with surrounding space; a blank preset is unset. An unknown value is refused by
  `RuntimeConfig(...)` itself with pydantic's `ValidationError` (a `ValueError`), as wrong-typed
  fields already were: `console_stream`, the presets and payload limits used to be refused only at
  `init()` (`ValueError("Invalid runtime settings: ...")`), and an unknown theme, Graylog protocol
  or queue policy was not refused at all. `logdemo(graylog_protocol="ftp")` therefore raises
  `ValidationError` as well. An unknown `console_format_preset` is refused also beside
  `console_format_template` or a `console_adapter_factory`.
- `LOG_CONSOLE_STREAM`, `LOG_GRAYLOG_PROTOCOL`, `LOG_CONSOLE_FORMAT_PRESET`,
  `LOG_DUMP_FORMAT_PRESET` and `LOG_CONSOLE_THEME` refusals name the variable
  (`Invalid runtime settings: LOG_GRAYLOG_PROTOCOL: Invalid Graylog protocol: 'ftp'; ...`). An
  unknown `LOG_DUMP_FORMAT_PRESET` is refused by `init()` instead of by every later `dump()`, and an
  unknown `LOG_CONSOLE_THEME` is refused instead of ignored. A format preset refusal reads
  `Invalid format preset: 'nope'; must be one of ...`. `LOG_QUEUE_FULL_POLICY` still falls back
  to the default.
- `dump(theme="nope")` raises `ValueError("Unknown console theme: 'nope'")` instead of rendering
  without a palette. `resolve_console_palette(theme, ...)` uses the theme it is given and no longer
  reads `LOG_CONSOLE_THEME` itself (`init()` still applies it).
- `lib_log_rich.bind()` declares its fields (`service`, `environment`, `job_id`, `request_id`,
  `user_id`, `user_name`, `hostname`, `process_id`, `process_id_chain`, `trace_id`, `span_id`,
  `extra`) instead of `**fields`, so pyright flags a misspelt field; other keys go in `extra`.
  `ContextBinder.bind()` and `LogContext.merge()` take the same keyword-only fields, and an unknown
  field raises `TypeError` also when no parent context exists (it was dropped silently there).
  A parentless `bind(extra=None)` no longer crashes.
- `ContextBinder.serialize()` returns a `ContextSnapshot` (exported from `lib_log_rich.domain`)
  instead of a dict; `ContextBinder.seed(context)` is new. Loading a snapshot validates every field
  and lists each refused one: `Invalid context snapshot: stack.0.process_id: Input should be a
  valid integer; ...`; a null `process_id_chain` or `extra` in a frame is refused.
- Ring-buffer persistence goes through a port: `RingBuffer(checkpoint=JsonlCheckpoint(path))`
  replaces `RingBuffer(checkpoint_path=path)` (which now raises `TypeError`); the parameter takes
  any `domain.EventCheckpoint`. The file format is unchanged, so 6.x checkpoints load. A corrupt
  line is refused with `ValueError("Invalid checkpoint <path>, line <n>:")` plus one
  `<field>: <reason>` line per problem; unknown fields, a string where an integer belongs and a
  non-object line are refused too. `init()` configures no checkpoint, so
  `flush(flush_ring_buffer=True)` and the ring-buffer flush at shutdown do nothing there.
- `LoggerProxy(name, process)` and `LoggingRuntime.process` take a `ProcessCallable`;
  `compute_thresholds()` takes `RuntimeSettings`; `ConsoleAppearance.format_preset` is a
  `FormatPreset | None` and `DumpDefaults.format_preset` a `FormatPreset` (default `FULL`).
- `LogContext.replace` / `LogEvent.replace` take typed keyword-only fields.

### Fixed

- `extra_max_total_bytes` could be exceeded by an extra holding a value JSON cannot encode
  (bytes, arbitrary objects): its size was measured from Python's `repr`, not the escaped JSON text
  it is written as.
- Payload-limit diagnostics for nested mappings use the documented event names
  (`extra_value_truncated`, `extra_keys_dropped` and their `context_extra_` forms); the nesting
  level is in the payload's `depth`. They used to append a suffix per level
  (`extra_value_truncated_value_truncated...`).

- An adapter failure (a console, journald, Graylog or Event Log `emit` that raises, or an error
  in the queue worker) is no longer silent under `attach_std_logging()`. The library reports such
  failures through its own `lib_log_rich.*` loggers, which the bridge keeps out of the pipeline;
  installed on root, the bridge also stopped stdlib from falling back to `logging.lastResort`, so
  the report reached no output at all. When only the bridge would see such a warning, it now goes
  to stderr as stdlib would print it; a handler of your own on the chain still gets it alone.
- With the queue on, an adapter failure is counted: `severity_snapshot().drops_by_reason["adapter_error"]`
  goes up and the diagnostic hook gets the once-per-event `adapter_error` naming the failed adapters,
  as it already did with the queue off. The queue worker used to run the bare fan-out and discard
  its list of failed adapters, so only the per-sink diagnostic fired and the counter stayed at 0.
- A long log line is no longer split when the console is not a terminal (stderr redirected to a
  file, a systemd service, CI). Rich wrapped at its default of 80 columns there, so the journal or
  log file got one event as several records, the first ending in a space. On a terminal, lines
  still wrap at its width. The HTML text dump and `QueueConsoleAdapter` without `console_width`
  split long events the same way and now keep each on one line; an explicit `console_width`
  still wraps at that width.

## [6.5.7] 2026-10-22 14:04:09

### Fixed

- The Rich console adapter prints each log line literally. It used to hand the formatted line to
  Rich as a plain string, which Rich parsed as console markup: a message or extra value holding
  `[/]` was dropped from the console without any error, `[red]` vanished, a backslash before `[`
  was lost and `:smile:` became an emoji. Paths, unit names and user input now appear exactly as
  logged; the level colour still applies to the whole line.
- The stress-test TUI status line renders its values literally for the same reason.

### Changed

- `console_format_template` / `LOG_CONSOLE_FORMAT_TEMPLATE` is printed literally too, matching text
  dumps, which never parsed markup. A custom template that copied the old presets' `\[` escaping
  now shows the backslash: drop it. Markup tags in a template no longer style the line. The
  built-in presets render exactly as before.

## [6.5.6] 2026-10-20 15:29:00

### Changed

- The wheel build config no longer lists `include = ["src/lib_log_rich/py.typed"]`. The line had
  no effect (`packages` already ships every file of the package, `py.typed` included) but read as
  if it limited the wheel to that one file. The wheel's contents are unchanged.
- The packaging tests now check that the wheel carries every file of the package (modules and the
  bundled TOML defaults) and no bytecode, instead of `py.typed` alone.

## [6.5.5] 2026-10-19 19:14:03

### Fixed

- `config.enable_dotenv()` with no `search_from` returns `None` when the cwd was removed after the
  chdir, instead of raising a bare `FileNotFoundError` from `os.getcwd()`. It is the same "no
  directory to start from" case as an unreadable cwd on macOS (6.5.4). An explicit `search_from`
  that does not exist still raises `FileNotFoundError`.

### Changed

- The development cooldown `[tool.uv] exclude-newer` is 7 days (was 1 day): the lock admits no
  release file younger than a week, and bmk raises third-party floors only to releases at least
  that old. It is not part of the published package and changes nothing for installers.

## [6.5.4] 2026-10-19 16:53:13

### Fixed

- `config.enable_dotenv()` no longer crashes when the cwd is a directory the process cannot enter
  (a service account running a command from `/root`). It raised `PermissionError` on Python
  3.10-3.13 and a misleading "missing directory" `FileNotFoundError` on 3.14+; such a directory
  now ends the search with no `.env` found, as does a cwd macOS refuses to name (its `getcwd`
  raises `PermissionError` for an unreadable cwd). The search no longer calls `os.chdir`, which moved the
  cwd of every thread while it ran and failed to change back from an unenterable cwd even when
  `search_from` named a readable directory.
- A `.env` that is not valid UTF-8 raises `config.DotenvDecodeError` (a `ValueError`, so the exit
  code stays 22) naming the file and byte offset, instead of a bare `UnicodeDecodeError` naming
  neither. The raw bytes are not chained onto it, and no entry from the file is loaded.

### Documentation

- The bundled `python-logging` skill names `DotenvDecodeError`, the unenterable-directory stop and
  `interpret_dotenv_toggle`.

## [6.5.3] 2026-10-15 17:01:33

### Fixed

- The source distribution ships only the package, its tests, its docs, `README.md`, `LICENSE`,
  `CHANGELOG.md` and `pyproject.toml` (an include list). Earlier sdists also carried every other
  root file and, in a local build, anything hidden only through `.git/info/exclude` or a nested
  `.gitignore`. `tests/test_packaging.py` builds the sdist and wheel and checks both; `hatchling`
  joins the dev extra for it.

### Changed

- Ruff selects `C90` (mccabe), so a function over cyclomatic complexity 10, the house hard limit,
  now fails `make test`; before, nothing enforced it.

### Tests

- The `os_windows`, `os_macos`, `os_linux` and `os_posix` markers now skip a test off its platform
  (`pytest_runtest_setup` in `tests/conftest.py`); registered in `pyproject.toml` alone they skipped
  nothing, so a test trusting its marker ran everywhere. `tests/test_platform_markers.py` pins it.

## [6.5.2] 2026-10-12 22:38:01

### Added
- Python 3.15 is a supported version: declared in the classifiers, so CI and `make test-all` run it.
- `uv.lock` is committed, and `[tool.uv] exclude-newer` sets a cooldown that keeps releases younger
  than it out of the lock. CI installs exactly the locked tree on push and pull request; the nightly
  scheduled run still resolves afresh to catch upstream breakage early.

### Changed
- The install section explains why installing with uv protects against supply-chain attacks and why
  a plain `pip install` from PyPI does not.
- `rtoml` floor raised to 0.14.0, the first release with Python 3.15 wheels.

## [6.5.1] 2026-10-08 19:12:11

### Fixed
- `init()` and `validate_config()` refuse Graylog enabled without an endpoint (`enable_graylog=True` or `LOG_ENABLE_GRAYLOG=1` with neither `graylog_endpoint` nor `LOG_GRAYLOG_ENDPOINT`), with `Invalid runtime settings: Graylog is enabled but no endpoint is set (graylog_endpoint or LOG_GRAYLOG_ENDPOINT)`. Such a config used to be accepted and built no Graylog sink, so every event meant for Graylog was dropped without an error. A config that relied on this must set an endpoint or turn Graylog off.

## [6.5.0] 2026-10-08 18:09:18

### Added
- An `eventlog` extra (`pip install lib_log_rich[eventlog]`) that installs `pywin32` on Windows. The Windows Event Log sink needs it, and it was declared nowhere.

### Fixed
- A `console_styles` mapping that mixes `LogLevel` members and level names (`{LogLevel.INFO: "green", "error": "red"}`) is accepted. It used to be refused with `Console style key is not a log level: '20'`, because the member was coerced to its integer value on the way into `RuntimeConfig`.
- `init()` refuses a `console_adapter_factory` whose adapter lacks a `ConsolePort` method, with `TypeError: console_adapter_factory returned <type>, which lacks ConsolePort method(s): flush`. Such an adapter used to be accepted; `flush()` and `shutdown()` then raised `AttributeError`, and the failed shutdown left the runtime initialised so it could not be started again. `validate_config()` never calls the factory, so it does not report this case.

### Documentation
- The `python-logging` skill states the three behaviours this release changes (the `eventlog` extra, mixed `console_styles` keys, the factory console refusal), each with the version it holds from; mirrored to bitranox-skills as `coding-python-logging` 8.7.1.
- The docs now match the code in fourteen places where they did not, each re-run against the code:
  - README: journald without `systemd-python` falls back to the journal socket (no `RuntimeError` at `init()`), and its events carry no `SYSLOG_IDENTIFIER`. `queue_put_timeout` defaults to `1.0`, not `None`. `RuntimeConfig.graylog_endpoint` takes a tuple (only `LOG_GRAYLOG_ENDPOINT` takes `HOST:PORT`). An unknown `console_theme` is accepted and adds no styles. The `max_level_seen` row of the API table rendered a cell short.
  - INSTALL_JOURNAL.md: a check for lib_log_rich's own events by field (`journalctl SERVICE=...`), since `journalctl -t` cannot find them.
  - OPENTELEMETRY.md: marked as an unimplemented plan.
  - DOTENV.md: the search stops at a `pyproject.toml`/`.git` marker, and the cached result ignores a later `search_from`.
  - SUBPROCESSES.md: a forked child loses every event with the queue on unless it re-initialises; the examples use `getLogger` (not `get`) and the runtime's binder for context propagation.
  - STREAMINGCONSOLE.md: the examples pass a `RuntimeConfig` to `init()`, drop the nonexistent `appearance.console_width`, shut down before sending the stop sentinel (they used to lose the line), use `shutdown_async()` inside an event loop, and give the composite adapter its `flush()`.
  - QUEUE.md and DIAGNOSTIC.md: `queue_full`, `adapter_error`, payload-limit (`*_truncated`, `*_keys_dropped`, `*_total_trimmed`) and formatting diagnostics are catalogued with their real payload keys.
  - LOGDUMP.md: the default dump layout precedence, the missing placeholders, the `exact` mode, regex `flags` and the one-mode rule.
  - CLI.md: `stresstest` has no backend flags, the `--dump-path` file naming, and the theme suffix on `--environment`.

## [6.4.2] 2026-10-08 15:47:22

### Added
- A Claude Code skill, `python-logging` (`skills/python-logging/`), that teaches a coding agent to install, configure, deploy and operate lib_log_rich: configuration and `LOG_*` overrides, the console, journald, Windows Event Log and Graylog sinks, systemd, container and Windows service deployment, scrubbing, the stdlib bridge, queues and multiprocessing, `validate_config`, dumps and the CLI. The repo is now its own plugin marketplace (`/plugin marketplace add bitranox/lib_log_rich`); the skill is mirrored in bitranox-skills as `coding-python-logging`.

### Fixed
- `validate_config()` now refuses TLS over UDP for an enabled Graylog with an endpoint, as `init()` does, with the same message (`Invalid runtime settings: TLS is only supported for TCP Graylog transport`). It used to pass `validate_config()` and fail only in `init()`.

## [6.4.1] 2026-10-08 14:52:36

### Changed
- `[tool.pip-audit].ignore-vulns` is empty. None of its 11 entries matched anything: the packages they covered are absent from the project's resolved tree on every supported Python, or present at fixed versions (pip 26.2.1, cryptography 50.0.2), and the audit is clean without them.

### Documentation
- README: a "Checking a configuration without starting a runtime" section with an example of `validate_config`, and `LogLevel.coerce` among the domain helpers. CONSOLESTYLES.md states that a style key must be a level name; `module_reference.md` lists `validate_config` in the public API and describes what settings resolution refuses.

## [6.4.0] 2026-10-08 14:32:55

### Added
- `validate_config(config)` (exported from `lib_log_rich` and `lib_log_rich.runtime`) raises the `ValueError` `init()` would raise for `config`, with the same message, without starting a runtime or touching a running one. A caller can now check a candidate configuration while logging runs, instead of shutting the runtime down and initialising again to find out.
- `LogLevel.coerce(value)` turns a member, a case-insensitive name or a stdlib integer into a `LogLevel`.

### Changed
- `build_runtime_settings` now refuses everything `init()` refuses: an unknown `console_level`, `backend_level` or `graylog_level` (or `LOG_*_LEVEL`), an uncompilable scrub pattern (config or `LOG_SCRUB_PATTERNS`), and, when the built-in console is used, an unknown console format preset or a console style key that is not a level name. These used to pass settings resolution and fail only while `init()` built the adapters.
- `RuntimeSettings.console_level`, `backend_level`, `graylog_level` and `GraylogSettings.level` are now `LogLevel` members; a level string is parsed when the model is built.
- A refusal now reads `field: reason` (for example `Invalid runtime settings: console_level: Unknown log level: 'loud'`), naming the field or environment variable that held the bad value, instead of Pydantic's multi-line rendering with model names and a documentation URL. An unknown level given to `init()` now carries the `Invalid runtime settings:` prefix like every other settings refusal.

## [6.3.9] 2026-10-05 20:57:40

### Fixed
- A usage error (bad flag, unknown command) now exits `2` and prints Click's usage message, from the console script and `python -m` alike; it exited `1` with a bare `NoSuchOption: ...` line. The cause was in `lib_cli_exit_tools.cli_session`, fixed in lib_cli_exit_tools 2.4.0, which is now the minimum version.

### Changed
- `cli.main()` uses `cli_session()` as typed by lib_cli_exit_tools 2.4.0 instead of casting it.

## [6.3.8] 2026-10-05 19:25:47
### Fixed
- `python -m lib_log_rich` now runs the same `cli.main()` as the `lib_log_rich` console script instead of a second CLI session of its own, so exit codes and error handling cannot drift apart.
### Changed
- The `lib_log_rich` console script now points at `lib_log_rich.cli:main`. `lib_log_rich.__main__:main` stays importable and delegates to it, so existing installs and callers keep working.
- Raised dependency floors: pydantic 2.13.5, rich-click 1.9.9, python-dotenv 1.2.4, plus the dev tool floors.

## [6.3.7] 2026-08-01 00:13:26
### Fixed
- **Console output no longer crashes on a legacy codepage.** A Windows console at codepage 1252
  hands Python an `errors="strict"` stream, so writing a non-ASCII character such as a check mark
  raised `UnicodeEncodeError: 'charmap' codec can't encode character` and the command exited
  non-zero *after* its work had already succeeded. Click does not protect against this, and Rich
  raises the same way through its own writer. The new `safe_console` module degrades at the sink:
  a UTF-8 terminal still receives the character, and only a stream that cannot encode it sees
  `[OK]` / `[X]` / `[!]`. Every `click.echo` call site routes through it, and a test fails if a
  module reaches for `click.echo` or builds an unwrapped `Console(file=sys.stdout)` again.


## [6.3.6] 2026-07-24 17:33:33

### Fixed
- **Latest-ruff CI regression**: this repo had no explicit `[tool.ruff.lint].select`, so ruff 0.16's
  widened default rule set (~920 rules) newly reddened CI. Added the curated bitranox `select` list
  plus Pydantic `runtime-evaluated-base-classes` and per-file-ignores, then fixed the resulting
  ~250 real violations at the root (keyword-only params for internal helpers, ASCII docstrings,
  dict/list comprehensions, `contextlib.suppress` over bare `except: pass`, `logger.exception`
  over `error(..., exc_info=True)`, named constants for magic numbers, `re.escape()`'d
  `pytest.raises(match=...)` patterns, a real invalid-escape ESC byte in a regex literal).
- **TC00x autofix breakage**: the mechanical `TYPE_CHECKING`-only-import autofix moved several
  names into type-checking blocks that were actually needed at runtime - cross-module re-exports
  (`PayloadLimitsProtocol`, `FeatureFlags`), a Pydantic dataclass (`RunConfig`) resolving
  `FilterSpecValue` at build time, and a dozen doctests constructing `LogEvent`/`DumpFormat`/
  `QueuePort`/`Path`/`timedelta` at runtime. Reverted those specific imports and made every
  affected doctest self-contained.
- **ruff-vs-pyright-strict conflicts**: two mechanical autofixes (`B009` get-attr, `B010` set-attr)
  rewrote a `getattr`/`setattr` used deliberately to dodge a strict-mode attribute check into
  direct attribute access, which pyright then flagged. Fixed the root cause instead of reverting:
  cast the test double to `IO[str]` before accessing `.write`; kept `setattr` (with a narrow noqa)
  for a `ModuleType` fake that has no static `journal` attribute.

### Changed
- Keyword-only parameters on internal helpers across `adapters/`, `application/use_cases/`, and
  `runtime/` (Click's `cli()` group callback, `_queue_worker` shutdown helpers, dump/console
  formatting helpers) to satisfy `PLR0917`; left the public getenv-style `env_bool()` and the
  `flush()`/`flush_async()` port implementation positional (narrow noqas) since they are called
  positionally by consumers or threaded through a `Callable[[...], ...]` port type.

## [6.3.5] 2026-06-14

### Changed
- Added a `typed_click.py` facade wrapping rich-click's `option` / `version_option` decorators behind explicit, fully-known signatures, keeping the CLI strict-clean under pyright 1.1.410 (`reportUnknownMemberType`) without disabling the rule (ignore isolated to the facade).
- Bumped `lib_cli_exit_tools` floor to `>=2.3.2`.

## [6.3.4] 2026-04-24 11:14:26

### Fixed
- **pyright 1.1.409 type errors**: Replaced `Iterator[X]` with `Generator[X, None, None]` on `@contextmanager`-decorated functions in `config.py`, `domain/context.py`, `runtime/_state.py`, and two test helpers to satisfy the new `reportDeprecated` rule.
- **Test type-check cascades**: Replaced `dependencies.__class__(**dependencies.__dict__ | {...})` with `dataclasses.replace(...)` in `tests/application/test_use_cases.py` and the six `RuntimeConfig(**base.model_dump() | {...})` patterns with `base.model_copy(update={...})` in `tests/runtime/test_settings_resolvers.py`, eliminating 187 cascading `reportArgumentType` errors.

### Changed
- **bmk Makefile** bumped from 2.3.2 to 2.9.0  -  switches from `uvx bmk@latest` to `uv tool install bmk --with .` for a persistent tool venv that resolves project deps.
- **Dependency pin bumps** picked up by `make test`: `pydantic>=2.13.3`, `rich>=15.0.0`, `pytest>=9.0.3`, `pytest-cov>=7.1.0`, `ruff>=0.15.11`, `pyright[nodejs]>=1.1.409`, `bandit>=1.9.4`, `build>=1.4.4`, `codecov-cli>=11.2.8`, `textual>=8.2.4`, `import-linter>=2.11`, `hypothesis>=6.152.2`, `lib_cli_exit_tools>=2.3.0`, `orjson>=3.11.8`, `python-dotenv>=1.2.2`, `hatchling>=1.29.0`.
- **CI matrix updates** (carried over from previous unreleased commits): bumped codecov action to v6, made pip-audit warning-only, bandit now reads `pyproject.toml`, runner pinning fixes.

### Security
- **CVE list refresh**: pruned stale ignores whose packages are now patched (urllib3, jaraco-context, virtualenv, wheel, pynacl, setuptools, cryptography 46.0.5 ignore). Added documented ignores for currently flagged tooling-only CVEs that do not affect deps shipped with `lib_log_rich`:
  - `CVE-2025-8869` / `GHSA-4xh5-x5gv-qwph` (pip 24.3.1 tar symlink)
  - `CVE-2026-1703` (pip 24.3.1 wheel install path traversal)
  - `CVE-2026-40192` (pillow 12.0.0 FITS decompression bomb)
  - `GHSA-jj8c-mmj3-mmgv` (authlib 1.6.9 CSRF)
  - `CVE-2026-39892` (cryptography 46.0.6 buffer overflow)
  - `CVE-2026-41066` (lxml 6.0.2 XXE)
  - `CVE-2026-40347` (python-multipart 0.0.22 DoS)
  - `GHSA-pjjw-68hj-v9mw` (uv 0.9.11 wheel uninstall path traversal)

## [6.3.3] - 2026-02-13

### Changed
- **Build tooling migration**: Replaced `scripts/` Python build system with `bmk` (external build tool), removing ~5,800 lines of custom build/test/release scripts
- **Makefile overhaul**: Updated Makefile to delegate to `bmk` for all build, test, bump, push, and release targets
- **CI/CD pipeline**: Updated GitHub Actions workflow and added reusable `extract-metadata` action
- **Dev container**: Added `.devcontainer/` configuration for containerized development
- **Dependency updates**: Bumped `ruff` (>=0.15.1), `import-linter` (>=2.10), `hypothesis` (>=6.151.6)

### Security
- **CVE-2026-25990** (pillow 12.0.0): Added documented ignore with risk assessment  -  out-of-bounds write in PSD loader, not a runtime concern for this library
- **CVE-2026-26007** (cryptography 46.0.3): Added documented ignore with risk assessment  -  ECDH/ECDSA subgroup validation, only SECT curves affected

### Removed
- `scripts/` directory: All 19 custom Python build scripts replaced by `bmk`
- `tests/test_scripts.py`: Tests for removed scripts

## [6.3.1] - 2026-01-29

### Fixed
- **ReDoS Risk in Scrubber**: Added `try/except` around `re.compile()` to catch invalid regex patterns and raise a clear `ValueError` with the problematic pattern name
- **Socket Leak in Graylog TCP**: Moved socket acquisition inside try block to ensure cleanup via `_close_socket()` on any failure during TCP send operations
- **Inefficient Dictionary Copying**: Optimized `RegexScrubber._scrub_dict()` with lazy copy-on-write pattern - dictionary copy only occurs when first modification is needed

### Changed
- **Named Constants for Protocol Values**: Added RFC-documented constants for:
  - `GELF_MESSAGE_TERMINATOR` (GELF 1.1 null byte terminator)
  - Syslog severity levels per RFC 5424 (`SYSLOG_DEBUG`, `SYSLOG_INFO`, etc.) in both `graylog.py` and `journald.py`
  - `_STYLE_EXTRACTION_MARKER` for Rich ANSI style extraction in `dump.py`
- **Shared JSON Coercion Utility**: Extracted `coerce_json_value()` to new `adapters/_json_coerce.py` module, eliminating code duplication between Graylog and schema adapters
- **Context Field Sorting**: Optimized `_merge_context_and_extra()` in `_formatting.py` to sort keys once rather than during iteration

### Documentation
- **Blocking Behavior Notes**: Added docstring notes to `GraylogAdapter.emit()` and `StdlibLoggingHandler.emit()` documenting that UDP/TCP socket operations are blocking and recommending queue-based logging for async contexts
- **pip-audit CVE Rationale**: Added detailed comments to `pyproject.toml` explaining each ignored CVE with risk assessment, status, and review dates

### Files Modified
- `src/lib_log_rich/adapters/scrubber.py` - ReDoS fix, lazy copy optimization
- `src/lib_log_rich/adapters/graylog.py` - Socket fix, named constants, shared coercion import, blocking docs
- `src/lib_log_rich/adapters/structured/journald.py` - RFC 5424 syslog constants
- `src/lib_log_rich/adapters/dump.py` - Style extraction marker constant
- `src/lib_log_rich/adapters/_json_coerce.py` - New shared JSON coercion module
- `src/lib_log_rich/adapters/_formatting.py` - Optimized key sorting
- `src/lib_log_rich/application/use_cases/_payload_sanitizer.py` - Added explanatory comments for type: ignore flags
- `src/lib_log_rich/runtime/_stdlib_handler.py` - Blocking behavior documentation
- `pyproject.toml` - Version bump, detailed pip-audit CVE rationale comments
- `tests/adapters/test_graylog_adapter.py` - Updated test for shared coercion module

## [6.3.0] - 2026-01-26

### Added
- **TOML Compatibility Validators**: `RuntimeConfig` now includes two Pydantic `@field_validator(mode="before")` validators that normalise edge-case inputs from TOML configuration files:
  - `_empty_str_as_none` - coerces empty or whitespace-only strings to `None` for `console_format_template` and `dump_format_template`, so `console_format_template = ""` in TOML behaves the same as omitting the key
  - `_empty_seq_as_none` - coerces empty lists/tuples to `None` for `graylog_endpoint` and `rate_limit`, so `graylog_endpoint = []` in TOML is equivalent to `None`

### Documentation
- **CLI.md**: Added complete Root Command Options table documenting all global flags with types and defaults (`--use-dotenv`, `--hello`, `--traceback`, `--console-format-preset`, `--console-format-template`, `--queue-stop-timeout`, `--version`)
- **module_reference.md**: Added TOML compatibility validators documentation to the settings section; added full `RuntimeConfig` parameter reference table with all 30+ parameters, types, and defaults; added `PayloadLimits` defaults table
- **CLAUDE.md**: Added `python_data_architecture_enforcement.md` to Python-specific guidelines; added Stdlib Integration, Severity Analytics, and TOML Compatibility to Key Features list
- **`__init__conf__.py`**: Synced version constant to 6.3.0

### Files Modified
- `src/lib_log_rich/runtime/settings/models.py` - Added `_empty_str_as_none` and `_empty_seq_as_none` validators
- `src/lib_log_rich/__init__conf__.py` - Updated version from 6.2.0 to 6.3.0
- `pyproject.toml` - Version bump to 6.3.0
- `CLI.md` - Added Root Command Options table
- `docs/systemdesign/module_reference.md` - Added TOML validators docs, RuntimeConfig reference, PayloadLimits reference
- `CLAUDE.md` - Added missing guideline, expanded Key Features
- `CHANGELOG.md` - This entry

## [6.2.0] - 2026-01-07

### Added
- **Flush Without Shutdown**: New `flush()` and `flush_async()` functions drain queues and flush all adapters (console, Graylog) without terminating the runtime
  - `flush(timeout=None, *, flush_ring_buffer=False)` - Synchronous variant, blocks until queue drains or timeout
  - `flush_async(timeout=None, *, flush_ring_buffer=False)` - Async variant for use in async contexts
  - Raises `TimeoutError` if queue doesn't drain within timeout (default: 5.0s from `queue_stop_timeout`)
  - Set `flush_ring_buffer=True` to append buffer events to checkpoint file and clear the buffer
  - No-op if no checkpoint path configured (buffer preserved)
  - Unlike `shutdown()`, runtime remains active after flush completes
  - See `QUEUE.md` section 3 for detailed comparison with `shutdown()`

- **Console Flush in Shutdown**: `shutdown()` now flushes console streams (stdout/stderr/custom) before terminating, ensuring all buffered output is written

### Changed
- **RingBuffer.flush() Behavior**: Now appends events to checkpoint file and clears the buffer (previously replaced file and kept buffer). This prevents duplicates since each event is only written once. No-op if no checkpoint path configured.
- **ConsolePort Protocol**: Added `flush()` method to the `ConsolePort` protocol for explicit stream flushing
- **QueuePort Protocol**: Added `wait_until_idle(timeout)` method to support non-blocking queue drain

### Files Modified
- `src/lib_log_rich/domain/ring_buffer.py` - Changed `flush()` to append and clear buffer (was replace and keep)
- `src/lib_log_rich/application/use_cases/shutdown.py` - Added `create_flush()` factory, extracted `_flush_adapters()` helper
- `src/lib_log_rich/application/ports/console.py` - Added `flush()` to `ConsolePort` protocol
- `src/lib_log_rich/application/ports/queue.py` - Added `wait_until_idle()` to `QueuePort` protocol
- `src/lib_log_rich/adapters/console/rich_console.py` - Implemented `flush()` with defensive exception handling
- `src/lib_log_rich/runtime/_state.py` - Added `flush_async` field to `LoggingRuntime`
- `src/lib_log_rich/runtime/_composition.py` - Added `_bind_flush_callable()`, updated `_bind_shutdown_callable()` to include console
- `src/lib_log_rich/runtime/_api.py` - Added `flush()`, `flush_async()`, `_ensure_flush_allowed()` guard
- `src/lib_log_rich/runtime/__init__.py` - Exported `flush`, `flush_async`
- `src/lib_log_rich/__init__.py` - Exported `flush`, `flush_async`
- `src/lib_log_rich/lib_log_rich.py` - Exported `flush`, `flush_async`
- `tests/runtime/test_flush.py` - New test module with 14 tests for flush functionality
- `README.md` - Added `flush` and `flush_async` to Public API table
- `QUEUE.md` - Added section 3 "Flush vs Shutdown" with API documentation
- `CLAUDE.md` - Added "Flush Without Shutdown" to Key Features
- `docs/systemdesign/module_reference.md` - Added `flush()`, `flush_async()`, `create_flush()` documentation

## [6.1.0] - 2025-12-29

### Added
- **Logdemo Preset × Theme Iteration**: The `logdemo` command now iterates through all preset × theme combinations (5 presets × 4 themes = 20 examples by default)
  - New `--preset` option to filter specific presets (repeatable, like `--theme`)
  - Dump files now include preset in filename: `logdemo-<preset>-<theme>.<ext>`
  - Dump headers show both preset and theme: `--- dump (json) preset=short theme=classic ---`
  - Removed `--console-format-preset` from logdemo (presets are now iterated, use `--preset` to filter)

- **New Console Preset `short_loc_icon`**: Minimal format showing local time + level icon + message
  - Template: `\[{hh_loc}:{mm_loc}:{ss_loc}] {level_icon} {message}`
  - Now the default on Windows for better icon rendering

- **Platform-Specific Default Presets**: Console format preset now defaults based on platform:
  - Windows: `short_loc_icon` (icons render well in Windows Terminal)
  - Linux/Mac: `short_loc` (compact format without icons)

- **Cross-Platform Path Utilities**: New `domain/paths.py` module with functions for consistent path handling:
  - `normalize_path(path_input: str | Path) -> Path` - Normalizes paths to platform-native format, handles UNC paths (`//server/share` on POSIX, `\\server\share` on Windows)
  - `path_to_posix(path: Path | str) -> str` - Serializes paths to POSIX format with forward slashes for cross-platform storage/transmission
  - Exported from `lib_log_rich.domain` for public use

- **Path Serialization in Graylog**: `Path` objects in log event extra fields now serialize correctly as POSIX strings in Graylog GELF payloads (previously fell through to `str()` which could produce backslashes on Windows)

### Changed
- **Default Console Theme is now `"dark"`**: Changed `console_theme` default from `None` to `"dark"` for better out-of-box visual experience

- **Replaced `json` with `orjson`**: All JSON serialization now uses `orjson` for improved performance:
  - `domain/events.py` - `LogEvent.to_json()` now uses `orjson.dumps()`
  - `domain/ring_buffer.py` - Checkpoint serialization uses `orjson`
  - `adapters/dump.py` - JSON dump format uses `orjson` with `OPT_INDENT_2`
  - `adapters/graylog.py` - GELF payload serialization uses `orjson`
  - `application/use_cases/_payload_sanitizer.py` - Replaced `JSONEncoder` with orjson-based wrapper

- **Simplified `short_loc` Preset**: Changed from `\[{hh_loc}:{mm_loc}:{ss_loc}]\[{level_code} {level_icon}]\[{logger_name}]: {message}` to `\[{hh_loc}:{mm_loc}:{ss_loc}]\[{level_code}]: {message}` (removed icon and logger name for compactness)

- **Improved `config.example.toml`**: Added default values to all setting docstrings for better discoverability

- **Reduced Cyclomatic Complexity**: Refactored high-complexity functions using declarative field mappings:
  - `_merge_context_and_extra` in `_formatting.py`: 17 → 10 (rank C → B)
  - `GELFPayload.to_dict` in `graylog.py`: 11 → 4 (rank C → A)
  - `_build_fields` in `journald.py`: 12 → 7 (rank C → B)
  - `_sanitize_mapping` in `_payload_sanitizer.py`: 12 → 8 (rank C → B)

### Fixed
- Fixed import sorting (I001) across 25 files
- Fixed dead code warning for `fn` parameter in `UnitOfWork.run()` Protocol method

### Files Modified
- `src/lib_log_rich/adapters/console/rich_console.py` - Added `short_loc_icon` preset, `_default_preset()`, exported `CONSOLE_PRESETS`
- `src/lib_log_rich/cli.py` - Added `--preset` option, refactored logdemo for preset × theme iteration, renamed helper functions for combo handling
- `src/lib_log_rich/cli_stresstest.py` - Added `short_loc_icon` to stresstest preset choices
- `CLI.md` - Updated documentation for logdemo preset × theme iteration
- `config.example.toml` - Added defaults to all docstrings, updated preset lists
- `src/lib_log_rich/domain/paths.py` - New module with path utilities
- `src/lib_log_rich/domain/__init__.py` - Added exports for `normalize_path`, `path_to_posix`
- `src/lib_log_rich/adapters/graylog.py` - Added `Path` handling, refactored `GELFPayload.to_dict`
- `src/lib_log_rich/adapters/_formatting.py` - Refactored `_merge_context_and_extra`
- `src/lib_log_rich/adapters/structured/journald.py` - Refactored `_build_fields`
- `src/lib_log_rich/application/use_cases/_payload_sanitizer.py` - Refactored `_sanitize_mapping`
- `src/lib_log_rich/application/ports/time.py` - Fixed Protocol parameter naming
- `tests/domain/test_paths.py` - New test module for path utilities
- `tests/adapters/test_graylog_adapter.py` - Added Path serialization tests

## [6.0.0] - 2025-12-12

### Changed
- **Python 3.10+ Compatibility**: Lowered minimum Python requirement from 3.13 to 3.10
  - Replaced PEP 695 type parameter syntax (`def func[T]()`) with `TypeVar` definitions
  - Replaced PEP 695 type alias syntax (`type Foo = ...`) with `TypeAlias` annotations
  - Switched from `tomllib` (Python 3.11+) to `rtoml` for TOML parsing across all Python versions
  - Replaced `datetime.UTC` (Python 3.11+) with `datetime.timezone.utc`
  - Added fallback for `logging.getLevelNamesMapping()` (Python 3.11+) using `logging._nameToLevel`
  - Updated ruff target version from `py313` to `py310`
  - CI now tests against Python 3.10, 3.11, 3.12, and 3.13

### Added
- **Dependency**: Added `rtoml>=0.13.0` as dev dependency for cross-version TOML parsing

### Files Modified
- `pyproject.toml` - Updated `requires-python`, ruff target, added rtoml dependency
- `src/lib_log_rich/domain/events.py` - Replaced `datetime.UTC` with `timezone.utc`
- `src/lib_log_rich/runtime/_api.py` - Converted generic function to use TypeVar
- `src/lib_log_rich/runtime/_factories.py` - Replaced `datetime.UTC` with `timezone.utc`
- `src/lib_log_rich/runtime/_stdlib_handler.py` - Added fallback for `getLevelNamesMapping()`
- `tests/adapters/test_journald_adapter.py` - Converted type alias to TypeAlias annotation
- `tests/test_metadata.py` - Switched to rtoml
- `scripts/_utils.py` - Switched to rtoml
- `scripts/toml_config.py` - Switched to rtoml
- `.github/workflows/ci.yml` - Updated test matrix and TOML parsing

## [5.5.1] - 2025-12-08

### Changed
- **Performance Optimization**: Achieved ~46% throughput improvement in payload sanitization:
  - Replaced `OrderedDict` with `dict` (Python 3.7+ maintains insertion order natively)
  - Inlined hot path functions (`_sanitize_key`, `_process_mapping_entry`, `_update_size_tracking`) into `_sanitize_mapping`
  - Changed mapping type check from tuple membership to direct identity comparison (`is _DICT_TYPE`)
  - Results: 24,641 msg/s (up from 16,868 msg/s baseline), 10% reduction in function calls

### Tests
- **OS Markers Standardization**: Added `pytestmark = [OS_AGNOSTIC]` to all 43 test modules for consistent platform-aware testing:
  - Domain layer: 2 files updated
  - Application layer: 6 files updated
  - Adapter layer: 1 file updated
  - Runtime layer: 7 files updated
  - Root-level tests: 2 files updated
- All 721 tests passing with 95.43% coverage

## [5.5.0] - 2025-12-03

### Changed
- **`attach_std_logging()` defaults**: Now defaults `logger_level` to `get_minimum_log_level()` so the stdlib root logger automatically captures all events that any backend might accept (pass `logger_level=None` to leave unchanged), and `propagate=False` to prevent duplicate emission when other handlers exist in the hierarchy.

## [5.4.0] - 2025-12-03

### Added
- **Configuration Enums**: Added type-safe enums for configuration options in `lib_log_rich.domain.enums`:
  - `QueuePolicy` - Queue backpressure handling (`BLOCK`, `DROP`)
  - `ConsoleStream` - Console output destination (`STDOUT`, `STDERR`, `BOTH`, `CUSTOM`, `NONE`)
  - `GraylogProtocol` - Graylog transport protocol (`TCP`, `UDP`)
  - All enums include `from_str()` class method with case-insensitive parsing and `@lru_cache`
  - Enums inherit from `str, Enum` for seamless JSON serialization and Pydantic integration

- **Structured Dataclasses**: Replaced dict returns with typed dataclasses for better IDE support and type safety:
  - `LogDemoResult` and `BackendStatus` in `demo.py` for `logdemo()` return value
  - `GELFPayload` in `graylog.py` for Graylog GELF protocol payloads
  - `FormatPayload` and `TimestampFields` in `_formatting.py` for template rendering
  - All dataclasses use `slots=True, frozen=True` for memory efficiency and immutability

- **Optional journald extra**: Added `systemd-python>=235` as optional `[journald]` extra for Linux systems requiring native journald integration (`pip install lib_log_rich[journald]`)

### Changed
- **Direct Dataclass Attribute Access**: Refactored adapters to access `LogContext` attributes directly instead of converting to dict:
  - `journald.py` - `_build_fields()` now uses direct attribute access
  - `graylog.py` - `_build_payload()` now uses direct attribute access
  - `windows_eventlog.py` - `_build_strings()` now uses direct attribute access
  - `dump.py` - `_build_html_table_row()` now uses direct attribute access
  - `_formatting.py` - `_merge_context_and_extra()` now uses direct attribute access
  - Improves performance by avoiding unnecessary dict conversions in hot paths

### Tests
- All 722 tests passing (6 new doctest cases for enums)
- Updated test mocks to use dataclass attributes instead of dict subscripting
- Added proper attribute mocks for `CustomContext` and `DictContext` test classes

## [5.3.1] - 2025-11-19

### Added
- **Emoji Stripping for Structured Logging**: Implemented automatic removal of emoji and Unicode pictographic symbols from structured logging outputs:
  - Journald adapter now strips emoji from `MESSAGE` field to ensure compatibility with log viewers that don't support UTF-8 emoji
  - Graylog adapter now strips emoji from `short_message` field for clean GELF protocol output
  - Created shared `_text_utils.py` utility module with centralized emoji stripping logic
  - Comprehensive Unicode range coverage including all log level icons (🐞, ℹ, WARN, ✖, ☠, 🔥, 💥)
  - Console output preserves emoji icons for enhanced readability

### Changed
- Extracted duplicate emoji stripping code to shared utility following DRY principle, reducing code duplication by 64 lines

### Tests
- Added comprehensive test coverage for emoji stripping in both journald and Graylog adapters
- All 717 tests passing with 96.23% coverage
- Verified emoji removal across multiple test cases while ensuring console output remains unchanged

## [5.3.0] - 2025-11-19

### Changed
- **Code Quality Improvements**: Comprehensive refactoring project reducing cyclomatic complexity across 9 functions:
  - Eliminated all D-level complexity (100% reduction from 1 function)
  - Reduced C-level complexity by 62.5% (from 8 to 3 functions)
  - Average complexity improved by 15% (from 3.2 to 2.71)
  - 99.5% of codebase now at A/B complexity levels
  - Created 33 focused helper methods following Extract Method, Dispatch Table, and Phase Orchestration patterns
  - All 608 tests passing with zero breaking changes

- **Console Format Improvements**: Enhanced console output format presets:
  - Added space between `level_code` and `level_icon` in short presets for better readability
  - Format: `[HH:MM:SS][CODE ICON][logger]: message`
  - Fixed emoji icon alignment in full preset by removing space between icon and level name
  - Improved visual consistency across different log levels

### Fixed
- Console preset format now properly escapes square brackets with `\\[` to prevent Rich markup interpretation
- Log level alignment now consistent regardless of emoji icon visual width

### Documentation
- Added comprehensive `FORMAT_PLACEHOLDERS_REFERENCE.md` documenting all 50+ available format placeholders
- Created detailed refactoring statistics and analysis in `REFACTORING_STATISTICS.md`
- Generated `COMPLETE_REFACTORING_SUMMARY.md` with before/after complexity comparisons
- Updated test assertions to match new console output format

## [5.2.0] - 2025-11-09

### Changed
- Added `@lru_cache(maxsize=16)` decorator to `SeverityMonitor._normalise_reason` for improved performance when tracking dropped logs:
  - 98.2% cache hit rate across test suite
  - Reduces redundant string normalization for repeated drop reasons (rate_limited, queue_full, adapter_error)
  - Minimal memory overhead with only 3 cached entries in typical usage
  - Complements existing cache optimizations from v5.1.0, improving overall system cache hit rate to 90.2%

### Added
- Cache profiling and analysis tools in `tests/analytics/` directory:
  - `profile_lru_cache.py` - Profiles cache performance across entire test suite (599 tests)
  - `stress_test_cache.py` - Stress tests cache effectiveness with 1,000 log entries
  - `CACHE_PROFILING_REPORT.md` - Initial profiling analysis and results
  - `FINAL_CACHE_ANALYSIS_SUMMARY.md` - Comprehensive cache optimization report
  - `README.md` - Documentation for analytics tools
- Configured pytest to automatically exclude `tests/analytics/` from normal test runs via `pyproject.toml`

### Tests
- All 599 tests pass with new cache optimization
- Analytics tools demonstrate 99.6% cache hit rate under stress (1,000 logs) with 37,592 logs/second throughput
- Cache efficiency ratio of 230.5x (hits per miss) validates optimization effectiveness

## [5.1.0] - 2025-11-09

### Changed
- Added `@lru_cache` decorators to performance-critical functions across the logging pipeline for significant performance improvements:
  - **Tier 1 (Hot Paths)**: `LogLevel.from_name()` (maxsize=16), `LogLevel.from_numeric()` (maxsize=8), and converted `LogLevel` properties (severity, icon, code) to `@cached_property` for 10-30% improvement in event formatting
  - **Tier 2 (Format/Scrub Paths)**: `RegexScrubber._normalise_key()`, `_resolve_template()`, `_resolve_preset()`, `_resolve_theme_styles()`, and `DumpFormat.from_name()` for 5-15% improvement in scrubbing and dump operations
  - **Tier 3 (Configuration)**: `parse_console_styles()` and `parse_scrub_patterns()` for environment parsing optimization
- Performance gains achieved with minimal memory overhead (<1KB total) across all 599 passing tests

## [5.0.1] - 2025-10-18

### Fixed
- `ContextBinder` restores the bootstrap context stack for newly spawned threads or tasks, eliminating spurious "No logging context bound" runtime errors in background workers.

## [5.0.0] - 2025-10-17

### Added
- Added `LoggerProxy.exception(...)` to mirror `logging.Logger.exception` semantics, defaulting `exc_info=True` while keeping stack capture opt-in and flowing through the structured pipeline.
- Console output now defaults to stderr (matching Python's built-in logger) and can be redirected via `RuntimeConfig.console_stream` to `stdout`, `stderr`, `both`, `none`, or a caller-supplied stream (`console_stream_target`), keeping Rich formatting while matching host expectations.
- Documented `LoggerProxy.setLevel(...)`, which now mirrors `logging.Logger` semantics: accepts `LogLevel`, case-insensitive strings, or stdlib numeric levels, and filters events at the proxy before they reach the handler thresholds.
- Introduced `StdlibLoggingHandler` plus the `attach_std_logging()` helper so existing stdlib logging trees can forward `LogRecord` instances into the runtime without refactoring, including recursion guards and full payload normalisation (message/args, `exc_info`, `stack_info`, `stacklevel`, `extra`, call-site metadata).
- Exposed `create_stresstest_app()` so tools and tests can construct the Textual stress-test UI without invoking project configuration or reaching into internal helpers.

### Changed
- Clarified README and system design docs to explain that a log record must satisfy both the proxy level and each handler level (console/backends/Graylog) to emit, and to highlight the accepted level input shapes.
- Enriched formatter payloads with `pathname`, `lineno`, and `funcName` extracted from stdlib records so console and dump presets can display the originating call site; expanded README + system design documentation with a runnable stdlib integration example and architectural notes.

### Fixed
- Queue-backed console adapters now render via an in-memory buffer, preventing Windows codepage encoding failures that previously left the stresstest console panes empty and spammed diagnostics.

### Tests
- Added unit and integration coverage around the stdlib bridge (`tests/runtime/test_stdlib_handler.py`), confirming translation fidelity, recursion protection, and dump visibility when attaching the handler to the root logger.
- Broadened the Hypothesis property for extra payload sanitisation so it tolerates standardised `exc_info` and `stack_info` outputs while still asserting diagnostic emission when sanitisation alters caller data.

## [4.0.0] - 2025-10-17

### Breaking
- Renamed the public logger accessor from `get(name)` to `getLogger(name)` to mirror the standard library API. Call sites must update imports and invocations to use the new name, and any factories that accepted `get` should be passed `getLogger` instead.

### Changed
- Refreshed runtime configuration docs (README, DOTENV, streaming guide, examples) to document expected value ranges for presets, themes, templates, and queue policies, and to reference `getLogger` throughout.
- Updated system design references and example applications (Flask SSE sample, streaming console guide, EXAMPLES.md) to match the new `getLogger` helper and clarify how console adapters consume appearance settings.

## [3.3.0] - 2025-10-14

### Breaking
- Raised minimum supported versions of runtime dependencies to `pydantic>=2.12.0`, `rich>=14.2.0`, `rich-click>=1.9.3`, and `python-dotenv>=1.1.1`. Environments pinned to earlier releases must upgrade before adopting this build.

### Changed
- Retired legacy notebook normalisation during CI execution; the workflow now relies on modern `nbformat` behaviour that ships with Python 3.13 toolchains.
- Updated GitHub Actions workflows to `actions/checkout@v5` and `actions/setup-python@v6`, keeping runners on `ubuntu-latest` while aligning with current 
  action releases.
- Simplified the module entry point to reference CLI traceback limits directly, removing the legacy fallbacks that tolerated older adapters.
- Added a journald socket fallback so the adapter runs even when the `python-systemd` bindings expose only the legacy `systemd` module shim.
- Bumped development tooling floors (pytest 8.4.2, pytest-asyncio 1.2.0, pytest-cov 7.0.0, ruff 0.14.0, pyright 1.1.406, bandit 1.8.6, pip-audit 2.9.0, textual 6.3.0, codecov-cli 11.2.3, hatchling 1.27.0) so local and CI environments share the latest linting and packaging behaviour.
- Dismantled the monolithic log-event pipeline into a string of intent-revealing helpers so rate limiting, queue fan-out, and adapter dispatch each read like their own stanza.
- Rewired runtime composition through small data classes that gather wiring ingredients and queue settings, letting the orchestration read as a declarative recipe while keeping scripts untouched.

### Fixed
- Ensured CLI entrypoints and tests rely on `lib_cli_exit_tools.cli_session` for traceback restoration instead of custom try/except scaffolding, eliminating redundant state management.
- Hardened journald fallbacks to signal clearly when UNIX domain sockets are unavailable and documented the behaviour for non-Linux hosts.
- Documented queue worker zero-timeout semantics and added regression coverage.
- Guarded severity drop accounting against non-string reasons returned by adapters or queue dispatchers, keeping observability counters type safe.

## [3.2.0] - 2025-10-10

### Added
- Introduced a thread-safe `SeverityMonitor` domain service with runtime accessors (`max_level_seen`, `severity_snapshot`, `reset_severity_metrics`) so operators can inspect peak levels, per-level counts, threshold buckets, and drop statistics without scanning the ring buffer.
- Displayed the new severity counters inside the Textual stress test sidebar, alongside existing throughput metrics, for live visibility into high-severity bursts and drop reasons.

### Changed
- Pre-seeded default drop reasons (`rate_limited`, `queue_full`, `adapter_error`) so dashboards receive stable keys even before the first drop occurs.
- Extended README and system design docs with usage examples covering the new analytics API and stress-test enhancements.

### Tests
- Added focused unit and integration coverage for severity counting, drop tracking, and the runtime snapshot helpers.

## [3.1.0] - 2025-10-09

### Added
- added Logger Level Normalisation
- Introduced _ensure_log_level in src/lib_log_rich/runtime/_factories.py:48 to map LogLevel, strings, or stdlib integers into the domain enum and wired LoggerProxy._log plus coerce_level through it; updated docstrings and added the missing logging import so doctests cover numeric conversions.
- Documented the behaviour in README.md:301 by expanding the LoggerProxy row and narrative so callers know _log now normalises mixed level inputs.
- Added regression coverage in tests/runtime/test_logger_proxy.py to assert acceptance of string/int levels, rejection of unsupported types, and the expanded coerce_level contract.

## [3.0.0] - 2025-10-09

### Changed
- Reworked the runtime composition layer so adapter wiring, queue setup, and dump rendering flow through focused helper functions instead of monolithic blocks.
- Simplified shutdown orchestration by funnelling queue drains and adapter flushes through explicit helper steps, making asyncio usage clearer for host applications.

### Fixed
- Captured CLI banner output via a dedicated helper to guarantee `summary_info()` always returns the same newline-terminated payload for documentation and tests.

## [2.0.0] - 2025-10-05

### Added
- Added `console_adapter_factory` support to `runtime.init` so callers can inject custom console adapters (no more monkey-patching).
- Shipped queue-backed console adapters (`QueueConsoleAdapter`, `AsyncQueueConsoleAdapter`) with ANSI/HTML export modes for GUIs, SSE streams, and tests.
- Documented a Flask SSE example (`examples/flask_console_stream.py`) demonstrating live log streaming via the queue-backed adapters.
- Introduced `SystemIdentityPort` and a default system identity provider so the application layer no longer reaches into `os`, `socket`, or `getpass` directly when refreshing logging context metadata.

### Changed
- **Breaking:** `lib_log_rich.init` expects a `RuntimeConfig` instance; keyword-based calls are unsupported to keep configuration cohesive.
- Reworked the Textual `stresstest` console pane to use the queue adapter, restoring responsiveness while preserving coloured output.
- `QueueAdapter.stop()` operates transactionally: it raises a `RuntimeError` and emits a `queue_shutdown_timeout` diagnostic when the worker thread fails to join within the configured timeout. `lib_log_rich.shutdown()` and `shutdown_async()` clear the global runtime only after a successful teardown.
- Optimised text dump rendering by caching Rich style wrappers, reducing per-line allocations when exporting large ring buffers.
- Documentation covers the identity port, queue diagnostics, and changelog format.
- Enforced the documented five-second default for `queue_stop_timeout`, while allowing callers to opt into indefinite waits when desired.
- Set the queue put timeout safety net to a 1-second default (matching the architecture docs) and exposed an `AsyncQueueConsoleAdapter` drop hook so async consumers can surface overflows instead of losing segments silently.

## [1.1.0] - 2025-10-03

### Added
- Enforced payload limits with diagnostic hooks exposing truncation events.

### Changed
- Hardened the async queue pipeline so worker crashes are logged, flagged, and surfaced through the diagnostic hook instead of killing the thread; introduced a `worker_failed` indicator with automatic cooldown reset.
- Drop callbacks that raise emit structured diagnostics and error logs, ensuring operators see failures instead of silent drops.
- Guarded CLI regex filters with friendly `click.BadParameter` messaging so typos no longer bubble up raw `re.error` traces to users.

### Tests
- Added regression coverage for the queue failure paths (adapter unit tests plus an integration guard around `lib_log_rich.init`) and the CLI validation to keep the behaviour locked in.

## [1.0.0] - 2025-10-02

### Added
- Initial Rich logging backbone MVP.
