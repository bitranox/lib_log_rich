# Feature Documentation: lib_log_rich CLI

## Status
Complete

## Links & References
**Feature Requirements:** None (ad-hoc scaffold requirement)
**Task/Ticket:** None documented
**Related Files:**
- src/lib_log_rich/cli.py
- src/lib_log_rich/lib_log_rich.py
- src/lib_log_rich/__main__.py
- src/lib_log_rich/__init__conf__.py
- src/lib_log_rich/__init__.py
- tests/test_basic.py

## Problem Statement
Provide a minimal but functional command-line interface (CLI) scaffold for the `lib_log_rich` package so developers can exercise the package, preview logging features, and validate the packaging scripts without additional wiring.

## Solution Overview
A Click-powered CLI entry point exposes three subcommands (`info`, `hello`, `fail`) behind a root group and now executes a default pathway when no subcommand is supplied. Global options manage traceback verbosity via the shared `lib_cli_exit_tools` helper. The CLI wraps the library's stub functions, making it easy to test colored output, failure handling, and project metadata printing while reusing the shared exit tooling. Traceback preferences are scoped to each invocation, with the entrypoint restoring the prior setting once execution completes.

## Architecture Integration
**Where this fits in the overall app:**
Runs in the outer adapters layer as the package's main transport. It orchestrates user input into application/library functions (`hello_world`, `i_should_fail`, configuration printers) and routes exit behavior through `lib_cli_exit_tools`.

**Data flow:**
User invokes `lib_log_rich` CLI → Click parses args & stores global flags → Root command configures `lib_cli_exit_tools` → If no subcommand is provided, `cli_main()` triggers the domain placeholder `main`; otherwise the chosen subcommand executes the appropriate helper (`hello_world`, `i_should_fail`, or `__init__conf__.print_info`) → Results printed to stdout/stderr → `lib_cli_exit_tools.run_cli` returns exit code to caller.

## Core Components

### CLICK_CONTEXT_SETTINGS
**Purpose:** Shared Click context configuration ensuring `-h/--help` parity across all commands. Prevents divergence between commands as the CLI grows.  
**Value:** `{"help_option_names": ["-h", "--help"]}` — matches developer expectations from other tooling.  
**Location:** src/lib_log_rich/cli.py

### cli()
**Purpose:** Root Click group managing global options, shared context (traceback flag), and fallback delegation when no subcommand is provided.  
**Input:** Parsed CLI options (`--traceback`) and Click context.  
**Output:** Configured context dict with traceback flag; side-effect of syncing `lib_cli_exit_tools.config.traceback`.  
**Location:** src/lib_log_rich/cli.py

### cli_main()
**Purpose:** Default action for bare invocations; proxies to the domain's placeholder `main`.  
**Input:** None.  
**Output:** None (calls the domain helper, which returns `None`).  
**Location:** src/lib_log_rich/cli.py

### cli_info()
**Purpose:** Display project metadata via `__init__conf__.print_info()`.
**Input:** None (uses global config set by `cli`).
**Output:** Formatted metadata printed to stdout.
**Location:** src/lib_log_rich/cli.py

### cli_hello()
**Purpose:** Demonstrate successful command path by invoking `hello_world()`.
**Input:** None.
**Output:** `Hello World` message to stdout.
**Location:** src/lib_log_rich/cli.py

### cli_fail()
**Purpose:** Exercise error handling by calling `i_should_fail()` which raises `RuntimeError`.  
**Input:** None.  
**Output:** Raises exception captured by Click/`lib_cli_exit_tools`; produces traceback when `--traceback` is active.  
**Location:** src/lib_log_rich/cli.py

### main()
**Purpose:** Process wrapper that delegates to `lib_cli_exit_tools.run_cli` for consistent exit codes and signal handling while optionally restoring the prior traceback flag.
**Input:** Optional argv sequence plus keyword `restore_traceback` (default `True`), program name from `__init__conf__`.
**Output:** Integer exit code for the process; restores the pre-call traceback flag when requested.
**Location:** src/lib_log_rich/cli.py

### hello_world()
**Purpose:** Library helper returning the canonical greeting message for CLI reuse.
**Input:** None.
**Output:** Writes `Hello World` to stdout.
**Location:** src/lib_log_rich/lib_log_rich.py

