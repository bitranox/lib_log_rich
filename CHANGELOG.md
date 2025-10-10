# Changelog

All notable changes to this project will be documented in this file, following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.


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

- refractor

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
