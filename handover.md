# STALE - read 2026-10-25, work continued

## In flight

Nothing is part-done in this repo. No background agents are running from this session.
lib_log_rich 6.5.6 is released (tag v6.5.6, PyPI serves wheel and sdist). It removed the inert
wheel `include = ["src/lib_log_rich/py.typed"]` line and added a packaging test that pins every
file of the package in the wheel. A user had asked whether that line limited packaging; the reply
text was handed to the user in chat (answer: no, hatchling's `packages` walk ignores `include`).

## Committed, or not

- This `handover.md` plus the new `OPEN-WORK.md` line [30] go in one commit.
- `bf6c0ee` (backlog line [20], committed 2026-10-22 13:27 by ANOTHER session, from pwshpy work)
  is local and not pushed. This session did not push it; whoever pushes next ships it.
- `0f43726` (CI action sha pins) and `8b342ed` (dev-only certifi floor) are on origin after
  v6.5.6. Neither touches the published package, so neither needs a release on its own.
- `EXECUTION-USER-REVIEW.md` (gitignored) holds this session's decisions.

## Decided, and why

- No other install method is needed for the user's question: measured, the wheel holds 77
  members (all 69 modules, py.typed, two TOML defaults) with and without the include line.
- The new wheel test compares every file under `src/lib_log_rich` (TOML data included) with the
  wheel, not only `.py` files, because the runtime loads the bundled TOML defaults.

## Decided against, and why

- Fixing [20] or touching [10]/[30] in this session: [10] and [30] are date-blocked, and [20] was
  filed minutes before this handover by another session.

## Still open, untouched

- [10] drop the ruff timestamp pin, on or after 2026-10-23 - see `OPEN-WORK.md`.
- [20] Rich console adapter prints log lines with markup on (`[/]` drops the line) - see `OPEN-WORK.md`.
- [30] drop the certifi timestamp pin, on or after 2026-10-28 - see `OPEN-WORK.md`.
- bitranox-skills [660] lives in THAT repo's `OPEN-WORK.md`.

## Lessons for the next nap

- When a hatchling wheel target has `include` beside `packages`, it is inert (captured as
  reference-hatchling-s-wheel-include-is-inert-beside-packages at the libs level; listed here only
  so the nap knows it is done).
- When bmk or a person adds a `[tool.uv] exclude-newer-package` timestamp for a floor, file a
  backlog line to drop it in the same commit: the certifi pin landed with none and was found only
  while writing this handover.
- UNSOLVED: the first `uv run pytest` on a freshly deleted `.venv` installed 15 packages and then
  failed `import orjson` (a base dependency); `uv sync --extra dev` fixed it. The cause was not found.
- tooling: the self-improve Stop gate re-demanded the meta-dream-nap on every turn although the
  user had said not to read the old compacted session; it has no way to record that decline.
- (carried, nap has not run since 2026-10-20) When a probe or harness takes a tool name from argv,
  the string "None" is not Python None: a parity harness tested an unknown tool and printed false
  WRONG rows.
- (carried) When adding a module-level helper constant to a large test file, grep the file for the
  name first: a new `_B` shadowed an existing backslash constant and broke a Windows-path test.
- (carried) tooling: two opus bash-parity reviews of `shell_text.git_verb_dir` each found
  wrong-directory regressions the author's tests passed; a shell-text heuristic ships only after a
  harness compares it with real bash.
- (carried) tooling: `block-partial-typecheck` refuses `pyright <scratch file>` outside the repo; a
  scratch dir with its own `pyrightconfig.json` and a no-path run works.

## Exact next action

[20] is next, ahead of the top rank [10], because [10] cannot be done before 2026-10-23 (ruff
0.17.1 clears the 7-day cooldown on 2026-10-22 15:23 UTC). Start with the RED test the line names:
log a message containing `a[/]b` through the Rich console adapter and require it on the console
verbatim, no MarkupError; the fix site is `src/lib_log_rich/adapters/console/rich_console.py:219`.
First confirm with the user whether the session that filed [20] is already working on it.

## Files that matter

- `OPEN-WORK.md` (items [10], [20], [30])
- `src/lib_log_rich/adapters/console/rich_console.py` (the [20] fix site)
- `pyproject.toml` (`[tool.uv]` exclude-newer-package: ruff and certifi pins)
- `tests/test_packaging.py` (wheel/sdist content tests)

## How to verify

- `make test` in this repo.
- CI for a pushed commit: `ci_wait.py --ref HEAD` from `bitranox:compuse-toolbox`.
- `curl -s https://pypi.org/pypi/lib-log-rich/json` reports `info.version` 6.5.6 (the release
  made from `3fb0c0d`).

> Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
> delete it - if this session ends badly it is the only record of where things stood.
