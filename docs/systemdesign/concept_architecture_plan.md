# Implementation Roadmap (TDD) — lib_log_rich Logging Backbone

## 0. Goals & References
- Source documents: `concept.md` (product concept) and `concept_architecture.md` (architecture guide).
- Objective: deliver the complete logging backbone using a test-driven workflow (red → green → refactor) with production-ready adapters, documentation, and tooling.
- Result: console + platform adapters, optional Graylog, ring buffer dumps, configuration surface, context propagation, diagnostic hook, and thread-based queue offload (see `SUBPROCESSES.md` for multi-process propagation patterns).

## 1. Working Agreements
1. **TDD cadence per work item**
   - Start with a failing unit/contract/integration test.
   - Implement the minimum code to pass.
   - Refactor for clarity while keeping tests green.
2. **Test taxonomy**
   - *Unit*: domain entities, use case helpers, value conversions (pytest + hypothesis).
   - *Contract*: ports verified across fake adapters via parametrised tests.
   - *Integration*: queue fan-out, multiprocessing, dump pipeline.
   - *Snapshot*: console/HTML rendering approval tests.
3. **Definition of Done (global)**
   - `make test` (ruff, pyright, pytest, coverage) passes.
   - Documentation updated (module reference, concept docs, README excerpts where relevant).
   - No TODO/FIXME left; aligns with repo guidelines.

## 2. Step-by-Step Plan

### Phase A — Foundational Test Harness
1. **A1: Test fixtures & utilities**
   - Create pytest fixtures for fake adapters, deterministic clocks, ID providers.
   - Add doctest coverage for helper utilities.
2. **A2: Architectural linting**
   - Configure import-linter (domain → application → adapters) and wire into CI/`make test`.

### Phase B — Domain Layer
3. **B1: Value objects & events**
   - Implement `LogLevel`, `LogEvent`, `LogContext`, `DumpFormat` with validation.
   - Property tests assert conversions, serialisation, `process_id_chain` normalisation.
4. **B2: Context binder**
   - Build `ContextBinder` with stack semantics, serialisation for subprocesses, doctests demonstrating usage.

### Phase C — Application Layer
5. **C1: Ports & protocols**
   - Define `ConsolePort`, `StructuredBackendPort`, `GraylogPort`, `DumpPort`, `QueuePort`, `ScrubberPort`, `RateLimiterPort`, `ClockPort`, `IdProvider`.
   - Provide contract tests ensuring fake adapters satisfy each port.
6. **C2: Use cases**
   - Implement `process_log_event`, `capture_dump`, `shutdown` with rate limiting, scrubbing, ring buffer updates, queue hand-off, and diagnostic hook events.
7. **C3: Composition root (`init`)**
   - Wire adapters based on configuration, including toggles for journald, Event Log, Graylog, ring buffer, queue.
   - Honour environment overrides and `.env` opt-in path.

### Phase D — Adapter Layer
8. **D1: Console (Rich) adapter**
   - Support theme merging, icons, `{level_code}`, disabled colour path, unit + snapshot tests.
9. **D2: Journald adapter**
   - Emit uppercase fields; fake systemd bindings for tests; skip gracefully when unavailable.
10. **D3: Windows Event Log adapter**
    - Wrap `win32evtlogutil`; provide fake implementation for tests; configurable Event IDs.
11. **D4: Graylog adapter (optional)**
    - TCP/TLS client with retry/backoff; UDP support; no-op variant when disabled.
12. **D5: Dump adapter**
    - Text/JSON/HTML renderers; ensure `{process_id_chain}` placeholder surfaces; enforce level/context/extra filtering semantics; write-to-path option.
13. **D6: Queue adapter**
    - Bounded queue with one-second `queue_put_timeout`, degraded-drop diagnostics, drop handler support, background worker, and sentinel shutdown; stress tests for lossless delivery.
14. **D7: Scrubber & rate limiter**
    - Default regex patterns (covering both event `extra` and `LogContext.extra`), sliding window configuration, and tests for masking/throttling without mutating caller-visible objects.

### Phase E — CLI & Observability
15. **E1: CLI surface**
    - `info`, `hello`, `fail`, `logdemo`; tests with Click runner; expose dump filter flags (`--context-exact`, `--extra-regex`, etc.); doc updates.
16. **E2: Diagnostic hook**
    - Surface `queued`, `queue_dropped`, `queue_degraded_drop_mode`, `queue_worker_error`, `emitted`, and `rate_limited` events; ensure hook failures do not recurse and cannot break logging.

### Phase F — Documentation & Release Prep
17. **F1: Documentation refresh**
    - Update module reference, concept docs, README usage, and system design appendices; ensure doctests run.
18. **F2: Release readiness**
    - `make build`, coverage ≥ 90%, changelog entry.

## 3. Additional Notes
- Feature branches per phase with clear PRs.
- Mock external systems (journald, Event Log, Graylog) for automated tests; allow manual integration tests separately.
- Extend CI matrix (Linux, Windows) once adapters prove stable.
- Maintain rollback safety: revert entire phase if instability occurs.

## 4. Overall Definition of Done
- Every phase-specific DoD completed.
- `concept_architecture.md` remains the architectural authority; update this plan when deviations occur.
- Commit history demonstrates test-first workflow (failing test → implementation → refactor).
- Scrubbing, rate limiting, and diagnostic hook coverage validated; secrets never leak to sinks.

*Last updated: 01 October 2025*