### i_should_fail()
**Purpose:** Intentional failure hook to validate error paths and traceback emission.
**Input:** None.
**Output:** Raises `RuntimeError("I should fail")`.
**Location:** src/lib_log_rich/lib_log_rich.py

## Implementation Details
**Dependencies:**
- External: `click` for CLI parsing, `lib_cli_exit_tools` for standardized exit handling.
- Internal: `lib_log_rich.lib_log_rich` helpers, project metadata in `__init__conf__`.

**Key Configuration:**
- Global `--traceback/--no-traceback` flag toggles full traceback printing through `lib_cli_exit_tools.config.traceback`. When no subcommand is supplied the root handler calls `cli_main()`, which in turn executes the domain placeholder `main()` so default behavior remains centralized. The CLI restores the prior configuration on completion, while the module `__main__` entry point defers restoration until after exception reporting.
- Program metadata (`shell_command`, `version`, `title`) loaded from `__init__conf__`.

**Database Changes:**
None.

## Testing Approach
**How to test this feature:**
- Run `pytest tests/test_basic.py::test_cli_hello_and_fail_commands` for command behavior.
- Use `pytest tests/test_basic.py::test_module_main_traceback` to confirm traceback toggling.
- Manual smoke test: `python -m lib_log_rich --traceback fail` and `lib_log_rich hello` after editable install.

**Automated tests to write:**
Existing tests cover greeting output, traceback propagation, and `main` delegation.

**Edge cases to verify:**
- Invoking `fail` without `--traceback` should still exit non-zero with truncated stack per `lib_cli_exit_tools` defaults.
- Ensure `--traceback` flag persists for nested commands when new subcommands are added.
- Confirm metadata printing remains consistent after version bumps.

**Test data needed:**
No external data; CLI tests rely on Click's `CliRunner`.

## Known Issues & Future Improvements
**Current limitations:**
- Only exposes stub functionality.
- Traceback restoration depends on callers that disable `restore_traceback` resetting the flag when they finish (the default handles this automatically).

**Edge cases to handle:**
- Future commands should respect the traceback flag and return structured errors.
- Need graceful handling for unexpected exceptions once richer features exist.

**Planned improvements:**
- Replace `print`-based helpers with structured logging adapters when available.
- Introduce configuration-driven command registration.

## Risks & Considerations
**Technical risks:**
- Callers that opt out of automatic traceback restoration (`restore_traceback=False`) must ensure they reset the flag after error handling.
- Adding dependencies without wrapping them as adapters could violate Clean Architecture boundaries.

**User impact:**
- Failing command intentionally exits non-zero; document clearly to avoid confusion.
- Traceback defaults to hidden; users may need to pass `--traceback` to debug issues.

## Documentation & Resources
**Related documentation:**
- README.md (usage overview)
- CONTRIBUTING.md (development workflow)

**External references:**
- Click documentation for command extensions
- lib_cli_exit_tools project docs (shared tooling)

---
**Created:** 2025-09-17 by GPT-5 Codex  
**Last Updated:** 2025-09-23 by GPT-5 Codex  
**Review Date:** 2025-12-17

---

# Feature Documentation: Greeting & Failure Helpers Module

## Status
Complete

## Links & References
**Feature Requirements:** None (scaffold placeholder)
**Task/Ticket:** None documented
**Related Files:**
- src/lib_log_rich/lib_log_rich.py
- tests/test_basic.py

## Problem Statement
Provide deterministic success and failure paths so transports and documentation can validate stdout and error handling while the Rich logging helpers are under development.

## Solution Overview
Two small functions live in the `lib_log_rich` module. `hello_world()` emits the canonical greeting. `i_should_fail()` raises `RuntimeError`. Both are intentionally simple and side-effect free beyond their remit so they stay reusable across adapters.

## Architecture Integration
**Where this fits in the overall app:**
Domain layer placeholder invoked by CLI commands and tests.

**Data flow:**
`cli_hello()` → `hello_world()` writes to stdout. `cli_fail()` → `i_should_fail()` raises → CLI prints error via `lib_cli_exit_tools` helpers.

## Core Components

### hello_world()
**Purpose:** Emit the canonical greeting for documentation and smoke tests.  
**Input:** None.  
**Output:** Writes `"Hello World"` to stdout.  
**Location:** src/lib_log_rich/lib_log_rich.py

