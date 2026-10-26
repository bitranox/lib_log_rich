# STALE - read 2026-10-26, work continued

## In flight

- 8.0.0 is PUSHED (fix mode ask-big chosen by the user, who also gave the go for the push, the release
  and the twin push). CI on 9ea1bed failed once on 3.14t windows: a test released the queue worker before
  `stop(drain=False)` had begun. The follow-up commit adds `QueueAdapter.debug().wait_for_stop_request()`
  and waits on it. Next: CI on HEAD (`ci_wait.py --ref HEAD`), then `make release`,
  watch default_release_public.yml for that sha, check PyPI JSON + simple index, then push the twin, then
  `make clean-all`, then `skill_receipt.py end meta-skill-writer`.
- The bitranox-skills twin is COMMITTED LOCALLY in its main checkout as 194e797e (10.2.5, on origin d495ef76),
  NOT pushed. Before pushing: `git fetch` alone, and if origin moved, `git reset --keep origin/master` and
  re-apply the mirror under the next version. Commit there needs `git add` in an earlier command than the
  commit (the repo gate reads the index).
- No subagent of this session is running.

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

Wait for CI on HEAD (`ci_wait.py --ref HEAD`), then continue tfbpr at Step 11 (`make release`). The
public push, the release and the twin push were approved by the user's fix-mode answer.

## How to verify

- `env -u VIRTUAL_ENV make -C <lib_log_rich> test` -> `"result":"pass"`; `make test-all` -> all PASS.
- After push: `ci_wait.py --ref HEAD` (bitranox:compuse-toolbox).
- Twin parity: `python3 <bitranox-skills>/plugins/bitranox/hooks/repo-gate.py --mirror-of <lib_log_rich>`.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
