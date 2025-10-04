# Changelog

All notable changes to this project will be documented in this file, following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.

## [Unreleased]

### Added
- Introduced `SystemIdentityPort` and a default system identity provider so the application layer no longer reaches into `os`, `socket`, or `getpass` directly when refreshing logging context metadata.

### Changed
- `QueueAdapter.stop()` is now transactional: it raises a `RuntimeError` and emits a `queue_shutdown_timeout` diagnostic when the worker thread fails to join within the configured timeout. `lib_log_rich.shutdown()` and `shutdown_async()` clear the global runtime only after a successful teardown.
- Optimised text dump rendering by caching Rich style wrappers, reducing per-line allocations when exporting large ring buffers.
- Documentation refreshed to cover the new identity port, queue diagnostics, and changelog format.
- Enforced the documented five-second default for `queue_stop_timeout` so shutdown no longer blocks indefinitely unless callers opt in.

## [1.1.0] - 2025-10-03

### Added
- Enforced payload limits with diagnostic hooks exposing truncation events.

### Changed
- Hardened the async queue pipeline so worker crashes are logged, flagged, and surfaced through the diagnostic hook instead of killing the thread; introduced a `worker_failed` indicator with automatic cooldown reset.
- Drop callbacks that explode now emit structured diagnostics and error logs, ensuring operators see failures instead of silent drops.
- Guarded CLI regex filters with friendly `click.BadParameter` messaging so typos no longer bubble up raw `re.error` traces to users.

### Tests
- Added regression coverage for the new queue failure paths (adapter unit tests plus an integration guard around `lib_log_rich.init`) and the CLI validation to keep the behaviour locked in.

## [1.0.0] - 2025-10-02

### Added
- Initial Rich logging backbone MVP.
