# lib_log_rich configuration

Everything that shapes a runtime before `init()`: the `RuntimeConfig` fields, their `LOG_*` environment overrides,
`.env` loading, validation, scrubbing, payload limits, rate limiting and levels.

```python
import lib_log_rich as log
from lib_log_rich import RuntimeConfig

config = RuntimeConfig(service="checkout", environment="prod", console_level="info")
log.validate_config(config)  # optional dry run, needs lib_log_rich >= 6.4.0
log.init(config)
...
log.shutdown()
```

`RuntimeConfig` is a frozen Pydantic model. `service` and `environment` are required; every other field has a default.
`LogLevel` is imported from `lib_log_rich.domain`, not from the top-level package.

## Precedence

1. Environment variables (`os.environ`, including anything `enable_dotenv()` loaded) beat keyword arguments.
2. Keyword arguments beat library defaults.
3. A `.env` file only fills variables that are not already set, unless `override=True` is passed (see below).

Example: `LOG_SERVICE=envsvc` with `service="kw"` resolves to `envsvc`; `LOG_CONSOLE_LEVEL=error` beats `console_level="debug"`.
A set-but-empty variable is not uniformly ignored: empty booleans, `LOG_GRAYLOG_ENDPOINT`, `LOG_RATE_LIMIT`, theme, preset and
template variables fall back to the config value, but an empty `LOG_SERVICE`, `LOG_ENVIRONMENT` or `LOG_*_LEVEL` is used as
given and refused (`LOG_CONSOLE_LEVEL: Unknown log level: ''`), and an empty `LOG_RING_BUFFER_SIZE` is refused as non-integer.

## RuntimeConfig fields