### i_should_fail()
**Purpose:** Guarantee a repeatable failure path to test error propagation.  
**Input:** None.  
**Output:** Raises `RuntimeError("I should fail")`.  
**Location:** src/lib_log_rich/lib_log_rich.py

### main()
**Purpose:** Placeholder orchestration hook that keeps a stable seam for future domain entry wiring while staying side-effect free today.  
**Input:** None.  
**Output:** Returns `None` and performs no work.  
**Location:** src/lib_log_rich/lib_log_rich.py

## Implementation Details
**Dependencies:** None (pure Python for easy reuse and testing).  
**Key Configuration:** Direct execution is prevented by an `if __name__ == "__main__"` guard, and a no-op `main()` preserves a sanctioned extension seam without enabling ad-hoc execution.  
**Database Changes:** None.

## Testing Approach
**How to test this feature:** `pytest tests/test_basic.py::test_hello_world_prints_greeting` and `pytest tests/test_basic.py::test_cli_hello_and_fail_commands`.  
**Automated tests to write:** Covered by existing tests.  
**Edge cases to verify:** Ensure greetings remain identical when extracted via package import; confirm `RuntimeError` message remains stable for documentation and CLI assertions.  
**Test data needed:** None.

## Known Issues & Future Improvements
**Current limitations:** Placeholder logic until real logging helpers arrive.  
**Edge cases to handle:** None; functions are deterministic.  
**Planned improvements:** Replace with structured logging functions once implemented.

## Risks & Considerations
**Technical risks:** Minimal; only risk is diverging messages during refactors.  
**User impact:** Visible output used in docs—keep the string stable.  

## Documentation & Resources
**Related documentation:** README usage examples referencing the greeting.  
**External references:** None.

---
**Created:** 2025-09-18 by GPT-5 Codex  
**Last Updated:** 2025-09-23 by GPT-5 Codex  
**Review Date:** 2025-12-18

---

# Feature Documentation: Metadata Facade (`__init__conf__`)

## Status
Complete

## Links & References
**Feature Requirements:** Packaging metadata availability for CLI output.  
**Task/Ticket:** None documented.  
**Related Files:**
- src/lib_log_rich/__init__conf__.py
- docs/systemdesign/module_reference.md (this document)
- tests/test_basic.py (indirect usage via CLI info tests)

## Problem Statement
Expose authoritative metadata (name, version, homepage, author) at runtime without duplicating values from `pyproject.toml` so CLI commands and documentation stay in sync with the built package.

## Solution Overview
Wrapper functions around `importlib.metadata` gather distribution fields, provide fallbacks for working-tree execution, and surface module-level constants. `print_info()` renders the values in a consistent format consumed by the CLI `info` command.

## Architecture Integration
**Where this fits in the overall app:**
Lives in the outer platform layer; imported by CLI adapters and other tooling. Domain logic does not depend on it.

**Data flow:**
`cli_info()` calls `print_info()` → helper resolves constants via `_meta/_version/_home_page/_author/_summary/_shell_command` → output printed to stdout.

## Core Components

### _get_str()
Pulls string metadata values from mapping-like objects while enforcing string types. Used by all other helpers.  
**Location:** src/lib_log_rich/__init__conf__.py

### _meta(dist_name)
Loads distribution metadata via `importlib.metadata.metadata`. Returns `None` when the package is absent to avoid crashes during local development.  
**Location:** src/lib_log_rich/__init__conf__.py

### _version(dist_name)
Fetches the installed version or falls back to `0.0.0.dev0`.  
**Location:** src/lib_log_rich/__init__conf__.py

### _home_page(metadata)
Prefers `Home-page`/`Homepage` metadata but defaults to the GitHub repository link.  
**Location:** src/lib_log_rich/__init__conf__.py

### _author(metadata)
Returns the `(author, author_email)` tuple, defaulting to Bitranox placeholders.  
**Location:** src/lib_log_rich/__init__conf__.py

### _summary(metadata)
Supplies the CLI/group help text summary with sensible defaults.  
**Location:** src/lib_log_rich/__init__conf__.py

### _shell_command(entry_points)
Discovers the published console-script name, falling back to the distribution name when no entry point is registered.  
**Location:** src/lib_log_rich/__init__conf__.py

