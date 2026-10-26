# Handover - 2026-10-26 10:45

## In flight

The 7.0 strict data-architecture pass (OPEN-WORK [5], user request) is half done.
`bitranox:coding-python-enforce-data-architecture-strict` ran its first analysis pass: 8 agents,
74 findings, 25 public. The user chose "All 74, release as 7.0.0" (USER-DECISIONS.jsonl, and the
User decisions section of EXECUTION-USER-REVIEW.md). Phase 1 fixed 72 of them across 7
parallel fix groups, merged and gated green. No background agents or timers are running.

Not done yet, in order:
1. Phase 2: `domain/events.py` `LogEvent.from_dict(dict)` and `domain/ring_buffer.py`, which
   parses the checkpoint file in the domain. Move checkpoint reading to an adapter that has a
   Pydantic boundary model, and wire it through `runtime/_factories.py` / `_composition.py`.
2. Second analysis pass (the skill's STEP A) over the changed files.
3. STEP C item 5: diff the error surface against `8104706`, input by input, wherever validation
   moved. Examples: RuntimeConfig enum fields now refuse at construction, and the preset refusal
   wording changed.
4. Docs (about 25 stale passages) and the CHANGELOG 7.0.0 Removed/Changed sections. Bump to
   7.0.0 and release, which the user's option included. Also fold OPEN-WORK [70] (skill bridge
   line plus its bitranox-skills twin) into the release commit.

## Committed, or not

- Local only, 3 commits ahead of origin: `188a940` (shared str enums plus the StrValueEnum
  base), `fc1bb34` (runtime._settings removed), `ef3f8e8` (phase 1). Nothing is pushed. master is
  not releasable until steps 1-4 are done, so do not push it as is.
- These are untracked and excluded via `.git/info/exclude`, and needed: `.data_arch_violations.json`
  (skill state: pass 1, 2 open findings, the rest `in_progress` = fixed, awaiting re-analysis) and
  `.data_arch_release_notes.md` (all CHANGELOG material, stale-doc locations, open decisions,
  collected from the agents' reports). Delete both at release.
- The fix-agent brief is `/tmp/claude-1000/-media-srv-main-softdev-projects-public-libs-lib-log-rich/d2f7a83d-84d1-4017-903c-d08d6e3a7c53/scratchpad/dataarch/BRIEF.md`.
  It is session scratch and may be gone; its contracts are restated below.

## Decided, and why

- The diagnostic hook still receives event names as plain `str` (`DiagnosticEvent.value`), so
  6.x hooks are unaffected. Internal emitters take `DiagnosticEvent`.
- Every str enum subclasses `domain.enums.StrValueEnum`, so `str()` and f-strings give the value
  on 3.10 through 3.14. A plain `str, Enum` prints `Name.MEMBER` on 3.11+.
- The public console/queue/dump constructors refuse a plain string for an enum parameter
  (`require_enum_member`, TypeError). The reason: str enums hash equal to their value, so "full"
  would half-work and "html" would silently render ANSI.
- RuntimeConfig still parses value strings at the boundary (from_str before-validators). That is
  the boundary parse, not a shim.
- I removed a new test that monkeypatched `_factories.RichConsoleAdapter` only to prove the
  deleted fallback stayed deleted. It added no protection and broke the no-internal-patching rule.

## Decided against, and why

- Keeping any alias, or `str | Enum` unions on new public signatures: the user chose the full
  7.0 break. None of our 45 consumer repos uses a removed alias (grep with a positive control).
- Handing over while the agents ran: the user chose to wait for their reports.

## Still open, untouched

- [30] certifi timestamp pin, due on or after 2026-10-28; [70] skill bridge line at release;
  [80] stress-TUI shutdown deadlock; [90] doctests fail when `tests` runs before `src`. All are in
  `OPEN-WORK.md`. The open decisions the agents raised are in `.data_arch_release_notes.md`;
  several are user calls.

## Lessons for the next nap

- When a str enum replaces a str parameter, guard public entry points against raw strings: the
  member hashes equal to its value, so dict lookups half-work while `is` dispatch silently picks
  the default.
- When stopping your own backstop `sleep` timer, find it by PID under `ps --ppid $PPID` (comm
  `sleep`), never with `pkill -f`. The pgrep-self-match guard refuses that, rightly.
- When fixer agents edit one checkout in parallel, the pytest/pyright counts each reports are
  snapshots of a half-edited tree (they differed between 9 and 0 failures), so the coordinator's
  own run is the only verdict.
- tooling: the AskUserQuestion premise gate also flags facts recorded in memory but not
  re-measured that day. Label them "per a recorded memory fact" in the option text up front.

## Next action

Invoke `bitranox:coding-python-enforce-data-architecture-strict` (it is resumed by re-invoking it,
and the skill's current text wins over this summary). Read `.data_arch_violations.json` and
`.data_arch_release_notes.md`, then start phase 2 with a RED test: a corrupted checkpoint line
must be refused by a typed boundary model, not passed to `LogEvent.from_dict` as a raw dict.

## Files that matter

- `src/lib_log_rich/domain/enums.py` (StrValueEnum, FormatPreset, ExportStyle, DropReason,
  DiagnosticEvent), `src/lib_log_rich/domain/context.py` (ContextSnapshot, seed),
  `src/lib_log_rich/domain/events.py`, `src/lib_log_rich/domain/ring_buffer.py` (phase 2),
  `src/lib_log_rich/runtime/_factories.py`, `src/lib_log_rich/runtime/settings/models.py`,
  `CHANGELOG.md`, `OPEN-WORK.md`.

## How to verify

- `env -u FORCE_COLOR .venv/bin/python -m pytest -q -p no:cacheprovider src tests` (src first;
  see [90]), `.venv/bin/pyright --pythonpath .venv/bin/python`, `.venv/bin/ruff check src tests examples`,
  `.venv/bin/lint-imports`, then `make test` as the gate.
- At `ef3f8e8` all of these passed (1058 tests, pyright 0, ruff clean, 1 contract kept). This
  file and the OPEN-WORK edit were written after that run; re-run before building on it.
- `git log --oneline origin/master..master` must list exactly the three commits above, plus this
  handover commit.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
