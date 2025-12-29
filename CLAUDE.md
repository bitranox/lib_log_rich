# Claude Code Guidelines for lib_log_rich

## Session Initialization

When starting a new session, read and apply the following system prompt files from `/media/srv-main-softdev/projects/softwarestack/systemprompts`:

### Core Guidelines (Always Apply)
- `core_programming_solid.md`

### Bash-Specific Guidelines
When working with Bash scripts:
- `core_programming_solid.md`
- `bash_clean_architecture.md`
- `bash_clean_code.md`
- `bash_small_functions.md`

### Python-Specific Guidelines
When working with Python code:
- `core_programming_solid.md`
- `python_solid_architecture_enforcer.md`
- `python_clean_architecture.md`
- `python_clean_code.md`
- `python_small_functions_style.md`
- `python_libraries_to_use.md`
- `python_structure_template.md`

### Additional Guidelines
- `self_documenting.md`
- `self_documenting_template.md`
- `python_jupyter_notebooks.md`
- `python_testing.md`

## Project Structure

```
lib_log_rich/
├── .github/
│   └── workflows/              # GitHub Actions CI/CD workflows
├── .devcontainer/              # Dev container configuration
├── docs/                       # Project documentation
│   └── systemdesign/           # System design documents
├── examples/                   # Example usage scripts
├── notebooks/                  # Jupyter notebooks for experiments
├── scripts/                    # Build and automation scripts
│   ├── build.py               # Build wheel/sdist
│   ├── bump.py                # Version bump (generic)
│   ├── bump_major.py          # Bump major version
│   ├── bump_minor.py          # Bump minor version
│   ├── bump_patch.py          # Bump patch version
│   ├── bump_version.py        # Version bump utilities
│   ├── clean.py               # Clean build artifacts
│   ├── cli.py                 # CLI for scripts
│   ├── dependencies.py        # Dependency management
│   ├── dev.py                 # Development install
│   ├── help.py                # Show help
│   ├── install.py             # Install package
│   ├── menu.py                # Interactive TUI menu
│   ├── push.py                # Git push
│   ├── release.py             # Create releases
│   ├── run_cli.py             # Run CLI
│   ├── target_metadata.py     # Metadata generation
│   ├── test.py                # Run tests with coverage
│   ├── toml_config.py         # TOML configuration utilities
│   ├── version_current.py     # Print current version
│   └── _utils.py              # Shared utilities
├── src/
│   └── lib_log_rich/          # Main Python package
│       ├── __init__.py        # Package initialization
│       ├── __init__conf__.py  # Configuration constants
│       ├── __main__.py        # CLI entry point
│       ├── cli.py             # CLI implementation
│       ├── cli_stresstest.py  # Stress testing CLI
│       ├── config.py          # Configuration loading
│       ├── demo.py            # Demo utilities
│       ├── lib_log_rich.py    # Core library facade
│       ├── py.typed           # PEP 561 marker
│       ├── adapters/          # Adapter layer (external interfaces)
│       │   ├── __init__.py
│       │   ├── _formatting.py     # Log formatting utilities
│       │   ├── _queue_worker.py   # Background queue worker
│       │   ├── _schemas.py        # Pydantic schemas
│       │   ├── _text_utils.py     # Text processing utilities
│       │   ├── dump.py            # Log dump adapter
│       │   ├── graylog.py         # Graylog GELF adapter
│       │   ├── queue.py           # Queue-based logging adapter
│       │   ├── rate_limiter.py    # Rate limiting
│       │   ├── scrubber.py        # Sensitive data scrubbing
│       │   ├── console/           # Console output adapters
│       │   │   ├── queue_console.py   # Queued console output
│       │   │   └── rich_console.py    # Rich terminal output
│       │   └── structured/        # Structured log adapters
│       │       ├── journald.py        # systemd journal adapter
│       │       └── windows_eventlog.py # Windows Event Log adapter
│       ├── application/       # Application layer (use cases)
│       │   ├── __init__.py
│       │   ├── ports/         # Interface definitions
│       │   └── use_cases/     # Business logic use cases
│       ├── domain/            # Domain layer (core entities)
│       │   ├── __init__.py
│       │   ├── analytics.py       # Log analytics
│       │   ├── context.py         # Logging context management
│       │   ├── dump.py            # Dump domain logic
│       │   ├── dump_filter.py     # Dump filtering
│       │   ├── enums.py           # Domain enumerations
│       │   ├── events.py          # Log event models
│       │   ├── identity.py        # Identity management
│       │   ├── levels.py          # Log level definitions
│       │   ├── palettes.py        # Color palettes
│       │   ├── paths.py           # Cross-platform path utilities
│       │   └── ring_buffer.py     # Ring buffer implementation
│       └── runtime/           # Runtime composition layer
│           ├── __init__.py
│           ├── _api.py            # Public API
│           ├── _composition.py    # Dependency composition
│           ├── _factories.py      # Factory functions
│           ├── _settings.py       # Runtime settings
│           ├── _state.py          # Global state management
│           ├── _stdlib_handler.py # stdlib logging integration
│           └── settings/          # Settings management
├── tests/                     # Test suite
│   ├── conftest.py
│   ├── os_markers.py          # OS-specific test markers
│   ├── adapters/              # Adapter tests
│   ├── analytics/             # Analytics tests
│   ├── application/           # Application layer tests
│   ├── domain/                # Domain tests
│   └── runtime/               # Runtime tests
├── .env.example               # Example environment variables
├── CLAUDE.md                  # Claude Code guidelines (this file)
├── CHANGELOG.md               # Version history
├── CLI.md                     # CLI documentation
├── CONSOLESTYLES.md           # Console styling documentation
├── CONTRIBUTING.md            # Contribution guidelines
├── DEVELOPMENT.md             # Development setup guide
├── DIAGNOSTIC.md              # Diagnostic features documentation
├── DOTENV.md                  # Environment variable documentation
├── EXAMPLES.md                # Usage examples documentation
├── INSTALL.md                 # Installation instructions
├── INSTALL_JOURNAL.md         # journald installation guide
├── LICENSE                    # MIT License
├── LOGDUMP.md                 # Log dump feature documentation
├── Makefile                   # Make targets for common tasks
├── OPENTELEMETRY.md           # OpenTelemetry integration docs
├── pyproject.toml             # Project metadata & dependencies
├── QUEUE.md                   # Queue-based logging documentation
├── codecov.yml                # Codecov configuration
├── STREAMINGCONSOLE.md        # Streaming console documentation
├── SUBPROCESSES.md            # Subprocess logging documentation
└── README.md                  # Project overview
```