| Field                     | Type                                                 | Default                                                       | Env override                  | Meaning                                                                                       |
|---------------------------|------------------------------------------------------|---------------------------------------------------------------|-------------------------------|-----------------------------------------------------------------------------------------------|
| `service`                 | `str`                                                | required                                                      | `LOG_SERVICE`                 | Service name on every event. Blank after strip is refused.                                    |
| `environment`             | `str`                                                | required                                                      | `LOG_ENVIRONMENT`             | Deployment label (`dev`, `prod`, ...). Blank is refused.                                      |
| `console_level`           | `str \| LogLevel`                                    | `INFO`                                                        | `LOG_CONSOLE_LEVEL`           | Console threshold.                                                                            |
| `backend_level`           | `str \| LogLevel`                                    | `WARNING`                                                     | `LOG_BACKEND_LEVEL`           | journald / Windows Event Log threshold.                                                       |
| `graylog_level`           | `str \| LogLevel`                                    | `WARNING`                                                     | `LOG_GRAYLOG_LEVEL`           | Graylog threshold.                                                                            |
| `enable_ring_buffer`      | `bool`                                               | `True`                                                        | `LOG_RING_BUFFER_ENABLED`     | In-memory buffer behind `dump()`. When off, a 1024-event buffer is still built.               |
| `ring_buffer_size`        | `int`                                                | `25000`                                                       | `LOG_RING_BUFFER_SIZE`        | Events retained. Must be > 0.                                                                 |
| `enable_journald`         | `bool`                                               | `False`                                                       | `LOG_ENABLE_JOURNALD`         | journald adapter. Forced off on Windows.                                                      |
| `enable_eventlog`         | `bool`                                               | `False`                                                       | `LOG_ENABLE_EVENTLOG`         | Windows Event Log adapter. Forced off elsewhere.                                              |
| `enable_graylog`          | `bool`                                               | `False`                                                       | `LOG_ENABLE_GRAYLOG`          | Graylog adapter.                                                                              |
| `graylog_endpoint`        | `tuple[str, int] \| None`                            | `None`                                                        | `LOG_GRAYLOG_ENDPOINT`        | `(host, port)`; env form is `HOST:PORT`.                                                      |
| `graylog_protocol`        | `str`                                                | `"tcp"`                                                       | `LOG_GRAYLOG_PROTOCOL`        | `tcp` or `udp`, any case.                                                                     |
| `graylog_tls`             | `bool`                                               | `False`                                                       | `LOG_GRAYLOG_TLS`             | TLS, TCP only.                                                                                |
| `queue_enabled`           | `bool`                                               | `True`                                                        | `LOG_QUEUE_ENABLED`           | Background worker; `False` processes inline.                                                  |
| `queue_maxsize`           | `int`                                                | `2048`                                                        | `LOG_QUEUE_MAXSIZE`           | Pending-event capacity. Must be > 0.                                                          |
| `queue_full_policy`       | `str`                                                | `"block"`                                                     | `LOG_QUEUE_FULL_POLICY`       | `block` or `drop`, any case.                                                                  |
| `queue_put_timeout`       | `float \| None`                                      | `1.0`                                                         | `LOG_QUEUE_PUT_TIMEOUT`       | Seconds a blocking put waits. `None` or <= 0 means wait indefinitely.                         |
| `queue_stop_timeout`      | `float \| None`                                      | `5.0`                                                         | `LOG_QUEUE_STOP_TIMEOUT`      | Drain deadline in `shutdown()`. `None` or <= 0 means wait forever.                            |
| `force_color`             | `bool`                                               | `False`                                                       | `LOG_FORCE_COLOR`             | Rich colour even without a TTY.                                                               |
| `no_color`                | `bool`                                               | `False`                                                       | `LOG_NO_COLOR`                | No colour.                                                                                    |
| `console_theme`           | `str \| None`                                        | `"dark"`                                                      | `LOG_CONSOLE_THEME`           | Palette: `classic`, `dark`, `neon`, `pastel`. An unknown name is accepted and adds no styles. |
| `console_styles`          | `Mapping[str, str] \| None`                          | `None`                                                        | `LOG_CONSOLE_STYLES`          | Rich style per level; `LEVEL=style,...` in env.                                               |
| `console_format_preset`   | `str \| None`                                        | `None` (resolves to `short_loc`; `short_loc_icon` on Windows) | `LOG_CONSOLE_FORMAT_PRESET`   | `full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`, any case.                         |
| `console_format_template` | `str \| None`                                        | `None`                                                        | `LOG_CONSOLE_FORMAT_TEMPLATE` | `str.format` template; replaces the preset.                                                   |
| `console_stream`          | `str`                                                | `"stderr"`                                                    | `LOG_CONSOLE_STREAM`          | `stdout`, `stderr`, `both`, `custom`, `none`.                                                 |
| `console_stream_target`   | `object \| None`                                     | `None`                                                        | none                          | Object with `write()`; required for `custom`, rejected otherwise.                             |
| `console_adapter_factory` | `Callable[[ConsoleAppearance], ConsolePort] \| None` | `None`                                                        | none                          | Replaces the built-in console.                                                                |
| `dump_format_preset`      | `str \| None`                                        | `None` (resolves to `full`)                                   | `LOG_DUMP_FORMAT_PRESET`      | Default text-dump layout.                                                                     |
| `dump_format_template`    | `str \| None`                                        | `None`                                                        | `LOG_DUMP_FORMAT_TEMPLATE`    | Default text-dump template.                                                                   |
| `scrub_patterns`          | `dict[str, str] \| None`                             | `None` (built-ins apply)                                      | `LOG_SCRUB_PATTERNS`          | Extra scrub rules, merged over defaults.                                                      |
| `rate_limit`              | `tuple[int, float] \| None`                          | `None`                                                        | `LOG_RATE_LIMIT`              | `(max_events, window_seconds)`.                                                               |
| `payload_limits`          | `PayloadLimits \| Mapping[str, Any] \| None`         | `None` (defaults below)                                       | none                          | Per-event size caps.                                                                          |
| `diagnostic_hook`         | callable `\| None`                                   | `None`                                                        | none                          | Receives internal diagnostics (drops, truncation, queue state).                               |

Queue behaviour (drop vs block, degraded mode, shutdown) is covered in the runtime reference; only the knobs are listed here.
`console_stream="none"` mutes the console. `LogLevel` keys and string keys may be mixed in one `console_styles` mapping
(>= 6.5.0; earlier versions refused it with `Console style key is not a log level: '20'`).

## Environment value parsing

