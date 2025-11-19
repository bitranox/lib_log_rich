# Changelog

All notable changes to this project will be documented in this file, following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [5.3.1] - 2025-11-19

### Added
- **Emoji Stripping for Structured Logging**: Implemented automatic removal of emoji and Unicode pictographic symbols from structured logging outputs:
  - Journald adapter now strips emoji from `MESSAGE` field to ensure compatibility with log viewers that don't support UTF-8 emoji
  - Graylog adapter now strips emoji from `short_message` field for clean GELF protocol output
  - Created shared `_text_utils.py` utility module with centralized emoji stripping logic
  - Comprehensive Unicode range coverage including all log level icons (🐞, ℹ, ⚠, ✖, ☠, 🔥, 💥)
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
- `ContextBinder` restores the bootstrap context stack for newly spawned threads or tasks, eliminating spurious “No logging context bound” runtime errors in background workers.

## [5.0.0] - 2025-10-17

### Added
- Added `LoggerProxy.exception(...)` to mirror `logging.Logger.exception` semantics, defaulting `exc_info=True` while keeping stack capture opt-in and flowing through the structured pipeline.
- Console output now defaults to stderr (matching Python’s built-in logger) and can be redirected via `RuntimeConfig.console_stream` to `stdout`, `stderr`, `both`, `none`, or a caller-supplied stream (`console_stream_target`), keeping Rich formatting while matching host expectations.
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
- Updated GitHub Actions workflows to `actions/checkout@v5` and `actions/setup-python@v6`, keeping runners on `ubuntu-latest` while aligning with current action releases.
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
