# Handover - 2026-10-20 00:10

## In flight

Nothing. No background agents are running. lib_log_rich 6.5.5 is released (PyPI serves it) and
this repo has no work part-done. The session's other work, all in bitranox-skills, is pushed and
released: 9.8.0 (ci_wait `--queued-grace`, repo-gate follows `W=/abs; cd "$W"`, the
process-test-design 3.10 `Path.cwd` bullet) and 9.8.3 (a cd inside a subshell is undone when it
closes, CDPATH-relative cd reads as unknown). Its worktrees are removed.

## Committed, or not

Everything is pushed except this `handover.md` and the `OPEN-WORK.md` line [10]; both go in one
commit. `EXECUTION-USER-REVIEW.md` (gitignored) here and in bitranox-skills hold the decisions.

## Decided, and why

- `[tool.uv] exclude-newer = "7 days"` (user decision 2026-10-19, "7 days in both"). The runtime
  tree did not move; seven dev-only packages are one release back until they age in.
- `config._current_directory()` returns None on ANY OSError, so a deleted cwd (ENOENT) ends the
  .env search like an unenterable one on macOS (EACCES); an explicit missing `search_from` still
  raises FileNotFoundError.

## Decided against, and why

- Lowering `ruff>=0.17.1` to fit the 7-day cooldown: a lowered floor loses fixes; bmk pins the
  release by timestamp instead (see [10]).

## Still open, untouched

- [10] drop the ruff timestamp floor pin after 2026-10-22 - see `OPEN-WORK.md`.
- bitranox-skills [660]: cd inside a `case` arm or a called function body - in THAT repo's
  `OPEN-WORK.md`, not this one.

## Lessons for the next nap

- When a probe or harness takes a tool name from argv, the string "None" is not Python None: a
  parity harness run with `None` on its command line tested an unknown tool and printed dozens of
  false WRONG rows.
- When adding a module-level helper constant to a large test file, grep the file for the name
  first: a new `_B` shadowed an existing backslash constant and broke an unrelated Windows-path test.
- tooling: two opus bash-parity reviews of `shell_text.git_verb_dir` each found wrong-directory
  regressions the author's own tests passed (about 40 in the variable rule, a dozen in the subshell
  rule); a shell-text heuristic ships only after a harness compares it with real bash (recurrence
  of feedback-your-own-verification-is-blind-in-the-direction-your-fix-is-wrong).
- tooling (carried from the 2026-10-08 handover, no home found in the store or the contrib queue):
  `block-partial-typecheck` refuses `pyright <scratch file>` outside the repo; a scratch dir with its
  own `pyrightconfig.json` and a no-path run works.

## Exact next action

Nothing is due until [10]: on the first `make test` on or after 2026-10-23, run
`grep -n ruff pyproject.toml` and, if `ruff = "2026-10-15T15:23:19Z"` is still under
`exclude-newer-package`, delete it, rerun `make test` to relock, and commit.

## Files that matter

- `pyproject.toml` (`[tool.uv]` exclude-newer and exclude-newer-package)
- `src/lib_log_rich/config.py` (`_current_directory`, `_probe`, `enable_dotenv`)
- `tests/test_config_dotenv.py`

## How to verify

- `make test` and `make test-all` in this repo.
- CI for the pushed commit: `uv run <toolbox>/scripts/ci_wait.py --ref origin/master`.
- `curl -s https://pypi.org/pypi/lib_log_rich/json` reports `info.version` 6.5.5.

> Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
> delete it - if this session ends badly it is the only record of where things stood.