| Kind                     | Variables                                                                                                                  | Rule                                                                                                 | Bad value                                                                                                                                           |
|--------------------------|----------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| Boolean                  | every `LOG_ENABLE_*`, `LOG_RING_BUFFER_ENABLED`, `LOG_QUEUE_ENABLED`, `LOG_FORCE_COLOR`, `LOG_NO_COLOR`, `LOG_GRAYLOG_TLS` | `1/true/yes/on` is True, `0/false/no/off` is False; case-insensitive, whitespace trimmed             | Ignored: the config value stays (`LOG_FORCE_COLOR=maybe`, `""` and `2` all kept the kwarg)                                                          |
| Level                    | `LOG_CONSOLE_LEVEL`, `LOG_BACKEND_LEVEL`, `LOG_GRAYLOG_LEVEL`                                                              | level name, any case                                                                                 | Refused, naming the variable                                                                                                                        |
| Ring size                | `LOG_RING_BUFFER_SIZE`                                                                                                     | integer > 0                                                                                          | Refused: `LOG_RING_BUFFER_SIZE must be an integer` / `must be positive`                                                                             |
| Endpoint                 | `LOG_GRAYLOG_ENDPOINT`                                                                                                     | `HOST:PORT`, split at the first colon (IPv6 literals are not supported), port > 0; empty means unset | Refused: `must be HOST:PORT`, `port must be an integer`, `port must be positive`                                                                    |
| Rate limit               | `LOG_RATE_LIMIT`                                                                                                           | `MAX:WINDOW_SECONDS`, e.g. `500:60`, both > 0; empty means unset                                     | Refused: `must be MAX:WINDOW_SECONDS`, `... with numeric values`, `values must be positive`                                                         |
| Protocol, stream, preset | `LOG_GRAYLOG_PROTOCOL`, `LOG_CONSOLE_STREAM`, `LOG_CONSOLE_FORMAT_PRESET`                                                  | enum or preset name                                                                                  | Refused                                                                                                                                             |
| Queue                    | `LOG_QUEUE_MAXSIZE`, `LOG_QUEUE_FULL_POLICY`, `LOG_QUEUE_PUT_TIMEOUT`, `LOG_QUEUE_STOP_TIMEOUT`                            | int > 0, `block`/`drop`, float                                                                       | Silently ignored: the config value stays. `LOG_QUEUE_MAXSIZE=0` also keeps the config value; a timeout of `0` or negative means no timeout (`None`) |
| Key lists                | `LOG_CONSOLE_STYLES`, `LOG_SCRUB_PATTERNS`                                                                                 | `key=value,key=value`; entries without `=` or with an empty key are skipped                          | Skipped per entry; a malformed regex is refused later by the compile check                                                                          |

Observed refusals (each prefixed `Invalid runtime settings: `):

```
LOG_CONSOLE_LEVEL: Unknown log level: 'loud'
LOG_RING_BUFFER_SIZE must be an integer
LOG_GRAYLOG_ENDPOINT must be HOST:PORT
LOG_RATE_LIMIT must be MAX:WINDOW_SECONDS
```

`LOG_QUEUE_MAXSIZE=abc`, `LOG_QUEUE_FULL_POLICY=zzz`, `LOG_QUEUE_PUT_TIMEOUT=abc` and `LOG_ENABLE_GRAYLOG=maybe` all pass
validation silently and use the configured values, so a typo in those variables is invisible.

`LOG_CONSOLE_STYLES` keys are upper-cased; `LOG_CONSOLE_STYLES="info=green, WARNING=bold yellow"` yields
`{'INFO': 'green', 'WARNING': 'bold yellow'}`. Env styles beat explicit `console_styles` (example: `INFO=blue` over `{"INFO": "green"}`), which beat theme defaults.

## Levels

Accepted anywhere a level is configured (`console_level`, `backend_level`, `graylog_level` and the matching env vars):

| Form                               | Example                 | Result             |
|------------------------------------|-------------------------|--------------------|
| Name, any case, whitespace trimmed | `"DeBuG"`, `" error "`  | `DEBUG`, `ERROR`   |
| `LogLevel` member                  | `LogLevel.CRITICAL`     | `CRITICAL`         |
| stdlib integer                     | `logging.WARNING`, `40` | `WARNING`, `ERROR` |

Members and values: `DEBUG` 10, `INFO` 20, `WARNING` 30, `ERROR` 40, `CRITICAL` 50. Any other name (`fatal`, `loud`) or integer
(`5`) is refused. A `bool` or `float` fails at `RuntimeConfig(...)` construction as a Pydantic `ValidationError`.

The three thresholds are independent: console, backend (journald and Event Log), and Graylog each filter on their own level.
Defaults are `INFO`, `WARNING`, `WARNING`.

## .env loading

Nothing reads `.env` unless the host calls the helper before `init()`:

```python
import lib_log_rich.config as log_config

loaded = log_config.enable_dotenv()  # Path to the .env, or None
log_config.enable_dotenv(search_from="/path/to/app", markers=("pyproject.toml", ".git"))
log_config.load_dotenv("/path/to/app", override=True)  # alias; `override` here, `dotenv_override` on enable_dotenv
```

- Search starts at `search_from` (default cwd; a file path uses its parent; a missing directory raises `FileNotFoundError`)
  and walks up. It stops after the first directory that holds a marker file (`pyproject.toml` or `.git` by default),
  and that directory is still searched. A `.env` above the marker directory is never loaded (the lookup returns `None`).
- Existing environment variables win; `override=True` / `dotenv_override=True` lets the file win.
- The result is cached per process and per override flag: a second call with a different `search_from` returns the first path
  and loads nothing new.
