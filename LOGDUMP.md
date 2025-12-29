# Dump Reference

`log.dump(...)` bridges the in-memory ring buffer to structured exports. Use it for on-demand snapshots (human-readable text, machine-friendly JSON, or HTML tables) without stopping the runtime. You can filter, colourise text output, and write the dump to disk or keep it in memory for quick inspection. Each event carries `user_name`, `hostname`, and `process_id` in the context so templates (e.g. `{user_name}`, `{hostname}`, `{process_id}`) and structured outputs expose system identity automatically.

`log.dump(...)` parameters in detail:

| Parameter | Type / Default | Description |
|-----------|----------------|-------------|
| `dump_format` | `text`, `json`, `html_table`, `html_txt` (default `text`) | Renderer selection. `html_table` mirrors the structured table view; `html_txt` renders Rich-coloured preformatted text (monochrome when `color=False`). |
| `path` | `Path` / string / `None` | Optional destination. When provided, writes the dump to disk and still returns the string. |
| `level` | `LogLevel` / name / `None` | Minimum severity filter (case-insensitive). Events below this threshold are excluded. |
| `console_format_preset` | Preset string / `None` | Optional preset for text/HTML text dumps (`full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`). Defaults to `full`. Platform-specific defaults apply to console output: Windows uses `short_loc_icon`, Linux/Mac use `short_loc`. |
| `console_format_template` | String / `None` | Custom layout overriding the preset when provided. |
| `theme` | String / `None` | Overrides the runtime theme used for colouring text/HTML text dumps. |
| `console_styles` | Mapping / `None` | Level-to-Rich-style overrides applied to text/HTML text dumps (takes precedence over theme). |
| `context_filters` | Mapping / `None` | Field predicates applied to `LogContext` attributes before rendering. Supports exact/contains/`icontains`/regex entries. |
| `context_extra_filters` | Mapping / `None` | Predicates matched against `LogContext.extra` values with the same operators as `context_filters`. |
| `extra_filters` | Mapping / `None` | Predicates applied to `LogEvent.extra` values prior to formatting. |
| `color` | Bool (`False`) | When `True`, text/HTML text dumps emit ANSI colours; otherwise output stays monochrome. |

Filters support context and extra predicates alongside the level filter.


### Filter specification

Filters accept mapping inputs where keys identify fields and values describe predicates. Supported shapes are:

- Plain values (e.g. `{"job_id": "batch-42"}`) for exact matches.
- Dictionaries with a single operator: `{"contains": "value"}`, `{"icontains": "value"}`, or `{"pattern": "^prefix", "regex": True}`.
- Compiled regular expressions passed via `{"pattern": re.compile("^prefix"), "regex": True}`.
- Sequences to express OR conditions: `{"service": ["checkout", "billing"]}`.

All predicates for different keys combine with logical AND, while multiple predicates for the same key evaluate with OR semantics. Regex filters require `regex=True` to avoid accidental ReDoS; substring operators leave casing untouched unless you choose `icontains`.

```python
from lib_log_rich import dump
payload = dump(
    dump_format="json",
    context_filters={"job_id": "batch-42"},
    extra_filters={"request": {"icontains": "api"}},
)
```

The call above returns JSON matching only events emitted under `job_id=batch-42` with an `extra['request']` field containing `api` (case-insensitive). CLI options such as `--context-exact job_id=batch-42` and `--extra-icontains request=api` generate the same specifications when using `logdemo`.
### Text format placeholders

For example, you can recreate the console layout with:  
`console_format_template="{timestamp} {level_icon} {LEVEL:>8} {logger_name} — {message}{context_fields}"`

| Placeholder | Description |
|-------------|-------------|
| `timestamp` | ISO8601 UTC string (e.g. `2025-09-24T10:15:24.123456+00:00`). |
| `timestamp_trimmed`, `timestamp_no_us` | UTC timestamps trimmed to whole seconds (retaining `+00:00`). |
| `timestamp_trimmed_naive` | Trimmed UTC timestamp without timezone offset. |
| `timestamp_loc` | Host-local ISO8601 timestamp with timezone offset. |
| `timestamp_trimmed_loc` | Local timestamp trimmed to whole seconds with offset. |
| `timestamp_trimmed_naive_loc` | Local timestamp trimmed to whole seconds without offset. |
| `theme` | Active console theme name (helpful when dumps reflect `logdemo`). |
| `level` | Upper-case level name (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `YYYY`, `MM`, `DD`, `hh`, `mm`, `ss` | UTC calendar components derived from `timestamp`. |
| `YYYY_loc`, `MM_loc`, `DD_loc`, `hh_loc`, `mm_loc`, `ss_loc` | Local-time calendar components. |
| `level_code` | Four-character abbreviation (`DEBG`, `INFO`, `WARN`, `ERRO`, `CRIT`) for fixed-width layouts. |
| `logger_name` | Logger identifier provided to `get(...)`. |
| `event_id` | Unique identifier generated per log event. |
| `message` | Raw log message string. |
| `user_name`, `hostname`, `process_id` | System identity fields captured during `init()`. |
| `context` | Full context dictionary (service, environment, job_id, request_id, user metadata, process lineage, trace ids, extras). |
| `process_id_chain` | `>`-joined ancestry of process IDs (root to child order). |
| `extra` | Shallow copy of the `extra` mapping supplied to the logger call. |

Standard `str.format` features apply (`{level_code:>6}`, `{message!r}`, `{process_id:05d}`, etc.), and undefined keys raise a `ValueError`. HTML dumps render a simple table structure intended for reports or quick sharing; colours are intentionally omitted so the output remains readable in any viewer. For multi-process logging patterns (fork/spawn), follow the recipes in [SUBPROCESSES.md](SUBPROCESSES.md).