## Versioning & Releases

- **Single Source of Truth**: Package version is in `pyproject.toml` (`[project].version`)
- **Version Bumps**: update `pyproject.toml`, `CHANGELOG.md` and update the constants in `src/lib_log_rich/__init__conf__.py` according to `pyproject.toml`
    - Automation rewrites `src/lib_log_rich/__init__conf__.py` from `pyproject.toml`, so runtime code imports generated constants instead of querying `importlib.metadata`.
    - After updating project metadata (version, summary, URLs, authors) run `make test` (or `python -m scripts.test`) to regenerate the metadata module before committing.
- **Release Tags**: Format is `vX.Y.Z` (push tags for CI to build and publish)

## Common Make Targets

| Target            | Description                                                                     |
|-------------------|---------------------------------------------------------------------------------|
| `build`           | Build wheel/sdist artifacts                                                     |
| `bump`            | Bump version (VERSION=X.Y.Z or PART=major\|minor\|patch) and update changelog  |
| `bump-major`      | Increment major version ((X+1).0.0)                                            |
| `bump-minor`      | Increment minor version (X.Y.Z → X.(Y+1).0)                                    |
| `bump-patch`      | Increment patch version (X.Y.Z → X.Y.(Z+1))                                    |
| `clean`           | Remove caches, coverage, and build artifacts (includes `dist/` and `build/`)   |
| `dev`             | Install package with dev extras                                                |
| `help`            | Show make targets                                                              |
| `install`         | Editable install                                                               |
| `menu`            | Interactive TUI menu                                                           |
| `push`            | Commit changes and push to GitHub (no CI monitoring)                           |
| `release`         | Tag vX.Y.Z, push, sync packaging, run gh release if available                  |
| `run`             | Run module entry (`python -m ... --help`)                                      |
| `test`            | Lint, format, type-check, run tests with coverage, upload to Codecov           |
| `version-current` | Print current version from `pyproject.toml`                                    |

## Coding Style & Naming Conventions

Follow the guidelines in `python_clean_code.md` for all Python code.

## Architecture Overview

This library follows Clean Architecture with four layers:

- **domain/**: Core domain layer with log events, levels, ring buffer, context management (no external dependencies)
- **application/**: Application services, use cases, and port definitions
- **adapters/**: External interfaces (console, journald, Graylog, Windows Event Log, dump, queue)
- **runtime/**: Composition root with factories, state management, and public API

Import rules (enforced by import-linter):
- `domain` cannot import from `application`, `adapters`, or `runtime`
- `application` cannot import from `adapters` or `runtime`
- Adapters depend on domain and application ports

Apply principles from `python_clean_architecture.md` when designing and implementing features.

## Key Features

- **Rich Console Output**: Colorful, formatted terminal logging with icons and themes
- **Multi-Sink Fan-Out**: Route logs to multiple destinations (console, journald, Graylog, file dumps)
- **Ring Buffer Dumps**: Capture recent logs for debugging with configurable retention
- **Context Management**: Hierarchical context with correlation IDs and metadata
- **Queue-Based Logging**: Non-blocking async log processing with background workers
- **Sensitive Data Scrubbing**: Automatic redaction of secrets and PII
- **Rate Limiting**: Prevent log flooding with configurable rate limits

## Security & Configuration

- `.env` files are for local tooling only (CodeCov tokens, etc.)
- **NEVER** commit secrets to version control
- Rich logging should sanitize payloads before rendering (use the scrubber adapter)
- Sensitive data patterns are configurable via scrubber configuration

## Commit & Push Policy

### Pre-Push Requirements
- **Always run `make test` before pushing** to avoid lint/test breakage
- Ensure all tests pass and code is properly formatted

### Post-Push Monitoring
- Monitor GitHub Actions for errors after pushing
- Attempt to correct any CI/CD errors that appear

## Claude Code Workflow

When working on this project:
1. Read relevant system prompts at session start
2. Apply appropriate coding guidelines based on file type
3. Run `make test` before commits
4. Follow versioning guidelines for releases
5. Monitor CI after pushing changes