- `LOG_USE_DOTENV` (`1/true/yes/on`) is read only by the CLI and `python -m lib_log_rich`, never by `init()`. Setting it in a
  library process does nothing. Helpers: `should_use_dotenv(explicit=None, env_value=None)` gives an explicit flag priority over the
  env text (`should_use_dotenv(explicit=False, env_value="1")` is `False`).
- Value shapes in a `.env` are the same as in the process environment.

## TOML and empty values

`RuntimeConfig` normalises config-file artefacts so a TOML loader can pass them straight through:

- `console_format_template` and `dump_format_template`: empty or whitespace-only string becomes `None`.
- `graylog_endpoint` and `rate_limit`: empty list or tuple becomes `None`; a list of two (`[5, 10]`) becomes `(5, 10.0)`.

Example: `RuntimeConfig(rate_limit=[], graylog_endpoint=[], console_format_template="  ", dump_format_template="")`
resolves all four to `None`. A string `graylog_endpoint="host:port"` is not accepted in the config (Pydantic `tuple_type` error);
only the env var takes `HOST:PORT`.

## validate_config (lib_log_rich >= 6.4.0)

`log.validate_config(config)` runs the same resolution as `init()`, with env overrides applied, and raises the same
`ValueError`. It returns `None`, builds no adapters, and neither reads nor touches a running runtime: it works while another
runtime is live, and `init()` does not need to be called first.

```python
log.validate_config(RuntimeConfig(service="svc", environment="dev", console_level="loud"))
# ValueError: Invalid runtime settings: console_level: Unknown log level: 'loud'
```

Message format: `Invalid runtime settings: <field or LOG_ variable>: <reason>`. Several model errors join with `; `:
`... service: service must not be empty; environment: environment must not be empty; rate_limit: rate_limit[1] must be positive`.

| Refused                                                                                         | Example message                                                                                    |
|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| Unknown level in config or `LOG_*_LEVEL`                                                        | `console_level: Unknown log level: 'loud'` (the first bad level stops resolution)                  |
| Scrub pattern that does not compile (config or `LOG_SCRUB_PATTERNS`)                            | `scrub_patterns: Invalid scrub pattern for 'pw': missing ), unterminated subpattern at position 0` |
| Unknown console preset (built-in console only)                                                  | `Unknown console format preset: 'nope'`                                                            |
| Console style key that is not a level (built-in console only)                                   | `Console style key is not a log level: 'FOO'`                                                      |
| Blank service or environment, `ring_buffer_size` or `queue_maxsize` <= 0, rate limit parts <= 0 | `ring_buffer_size must be positive`, `queue_maxsize: queue_maxsize must be positive`               |
| Bad protocol, stream, queue policy                                                              | `Invalid Graylog protocol: 'icmp'; must be 'tcp' or 'udp'`                                         |
| `console_stream="custom"` without a target                                                      | `console_stream_target must be provided when console stream is 'custom'`                           |
| Graylog endpoint port <= 0                                                                      | `endpoint: Graylog endpoint port must be positive`                                                 |
| Graylog enabled without an endpoint (lib_log_rich >= 6.5.1)                                     | `Graylog is enabled but no endpoint is set (graylog_endpoint or LOG_GRAYLOG_ENDPOINT)`             |

With a custom `console_adapter_factory` the preset and style-key checks are skipped (the factory defines its own vocabulary);
the same bad config returns `None` there. Model errors are collected before the preset and style checks run, so a scrub
error is reported first when both are present.

TLS over UDP is refused (lib_log_rich >= 6.4.2) whenever Graylog is enabled with an endpoint:
`Invalid runtime settings: TLS is only supported for TCP Graylog transport`. Unknown keys in `payload_limits` and an
unknown `console_theme` are ignored. Before 6.5.1, Graylog enabled without an endpoint was accepted and built no sink. `init()` called while a runtime is live raises `RuntimeError` before validating.

## Scrubbing

Defaults: keys `password`, `secret`, `token`, each with pattern `.+` (everything). Custom `scrub_patterns` and
`LOG_SCRUB_PATTERNS` merge over them; the env wins on a clash.

How a rule applies (as seen with `init()` and `dump(dump_format="json")`):

