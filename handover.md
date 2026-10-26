# Handover - 2026-10-26 13:35

## In flight

- lib_log_rich 7.0.0 is RELEASED (tag v7.0.0 on 1525ea0; CI, CodeQL and the Release workflow
  green; PyPI JSON and simple index list 7.0.0). Nothing of the release is pending.
- One background shell from this session may still be running and will report into the next
  conversation: `ci_wait.py --sha 4d31ae00f91403972a927dbc67cf21668a4ce0fd`, watching bitranox-skills
  CI for the skill twin (commit `coding-python-logging: lib_log_rich 7.0 typed API; 10.0.22`).
  - Green: remove the twin worktree and its branches in bitranox-skills:
    `git worktree remove .claude/worktrees/lib-log-rich-7-twin` in the bitranox-skills checkout,
    then `git branch -D lib-log-rich-7-twin lib-log-rich-7-twin-v1` there (both pushed or superseded:
    -v1 is the pre-rebuild copy, its change shipped as 4d31ae00).
  - Red: fix it in that worktree on top of origin/master; another session works in the
    bitranox-skills MAIN checkout, so never touch that one.

## Committed, or not

- lib_log_rich master: everything through 1525ea0 is pushed. The commit carrying this handover and
  the OPEN-WORK closures ([5], [70] closed; [95], [96], [97] added) is LOCAL ONLY - push it (a
  backlog-only change; watch CI with `ci_wait.py --ref HEAD`).
- Git-ignored: `EXECUTION-USER-REVIEW.md` holds this session's autonomous decisions (newest first).
- `.data_arch_release_notes.md` was deleted at release; its open items are OPEN-WORK [95]-[97].

## Decided, and why

- `RuntimeConfig(...)` keeps raising pydantic's `ValidationError` for the refusals 7.0 moved there:
  6.x already raised it there for wrong types, the shipped skill documents it, and known consumers
  catch it and render `.errors()` themselves. Listed in CHANGELOG 7.0.0.
- `StdlibLoggingHandler._normalise_args` stays: records from `logging.makeLogRecord` or third-party
  code carry `None` or a scalar in `args` (measured); pinned by a process-seam test (c43efed).
- OPEN-WORK [87] (SHORT_LOC_ICON dump preset) was filed, not fixed in 7.0: pre-existing, needs a
  refuse-or-support decision; the 7.0 docs describe current behaviour.

## Decided against, and why

- Re-wrapping `RuntimeConfig` construction errors as plain `ValueError`: breaks consumers that
  catch `ValidationError`.
- Fixing the wrong item number in the local backlog commit e3ebf71's message by amend: auto mode
  refuses amend as destructive, and the message is cosmetic.

## Still open, untouched

- Everything else is in `OPEN-WORK.md`; three items wait on a user decision ([65], [75], [87]),
  [30] is due on or after 2026-10-28.

## Lessons for the next nap

- When a data-architecture fixer pass edits files outside its violation list (hand-offs, call sites
  of a changed signature), mark every file in `git diff --name-only <pass-start>` in_progress before
  the next analysis, or those files skip re-analysis (queued as a skill contribution).
- When the bitranox-skills repo-gate blocks a tool-repo PUSH for twin drift, the twin must be on
  bitranox-skills ORIGIN master, not only committed in a worktree: push the twin first.
- When another session takes your plugin version number between fetch and push, rebuild the change
  on current origin with the next number rather than resolving version conflicts in a rebase.
- When a quote-back check finds 0 hits for a quoted sentence, re-check with whitespace joined
  before calling it fabricated: Markdown line wraps split quotes.
- When a test of a normaliser passes with the normaliser bypassed, the value is in the contract,
  not the rendered text: assert at the seam that receives its output.
- When an error-surface probe calls `dump()`, log an event first: an empty ring buffer returns
  early and hides argument validation.
- tooling: auto mode refuses `git commit --amend` (message-only) as "Git Destructive".

## Next action

Read `OPEN-WORK.md`, then ask the user the three pending decisions one at a time ([65] init()
checkpoint setting now that 7.0 shipped, [75] dump theme vs palette styles, [87] refuse or support
SHORT_LOC_ICON for dumps), then work [85] (LoggerProxy single-mapping args) as a 7.0.1 fix.

## Files that matter

- `OPEN-WORK.md`, `CHANGELOG.md` (7.0.0 entry), `EXECUTION-USER-REVIEW.md`
- `src/lib_log_rich/runtime/_factories.py` (LoggerProxy, for [85]),
  `src/lib_log_rich/runtime/settings/models.py` (RuntimeConfig, for [65] and [87])

## How to verify

- PyPI: `curl -s https://pypi.org/pypi/lib_log_rich/json` shows `info.version` 7.0.0.
- Gate: `make test` (release gate); CI for a push: `ci_wait.py --ref HEAD` from bitranox:compuse-toolbox.
- Twin pair: `python3 plugins/bitranox/hooks/repo-gate.py --mirrors` in bitranox-skills lists
  `coding-python-logging` as in sync.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
