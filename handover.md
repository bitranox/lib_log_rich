# Handover - 2026-10-26 16:10

## In flight

- Free-threaded 3.15 support (OPEN-WORK [15], now closed) is implemented and committed LOCAL ONLY
  as `cd8cb7f`. Shipping it is OPEN-WORK [25]; the user has not yet answered "ship now as 7.2.0?".
- Its skill twin is NOT committed: the one added install line in `SKILL.md` sits uncommitted in
  `.claude/worktrees/lib-log-rich-ft315-twin` of the bitranox-skills checkout
  (branch `lib-log-rich-ft315-twin`, off origin/master). The repo gate lets lib_log_rich commits
  through on that uncommitted text, but refuses a lib_log_rich PUSH until the twin is committed and
  pushed.
- The `skills` session (another Claude session) asked at ~15:59 to hold every bitranox-skills push
  until it messages again, expiring 20 minutes after the ask. I agreed and told it a 10.0.25 twin push
  will follow. Check `ListAgents` / its message before pushing there.
- No background agent or shell of this session is running.

## Committed, or not

- lib_log_rich master is ahead of origin by `cd8cb7f` and the commit carrying this handover; both
  local. Everything through `eb5b937` is pushed, with CI green.
- 7.1.0 is released (tag `v7.1.0`, publish run 40341640167 green via the OIDC Trusted Publisher,
  PyPI lists the wheel and sdist). Twin 10.0.24 (`cecf19ed`) is on bitranox-skills origin with CI
  green; its worktree is removed.
- Git-ignored: `EXECUTION-USER-REVIEW.md`.

## Decided, and why

- orjson packaging (user, 2026-10-26, option "Hard below 3.15, extra on 3.15"):
  `orjson>=3.13.0; python_version < "3.15"` plus an `orjson` extra. Measured: no installer marker
  separates a free-threaded build (uv 0.13 rejects `sys_abi_features` and `sys_abiflags`; PEP 780
  is Draft), and orjson's source build refuses free-threaded Python.
- `orjson` is also in the `dev` extra: bmk builds the dev venv on 3.15 (a GIL build), where the
  marker drops orjson and pyright could not resolve `import orjson`. Consequence: `.[dev]` cannot
  install on 3.15t; a free-threaded check installs `-e .` plus the dev list minus orjson.
- The codec never imports orjson on a free-threaded build, even if installed: an extension
  without free-threading support re-enables the GIL on import.
- The stdlib backend copies orjson for NaN/inf (null), datetime/date/time, UUID, Enum, so a
  checkpoint written on one build reads on the other. Left different on purpose: float exponent
  spelling (`1e-07`) and ints beyond 64 bits (orjson refuses them).
- [75]: a dump's theme replaces the runtime palette; configured styles win per level; per-call
  `console_styles` beats both. [65]: `ring_buffer_checkpoint` added. [87]: SHORT_LOC_ICON refused
  as a dump preset. All three shipped in 7.1.0.

## Decided against, and why

- Committing the twin in the bitranox-skills MAIN checkout: the `skills` session keeps unpushed
  commits there, and a push from it would publish theirs. Use an own worktree on origin/master and
  `git push origin HEAD:master`.
- Reading the merged (theme-filled) styles for dumps, and passing a new required field to
  `LoggingRuntime`: the latter would break ~10 test constructions for no gain (default `None`).

## Still open, untouched

- OPEN-WORK [25] ship 7.2.0, [28] free-threaded CI cell (a template change), [30] certifi pin
  (due 2026-10-28), [80] stress-test shutdown deadlock, [83], [90], [93], [95], [96], [97], [98].

## Lessons for the next nap

- When a python3 heredoc and a git command share one Bash call, put `&&` on the heredoc's opener
  line or split the calls: the git-semicolon guard blocked it twice in one session.
- When a guard refuses `cd /repo && make <target>` after a sibling call changed directory, use
  `make -C /repo <target>`.
- When a dependency gains a `python_version` marker, check the bmk dev venv's Python: it is the
  newest classifier, so the dependency vanishes from the venv and pyright fails to resolve it.
- When asserting installed requirement markers, parse with `packaging.requirements.Requirement`:
  installed metadata writes markers with single quotes.
- When running the suite in the order `tests src`, three doctests fail (OPEN-WORK [90]); use
  `src tests`, the order bmk uses, before blaming a change.
- When a skill twin must be pushed while another session has unpushed commits in the marketplace
  main checkout, commit in an own worktree on origin/master and push `HEAD:master`; fetch first and
  rebase if a peer pushed in between.
- tooling: `repo-gate.py --mirror-of` reports DRIFT for twin text that exists only uncommitted in a
  marketplace worktree, while the commit gate accepts that same text.

## Next action

The user asked while this was written: "did you put the 3.15t in the pyproj.toml ?" - answered: no,
only the 3.15 classifier is there. First ask whether to add a `Programming Language :: Python ::
Free Threading :: ...` classifier (and which level) before shipping, then the open OPEN-WORK [25]
question: ship the free-threaded support (`cd8cb7f`) now as 7.2.0? [25] is the top-ranked open item (a USER item); its line holds the full ship sequence. Then
ask [28] (file the free-threaded CI cell in default_cicd_public?).

## Files that matter

- `src/lib_log_rich/domain/json_codec.py`, `tests/domain/test_json_codec.py`
- `pyproject.toml` (orjson marker, `orjson` and `dev` extras), `CHANGELOG.md` (Unreleased)
- `OPEN-WORK.md`, `README.md` ("JSON encoder and free-threaded Python")

## How to verify

- Gate: `env -u VIRTUAL_ENV make -C <lib_log_rich> test` (result envelope `"result":"pass"`).
- Free-threaded: `uv venv -p 3.15t <scratch>`, then `uv pip install --python <scratch>/bin/python -e .`
  plus the `dev` list without orjson, then `<scratch>/bin/python -m pytest -q src tests`; check
  `sys._is_gil_enabled()` is False after `import lib_log_rich`.
- Twin parity once committed: `python3 <bitranox-skills>/plugins/bitranox/hooks/repo-gate.py --mirror-of <lib_log_rich>` prints `in sync`.
- CI after a push: `ci_wait.py --ref HEAD` from bitranox:compuse-toolbox.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not
delete it - if this session ends badly it is the only record of where things stood.
