# Customising Console Colours

`lib_log_rich.init(RuntimeConfig(...))` accepts a `console_styles` mapping (and the `LOG_CONSOLE_STYLES` environment variable) to override the Rich style used for each log level. This document explains the input format and available values.

## Quick reference

```python
import lib_log_rich as log

config = log.RuntimeConfig(
    service="svc",
    environment="prod",
    console_styles={
        "DEBUG": "dim",
        "INFO": "bright_green",
        "WARNING": "bold yellow",
        "ERROR": "bold white on red",
        "CRITICAL": "#FF00FF",
    },
)
log.init(config)
```

Environment alternative:

```
export LOG_CONSOLE_STYLES="DEBUG=dim,INFO=bright_green,WARNING=bold yellow,ERROR=bold white on red,CRITICAL=#FF00FF"
```

## Built-in themes

`lib_log_rich` ships with four ready-to-use palettes referenced by `logdemo(theme=...)` and the CLI command `lib_log_rich logdemo`:

| Theme   | DEBUG            | INFO             | WARNING          | ERROR            | CRITICAL                  |
|---------|------------------|------------------|------------------|------------------|---------------------------|
| classic | `dim`            | `cyan`           | `yellow`         | `red`            | `bold red`                |
| dark    | `grey42`         | `bright_white`   | `bold gold3`     | `bold red3`      | `bold white on red3`      |
| neon    | `#00ffd5`        | `#39ff14`        | `#fff700`        | `#ff073a`        | `bold #ff00ff on black`   |
| pastel  | `aquamarine1`    | `light_sky_blue1`| `khaki1`         | `light_salmon1`  | `bold plum1`              |

These values are Rich style strings and can be overridden by providing your own mapping or `LOG_CONSOLE_STYLES` value. Feel free to duplicate a theme and tweak the colours to match your brand or terminal palette.

## Accepted keys

- Case-insensitive level names: `debug`, `info`, `warning`, `error`, `critical`.
- A `LogLevel` enum member when calling `init(...)` from code (e.g. `LogLevel.INFO`).
- `LogLevel.code` exposes four-character abbreviations (`DEBG`, `INFO`, `WARN`, `ERRO`, `CRIT`) you can reuse in custom formatter strings or table columns when you need fixed-width level tokens.

Missing keys fall back to the built-in defaults:

| Level     | Default style |
|-----------|---------------|
| DEBUG     | `dim`
| INFO      | `cyan`
| WARNING   | `yellow`
| ERROR     | `red`
| CRITICAL  | `bold red`

## Style values

You can pass any Rich style string. Rich supports:

### Named colours

`red`, `green`, `cyan`, `magenta`, `yellow`, `blue`, etc. (full list at [Rich colour reference](https://rich.readthedocs.io/en/stable/style.html#color)).

### Modifiers

Combine modifiers with colours:

- `bold red`
- `dim cyan`
- `italic bright_green`
- `underline on black`

### Hex colours

For 24-bit terminals, use hex codes:

- `#44ff99`
- `#ff0055`

Rich downgrades these gracefully when the terminal only supports 256 or 16 colours.

### Background colours

Prefix with `on`:

- `white on red`
- `bold black on #ffaa00`

### Predefined Rich styles

Rich ships with convenience styles such as `bright_red`, `dark_orange3`, etc.

See the [Rich style guide](https://rich.readthedocs.io/en/stable/style.html) for the full grammar.

## Environment variable format

`LOG_CONSOLE_STYLES` is parsed as a comma-separated list of `LEVEL=style` pairs. Spaces around the equals sign are ignored. Example:

```
LOG_CONSOLE_STYLES="INFO=bright_green, ERROR=bold white on red"
```

Invalid pairs (missing `=` or empty keys/values) are ignored. Level names are normalised to uppercase.

## Interaction with `force_color` / `no_color`

- `no_color=True` (or `LOG_NO_COLOR=1`) disables colouring entirely, regardless of `console_styles`.
- `force_color=True` (or `LOG_FORCE_COLOR=1`) forces Rich to emit colour even when `stderr` isn’t a TTY.

## Combining code and environment

When both `console_styles` and `LOG_CONSOLE_STYLES` are provided, the environment values win. This makes it easy to keep defaults in code but override them per deployment.

## Validation

The library does not currently validate style strings beyond trimming and storing them—invalid values simply render as plain text. If you need stricter validation, consider wrapping Rich’s `Theme` API or using Rich’s style syntax checker in your configuration pipeline.
