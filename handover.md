# STALE - read 2026-10-26 11:31, work continued

## In flight

OPEN-WORK [5] (user request): the 7.0 strict data-architecture pass, run with
`bitranox:coding-python-enforce-data-architecture-strict`. Phase 1, phase 2 (checkpoint adapter)
and pass 2 (second analysis: 26 findings, all fixed) are committed. The state file
`.data_arch_violations.json` reads pass 2, total_violations 0, with the changed files marked
`in_progress` = fixed and awaiting re-analysis. No agents, timers or background shells are running.

Remaining, in order:
1. STEP A pass 3 over every file with status `in_progress` in `.data_arch_violations.json`;
   loop STEP B/A until an analysis returns 0 findings.
2. STEP C item 5: diff the error surface against `8104706`, one malformed input at a time,
   wherever validation moved (`git archive <rev> src | tar -x -C <dir>`, run with
   `PYTHONPATH=<dir>/src`; include one input changed on purpose as a control). The
   before/after rows already measured are in `.data_arch_release_notes.md` (wave 2 section).
3. Docs: about 45 stale passages, listed per file in `.data_arch_release_notes.md`, plus the user
   decision that ring-buffer persistence is documented as for direct `RingBuffer` users only.
4. CHANGELOG 7.0.0 (all material is in the notes file, including the `Fixed` entry for d4e9ac2),
   `make test`, bump to 7.0.0, release; fold OPEN-WORK [70] into the release commit; delete both
   `.data_arch_*` files at release.

## Committed, or not

- Local only, not pushed: 188a940..d4e9ac2 (`git log --oneline origin/master..master`). master is
  not releasable until steps 1-4 are done, so do not push it as it is.
- Untracked and excluded via `.git/info/exclude`, needed: `.data_arch_violations.json`,
  `.data_arch_release_notes.md`. Also the git-ignored `EXECUTION-USER-REVIEW.md` (decision log).
- The pass-2 analysis and fix briefs (`pass2/BRIEF.md`, `pass2/FIX_BRIEF.md`) were in the previous
  session's scratchpad and are likely gone; rewrite them from the skill.

## Decided, and why

- User, 2026-10-26: `init()` still gets no checkpoint setting; document that persistence is for
  direct `RingBuffer(checkpoint=JsonlCheckpoint(path))` users. The setting itself is [65].
- Checkpoint persistence is a domain port (`EventCheckpoint`), so the domain does no I/O and no
  parsing; `JsonlCheckpoint` is strict (unknown fields, coerced ints, non-object lines refused)
  because only this library writes the file. Lines stay `LogEvent.to_json()` byte for byte.
- `ContextSnapshot` JSON lives in `adapters/context_snapshot.py` for the same reason.
- `ConsoleTheme` mirrors `FormatPreset` at every boundary; an event's `extra["theme"]` stays
  tolerant (free-form caller data must not fail a dump).
- `ConsoleAppearance` still normalises style keys once more on the resolve path (idempotent): a
  directly built `ConsoleAppearance` is its own entry point (argued, not measured).
- Kept from phase 1: the diagnostic hook receives plain `str` names (6.x contract); str enums
  subclass `StrValueEnum`; public constructors refuse raw strings via `require_enum_member`;
  `RuntimeConfig` parses value strings in before-validators (boundary parse, not a shim).
- The sanitizer size fix (d4e9ac2) is its own commit: the bug predates 7.0 (fails on 8104706).

## Decided against, and why

- A shared `_Keep` sentinel for the typed `replace` methods: an underscore import across modules
  trips pyright's reportPrivateUsage, and a public one widens the domain API.
- Changing the sanitizer's shared encoder: only the size measurement was wrong; the encoder also
  turns unserialisable values into text, which is a separate behaviour.

## Still open, untouched

- [30] certifi pin (due on or after 2026-10-28), [65] init() checkpoint setting, [70] skill bridge
  line at release, [75] dump(theme=X) ignored once palette styles exist (needs a user decision),
  [80] stress-TUI shutdown deadlock, [90] doctests fail when `tests` runs before `src`. All in
  `OPEN-WORK.md`. Open user calls collected by agents are in `.data_arch_release_notes.md`.

## Lessons for the next nap

- When a parallel fan-out reports back, save each hand-back to a file the moment it arrives and
  count the files before merging: one unsaved report silently dropped 7 of 26 findings from a
  computed total.
- When moving module imports under TYPE_CHECKING, give every doctest that used those names its own
  import first: doctests read module globals.
- When a Hypothesis property fails during a refactor, replay the exact counterexample against the
  pre-refactor commit (git archive + PYTHONPATH, printing which module loaded) before blaming the
  refactor.
- tooling: block-partial-typecheck reads an unexpanded `$v` after pyright as a path and refuses the
  whole call (queued with contrib_add).

## Next action

Invoke `bitranox:coding-python-enforce-data-architecture-strict` (it resumes by re-invoking; its
current text wins over this summary), read `.data_arch_violations.json`, set pass to 3, and launch
the STEP A analysers over the `in_progress` files, grouped about 8 ways with disjoint file sets.

## Files that matter

- `.data_arch_violations.json`, `.data_arch_release_notes.md`, `OPEN-WORK.md`, `CHANGELOG.md`
- `src/lib_log_rich/adapters/checkpoint.py`, `src/lib_log_rich/adapters/context_snapshot.py`,
  `src/lib_log_rich/adapters/_validation.py`, `src/lib_log_rich/domain/enums.py`,
  `src/lib_log_rich/domain/ring_buffer.py`, `src/lib_log_rich/runtime/settings/models.py`,
  `src/lib_log_rich/runtime/settings/resolvers.py`

## How to verify

- `env -u FORCE_COLOR .venv/bin/python -m pytest -q -p no:cacheprovider src tests` (src first, see
  [90]), `.venv/bin/pyright --pythonpath .venv/bin/python` and the same with
  `--pythonversion 3.10` (no path arguments: a hook refuses partial runs), `.venv/bin/ruff check
  src tests examples`, `.venv/bin/lint-imports`; `make test` is the release gate.
- At d4e9ac2 the four direct checks passed (1143 passed, 12 skipped; pyright 0 at both versions;
  ruff clean; 1 contract kept). This file and the OPEN-WORK edit were written after that run.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