### print_info()
Formats the metadata block and writes it to stdout.  
**Location:** src/lib_log_rich/__init__conf__.py

## Implementation Details
**Dependencies:** :mod:`importlib.metadata` only (stdlib).  
**Key Configuration:** Defaults mirror `pyproject.toml` values and GitHub URL.  
**Database Changes:** None.

## Testing Approach
**How to test this feature:** Exercised indirectly via CLI tests; doctests cover helper fallbacks.  
**Automated tests to write:** Full property tests once metadata fields grow.  
**Edge cases to verify:** Working-tree runs without editable install; projects with custom console-script names.  
**Test data needed:** None.

## Known Issues & Future Improvements
**Current limitations:** No caching invalidation when metadata changes mid-run (acceptable for CLI lifecycle).  
**Edge cases to handle:** Multi-entry-point distributions picking the wrong name—add filtering if needed.  
**Planned improvements:** Extend metadata block when new fields become relevant (e.g., license).

## Risks & Considerations
**Technical risks:** Divergence between defaults and actual metadata if `pyproject.toml` values change without updating fallbacks.  
**User impact:** CLI info output must remain accurate; integrate regression checks when release automation is introduced.

## Documentation & Resources
**Related documentation:** README, packaging docs referencing CLI metadata.  
**External references:** Python `importlib.metadata` documentation.

---
**Created:** 2025-09-18 by GPT-5 Codex  
**Last Updated:** 2025-09-23 by GPT-5 Codex  
**Review Date:** 2025-12-18

---

# Feature Documentation: Module Entry Bridge (`__main__`)

## Status
Complete

## Links & References
**Feature Requirements:** Provide `python -m lib_log_rich` parity with console script.  
**Task/Ticket:** None documented.  
**Related Files:**
- src/lib_log_rich/__main__.py
- src/lib_log_rich/cli.py
- tests/test_basic.py

## Problem Statement
Guarantee that invoking the package as a Python module mirrors the console script behavior, including traceback toggling and exit-code normalization.

## Solution Overview
`_module_main()` captures the console-script execution flow: it calls `cli.main(restore_traceback=False)`, delegates error printing and exit-code mapping to `lib_cli_exit_tools`, and restores the prior traceback preference.

## Architecture Integration
**Where this fits in the overall app:**
Adapter layer bridging CPython module execution to the CLI helper.

**Data flow:**
`python -m lib_log_rich` → `_module_main()` → `cli.main()` → success or `lib_cli_exit_tools` exception handling → exit code.

## Core Components

### _TRUNCATED_TRACEBACK_LIMIT / _FULL_TRACEBACK_LIMIT
Constants controlling error message length (500 chars truncated vs. 10,000 expanded) aligned with `lib_cli_exit_tools` defaults.  
**Location:** src/lib_log_rich/__main__.py

### _module_main()
Executes the CLI, prints exceptions via shared tooling, restores traceback preference, and returns the exit code.  
**Location:** src/lib_log_rich/__main__.py

## Implementation Details
**Dependencies:** `lib_cli_exit_tools` and internal `cli` module.  
**Key Configuration:** Mirrors shared traceback limits defined by the tooling.  
**Database Changes:** None.

## Testing Approach
**How to test this feature:** `pytest tests/test_basic.py::{test_module_main,test_module_main_failure,test_module_main_traceback}`.  
**Automated tests to write:** Covered by existing suite.  
**Edge cases to verify:** Behavior when custom exceptions surface; ensuring prior traceback preference restored after failure.  
**Test data needed:** None.

## Known Issues & Future Improvements
**Current limitations:** Always relies on global config; future DI might pass explicit settings.  
**Edge cases to handle:** Non-CLI callers setting `restore_traceback=False` must manage restoration.  
**Planned improvements:** Introduce structured logging for exception outputs once logging helpers exist.

## Risks & Considerations
**Technical risks:** Divergence from console script if shared tooling defaults change without updating constants.  
**User impact:** Incorrect exit codes would confuse shell automation; maintain parity with CLI helper.

## Documentation & Resources
**Related documentation:** README CLI usage section.  
**External references:** `lib_cli_exit_tools` documentation.

---
**Created:** 2025-09-18 by GPT-5 Codex  
**Last Updated:** 2025-09-23 by GPT-5 Codex  
**Review Date:** 2025-12-18
