# Changelog

## [Unreleased]
- Renamed the HTML dump format to `html_table` and added a new `html_txt` renderer that honours runtime themes or console styles (monochrome when `color=False`).
- Extended CLI and documentation to surface the new dump formats and default palette behaviour.

## [0.0.1] - 2025-09-23
- Initial Rich logging backbone MVP.
- Added domain model (`LogContext`, `LogEvent`, `LogLevel`, `DumpFormat`, `RingBuffer`) with property-tested invariants.
- Introduced application ports and use cases for processing, dumping, and shutdown orchestration.
- Implemented adapters for Rich console, journald, Windows Event Log, Graylog, dump exporters, queue infrastructure, scrubbing, and rate limiting.
- Exposed public façade (`init`, `bind`, `get`, `dump`, `shutdown`, `logdemo`) plus diagnostic hooks.
- Console palette overrides via `init(console_styles=...)` and `LOG_CONSOLE_STYLES`; shipped themed previews through `logdemo` and documented palettes in `CONSOLESTYLES.md`.
- Captured `user_name`, short `hostname`, `process_id`, and bounded `process_id_chain` for every event; surfaced the fields across text/JSON/HTML dumps, Rich console, Graylog, journald, and Windows Event Log.
- Added ring buffer configuration knobs (`ring_buffer_size`, `LOG_RING_BUFFER_SIZE`) and dump/console customisation (`level`, `console_format_preset`, `console_format_template`, `color`) with matching environment overrides (`LOG_CONSOLE_FORMAT_PRESET`, `LOG_CONSOLE_FORMAT_TEMPLATE`, `LOG_DUMP_FORMAT_PRESET`, `LOG_DUMP_FORMAT_TEMPLATE`).
- Introduced `graylog_level`/`LOG_GRAYLOG_LEVEL` so Graylog fan-out has a configurable severity threshold (defaults to `WARNING` when enabled).
- Added local-time formatting presets (`full_loc`, `short_loc`) for console and text dumps alongside new placeholders (`timestamp_loc`, `YYYY_loc`, etc.) and taught text dumps to honour Rich theme palettes when colouring output.
- Removed the deprecated `text_format` argument from `log.dump(...)` and its CLI equivalent; custom layouts now use `console_format_template` (with presets via `console_format_preset`).
- Added `LOG_SCRUB_PATTERNS` environment override, merged with per-call scrub patterns.
- Graylog adapter now supports TCP or UDP transport plus optional TLS with validation of incompatible combinations.
- Auto-disable journald on Windows and Windows Event Log on non-Windows hosts to match platform support.
- `logdemo` can now exercise Graylog (`--enable-graylog` with endpoint/protocol/TLS options), systemd-journald (`--enable-journald`), and Windows Event Log (`--enable-eventlog`) from the CLI or API; the helper reports requested sinks via the returned `backends` mapping for quick manual verification.
- Graylog adapter now keeps TCP connections open and transparently reconnects after failures, eliminating new socket churn for every event while still supporting UDP/TLS transports.
- Reorganised documentation: moved install steps to `INSTALL.md`, contributor workflow to `DEVELOPMENT.md`, added `SUBPROCESSES.md`, and expanded README usage guidance.
