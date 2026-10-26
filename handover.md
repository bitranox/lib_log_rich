# Handover - lib_log_rich 8.0.0 backlog sweep

## In flight

- Backlog items [83] [84] [90] [93] [95] [96] [97] [98] are implemented and committed LOCAL ONLY
  (`6ce0dd6`..`4ec9019`, eight commits, unpushed). `make test` and `make test-all` (3.10-3.15 plus
  3.14t/3.15t) both passed on that tree (scratchpad gate8.log, 2026-10-26 19:57).
- Skill twin: DONE through meta-skill-writer (RED + 4 GREEN rounds, opus and haiku). lib_log_rich's
  skills/python-logging is edited but NOT committed yet; the bitranox-skills twin is PARKED UNCOMMITTED in
  .claude/worktrees/lib-log-rich-800-twin (branch lib-log-rich-800-twin on origin/master d495ef76, 10.2.4), with
  its review artifact .skillwriter/checklist-20261026-lib-8.0.0.md. At push time (after PyPI): fetch, rebase onto
  origin/master, run the scratchpad bump1024.py <worktree> <origin-version> <next> (plugin.json + pyproject +
  CHANGELOG), commit, `git push origin HEAD:master`. A peer pushed 10.2.4 while this ran.
- Next: commit the lib_log_rich skill edit (plus .claude-plugin/plugin.json 7.3.0 -> 8.0.0 at the bump), then
  release 8.0.0 via tfbpr (make bump-major, CHANGELOG [Unreleased] -> 8.0.0, push with MSG=, ci_wait --ref HEAD,
  make release, PyPI JSON + simple index), then the twin push, then `skill_receipt.py end meta-skill-writer`.
- No background agent of this session is running.

## Decided, and why

- [95] drop both root CLI options now; [98] RuntimeConfig/PayloadLimits refuse unknown keys in
  8.0; [96] re-export enums, enum-only theme/presets in dump()/logdemo(), tidy internals - but
  `dump_format` and `level` keep accepting strings (README quickstart uses them); [93] CLAUDE.md
  gets a directory-level tree (CLAUDE.md is gitignored, local only). All user decisions.
- [84] closed as measured, not a defect: the sentinel put waits only while the worker finishes its
  current item.
- [97] one narrow `# pyright: ignore[reportIncompatibleVariableOverride]` on `_ConsoleStreamTee.encoding`:
  TextIOBase.encoding is a read-only C slot while typeshed declares a plain attribute; a descriptor
  and an instance attribute both failed.

## Still open, untouched

- OPEN-WORK.md here: [25], [30] (due 2026-10-28), [80].
- default_cicd_public OPEN-WORK [40]: push of the 32 consumer repos awaits the user's go.
- Queued contribs (contrib_queue): process-test-design, coding-python-textual, and a hook proposal
  for bare `git ls-files` used as a tracking test (awaits a user decision).

## Lessons for the next nap

- When a background gate's notice says exit 0, read the gate log's per-gate result lines before
  concluding; the summary file path a session expects may not exist.
- When grepping a 19 MB transcript for context, parse it with python json, not grep -o with wide
  `.{0,300}` ranges: the grep ran past the 120 s timeout.

## Next action

Invoke bitranox:meta-skill-writer for the skill-twin edits listed above, then tfbpr for 8.0.0. The
public push and release were approved as part of the plan.

## How to verify

- `env -u VIRTUAL_ENV make -C <lib_log_rich> test` -> `"result":"pass"`; `make test-all` -> all PASS.
- After push: `ci_wait.py --ref HEAD` (bitranox:compuse-toolbox).
- Twin parity: `python3 <bitranox-skills>/plugins/bitranox/hooks/repo-gate.py --mirror-of <lib_log_rich>`.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
