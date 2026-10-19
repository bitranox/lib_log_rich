# `.env` Opt-In Loading

`lib_log_rich` already honours environment variables over function arguments (for example, setting `LOG_SERVICE` overrides the `service=` parameter passed to `init()`). Opt-in `.env` loading lets you keep configuration alongside the project while preserving that precedence order:

> CLI flag → real environment (`os.environ`) → discovered `.env` → hard-coded defaults.

## When to load `.env`

`.env` loading is **explicit**. The library never touches the filesystem unless you ask it to, so existing deployments are unaffected.

| Surface                     | How to enable                                                                                                           |
|-----------------------------|-------------------------------------------------------------------------------------------------------------------------|
| Library / apps              | Call `lib_log_rich.config.enable_dotenv()` (or `load_dotenv()`) before `init()`.                                        |
| `python -m lib_log_rich`    | Pass `--use-dotenv` (or export `LOG_USE_DOTENV=1`) so the module entry point loads `.env` before bootstrapping the CLI. |
| `scripts/run_cli.py` helper | Same semantics: `--use-dotenv/--no-use-dotenv` flag or `LOG_USE_DOTENV` environment toggle.                             |

> Value shapes: the [Runtime configuration](README.md#runtime-configuration) section documents each `LOG_*` variable, including accepted console themes (`classic`, `dark`, `neon`, `pastel`), format presets (`full`, `short`, `full_loc`, `short_loc`, `short_loc_icon`), template placeholders, rate-limit syntax (`MAX:WINDOW_SECONDS`), and scrub-pattern formats. `.env` loading accepts the same shapes.

## Search strategy

`enable_dotenv()` discovers configuration like this:

1. Pick a starting directory (`search_from` argument or `Path.cwd()`).
2. Walk up through parents towards `/`, but stop at the first directory that holds a project
   marker (`markers`, default `("pyproject.toml", ".git")`). The marker directory itself is still
   searched; nothing above it is. Pass `markers=()` to walk all the way to `/`.
3. The first directory in that range containing a `.env` file ends the search. So does a directory
   whose entries cannot be looked up (no execute permission, like `/root` for a service account),
   with no file found: the search never walks past a directory whose markers it could not see.
   A cwd the OS cannot name at all (removed after the chdir, or unreadable on macOS) gives the
   same result when `search_from` is omitted: there is no directory to start from, so nothing
   is loaded. An explicit `search_from` that does not exist still raises `FileNotFoundError`.
4. Read the file as UTF-8 and hand it to `python-dotenv` with `override=False`.

The search never changes the working directory, so other threads are unaffected and a cwd the
process cannot enter does not matter when `search_from` points elsewhere.

A `.env` that is not valid UTF-8 raises `lib_log_rich.config.DotenvDecodeError` (a `ValueError`)
whose message names the file and the byte offset of the first bad byte, never the content; nothing
from that file is loaded. A `.env` that exists but cannot be read raises its `OSError`
(`PermissionError`), which names the path.

So a `.env` above your project root is never picked up while the project has a `pyproject.toml`
or `.git`.

Because `override` defaults to `False`, existing `os.environ` values always win. For the edge case where you *want* `.env` values to override real environment variables, pass `dotenv_override=True`.

The helper caches the result per process, including a "not found" result. A later call with
the same `dotenv_override` returns the cached path and ignores its `search_from`: calling
`enable_dotenv(search_from=other_dir)` after a first call does not search `other_dir`. Only a
call with a different `dotenv_override` value searches again.

## Example - library usage

```python
import lib_log_rich as log
import lib_log_rich.config as log_config

# Walk upwards from the current working directory until we find .env
log_config.enable_dotenv()

config = log.RuntimeConfig(service="svc", environment="dev", queue_enabled=False)
log.init(config)
...
log.shutdown()
```

If you would rather search from a specific location or stop when specific markers are found (for example, `pyproject.toml` or `.git`), pass arguments explicitly:

```python
from pathlib import Path

log_config.enable_dotenv(
    search_from=Path(__file__).parent,
    markers=("pyproject.toml", ".git"),
)
```

## Example - CLI

```bash
# Equivalent triggers: flag or environment toggle
lib_log_rich --use-dotenv info
LOG_USE_DOTENV=1 lib_log_rich info

# Inspect what was loaded
lib_log_rich --use-dotenv logdemo --dump-format json
```

The CLI also propagates the flag when you launch it through `scripts/run_cli.py`:

```bash
python scripts/run_cli.py --use-dotenv logdemo --dump-format text
```

## Why we ship `python-dotenv`

`python-dotenv` is a small, installation-only dependency. Including it in the core dependency set avoids surprising `ImportError`s the first time someone opts into `.env` support.

## Testing notes

Automated coverage ensures the precedence rules work as expected:

- Doctest verifies `.env` values populate `os.environ` and are cleaned up afterwards.
- `tests/test_config_dotenv.py` checks library behaviour, CLI precedence (`--use-dotenv` beat `LOG_USE_DOTENV`, beats defaults), and that existing environment variables win over `.env`.

With those guardrails in place, `.env` support remains opt-in plumbing layered on top of the established environment-first configuration flow.