| Behaviour           | Result                                                                                                                                              |
|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| Key match           | Exact name, case-insensitive, whitespace-trimmed: `PASSWORD`, `Api_Key` match; `password2` and `tok` do not (no substring match)                    |
| Where               | Top-level keys of `extra` and of `LogContext.extra` (bound with `log.bind(extra={...})`)                                                            |
| Not scrubbed        | The log message text (`"hello password=abc"` is unchanged) and `LogContext` named fields                                                            |
| Value test          | `re.search` on string values (bytes are decoded); a match replaces the whole value with `***`; no match leaves it                                   |
| Non-strings         | `int`, `None` and similar are left unchanged (`"token": 12345` survives)                                                                            |
| Under a matched key | Mappings, lists, tuples and sets are walked and each string leaf is tested: `"secret": {"a": "v", "b": ["q"]}` becomes `{'a': '***', 'b': ['***']}` |
| Nested keys         | A key named `password` nested under an unmatched key is NOT scrubbed: `"data": {"password": "deep"}` stays `deep`                                   |
| Same key twice      | Keys normalise by case, so the later (custom or env) pattern replaces the default for that name                                                     |

```python
log.init(RuntimeConfig(service="svc", environment="dev", queue_enabled=False, console_stream="none", scrub_patterns={"api_key": r"^sk-", "Card": r"\d{4}"}))
log.getLogger("t").info("m", extra={"Api_Key": "sk-123", "api_key2": "sk-1", "card": "1234", "n": 5})
# dump extra: {'Api_Key': '***', 'api_key2': 'sk-1', 'card': '***', 'n': 5}
```

`LOG_SCRUB_PATTERNS` syntax: `field=regex` pairs separated by commas; whitespace around key and value is trimmed; an empty
regex becomes `.+`; entries with no `=` or an empty key are dropped. Example:
`api_key=^sk-, session= ,bad,=x,pin=\d+` produced rules for `api_key` (`^sk-`), `session` (`.+`) and `pin` (`\d+`) on top of the
defaults. A comma inside a regex splits the entry; avoid `{1,3}` style quantifiers in the env form and use `scrub_patterns=`
in code for those.

To scrub more than listed top-level keys, add a rule per key name; there is no recursive key-name search and no message scrubbing.

## Payload limits

`payload_limits` takes a `PayloadLimits` or a mapping of its field names (`lib_log_rich.runtime.settings.models.PayloadLimits`).
Unknown mapping keys are ignored silently. There is no env override.

| Field                     | Default | Effect when exceeded                                                                                                                                         |
|---------------------------|---------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `truncate_message`        | `True`  | `True`: cut the message and end it with a `[truncated]` marker. `False`: the log call raises `ValueError: log message length 30 exceeds configured limit 10` |
| `message_max_chars`       | `4096`  | Message length cap (> 0)                                                                                                                                     |
| `extra_max_keys`          | `25`    | Keys beyond the cap are dropped (first keys are kept)                                                                                                        |
| `extra_max_value_chars`   | `512`   | String values are truncated                                                                                                                                  |
| `extra_max_depth`         | `3`     | Deeper nesting is truncated (also used for context)                                                                                                          |
| `extra_max_total_bytes`   | `8192`  | Encoded size cap for all extras; `None` disables it                                                                                                          |
| `context_max_keys`        | `20`    | Context extra keys cap                                                                                                                                       |
| `context_max_value_chars` | `256`   | Context value length cap                                                                                                                                     |
| `stacktrace_max_frames`   | `10`    | Frames kept in exception traces                                                                                                                              |

All integer limits must be > 0; `extra_max_total_bytes` must be > 0 or `None`. Example with
`{"message_max_chars": 10, "extra_max_keys": 2, "extra_max_value_chars": 5}`: a 30-character message became a 10-character
truncated string, `extra={"a": "y"*20, "b": 1, "c": 2}` became `{'a': '<5 chars>', 'b': 1}`. Trimming emits diagnostics
(`message_truncated`, `extra_value_truncated`, ...) to `diagnostic_hook`.

## Rate limiting

`rate_limit=(max_events, window_seconds)` is a sliding window per (logger name, level) pair, applied before fan-out; events over
quota are dropped. `LOG_RATE_LIMIT=MAX:WINDOW_SECONDS` overrides it. Example: 10 `info` calls on one logger with
`(3, 60.0)` kept 3 events; with `LOG_RATE_LIMIT=5:60` set as well, 5 events (env wins). `None` disables limiting.

## Ring buffer

`ring_buffer_size` (default 25000, `LOG_RING_BUFFER_SIZE`) bounds what `dump()` can return. Example: 10 events with size 4
left 4 in the dump; with `LOG_RING_BUFFER_SIZE=6`, 6. `enable_ring_buffer=False` (or `LOG_RING_BUFFER_ENABLED=0`) swaps in a
1024-event buffer rather than none.

## Platform guards

`enable_journald=True` on Windows and `enable_eventlog=True` on non-Windows are silently turned off, not refused.
