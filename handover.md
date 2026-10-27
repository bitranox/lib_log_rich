# STALE - read 2026-10-27, work continued

## In flight

The user asked: "check documentation and skill for correctness and completeness, then 1-4" (1-4 = the 8.0.2
release steps of the previous handover). Decisions taken with the user this session (quoted in
`EXECUTION-USER-REVIEW.md`, User decisions): fix 3 code defects before releasing; skip 8.0.2 on PyPI and release
8.0.3 only; traceback text cut keeps its tail.

ALL work below is UNCOMMITTED in this checkout (36+ files):
- Code: `src/lib_log_rich/application/use_cases/_payload_sanitizer.py` (exc_info formatted via
  traceback.format_exception; frame-only compaction with per-run markers; strict frame regex `File ".*", line N, in `;
  repeat lines belong to their frame; malformed tuple and (None, None, None) handled; tail cut with
  `TRUNCATION_PREFIX`), `src/lib_log_rich/adapters/_schemas.py` (`_hex_undecodable_bytes`: only non-UTF-8 bytes
  become hex in JSON dumps).
- Tests: new `tests/application/test_traceback_text.py`, `tests/runtime/test_exc_info_reaches_the_dump.py`; edited
  `tests/adapters/test_dump_adapter.py`, `tests/application/test_use_cases.py`,
  `tests/application/test_process_event_limits.py` (five legacy tests rewritten to the frame/tail contract).
- Docs: README and all topic guides, `skills/python-logging/*`, `docs/systemdesign/*`, `config.example.toml`,
  `src/lib_log_rich/defaultconfig.toml`, `src/lib_log_rich/defaultconfig.d/90-logging.toml`, CHANGELOG
  ([Unreleased] Fixed + Changed; 8.0.2 entry says "Not published to PyPI").
- `OPEN-WORK.md`: new [100] (render exc_info in console/Graylog/journald/text dumps).

Remaining steps, in order:
1. Mirror the skill into bitranox-skills from a NEW worktree at origin/master (its main checkout is behind origin
   and another session is live there). In the worktree: copy the 5 files from
   `skills/python-logging/` to `plugins/bitranox/skills/coding-python-logging/`, set line 2 of SKILL.md to
   `name: coding-python-logging` (the only divergence), bump `plugins/bitranox/.claude-plugin/plugin.json` and
   `pyproject.toml` to 10.3.8 (origin is 10.3.7), add a CHANGELOG entry like 721953c7 does, commit, push to master,
   watch its CI (ci_wait --ref HEAD in that worktree).
2. Here: `env -u VIRTUAL_ENV make test`; read `git diff pyproject.toml` for bmk churn.
3. `make bump-patch` -> 8.0.3 (moves [Unreleased] into [8.0.3]); commit everything except handover.prev.md with
   `git commit -F` (message file in the scratchpad); push; ci_wait --ref HEAD.
4. `env -u VIRTUAL_ENV make release` (tags HEAD as v8.0.3), watch default_release_public.yml, then PyPI JSON +
   simple index show 8.0.3 with `Free Threading :: 3 - Stable`.
5. Commit OPEN-WORK.md / handover.md if still dirty.
- After all of it: `skill_receipt.py end meta-skill-writer` (a fixer subagent armed it:
  `bash <plugin>/hooks/run-python.sh <plugin>/hooks/skill_receipt.py end meta-skill-writer`).

## Decided, and why

- Version 8.0.3, not 8.0.2: origin/master's plugin.json already says 8.0.2, and installs re-fetch only on a version
  change, so skill fixes under 8.0.2 would never reach them.
- A string passed as the `exc_info=` ARGUMENT stays ignored; docs say text goes in `extra["exc_info"]` (no API
  widening in a patch).
- Accepted exotic residual: a note/message line copying the full frame shape still counts as a frame; CRLF text
  comes back LF after compaction. Logged in EXECUTION-USER-REVIEW.md.

## Decided against, and why

- Coercing every dump extra value through coerce_json_value: it changed Enum/dataclass/datetime/timedelta output
  (reviewer measured); replaced by bytes-only hex.

## Still open, untouched

- `OPEN-WORK.md` [100] (traceback rendering in sinks).
- default_cicd_public OPEN-WORK [40] push go; contrib_queue guard-hook decision (unchanged from last session).

## Lessons for the next nap

- When running lib_log_rich's pytest by hand, unset FORCE_COLOR and NO_COLOR: NO_COLOR=1 fails 14 colour tests
  (already captured as reference-no-color-1-reddens-lib-log-rich-s-colour-tests).
- When briefing doc fixers on a behaviour that is not yet in src, verify each claimed call path first: I told them
  a string `exc_info=` argument is kept, and only `extra["exc_info"]` is.
- When a fix coerces values for one failing type, pin the output of the other common types before shipping: the
  broad coerce silently changed five types and the suite stayed green.
- tooling: a fixer subagent armed a meta-skill-writer receipt for the whole session; the main session must end it.

## Next action

Step 1 above (bitranox-skills worktree mirror + 10.3.8), then steps 2-5.

## How to verify

- `env -u VIRTUAL_ENV -u FORCE_COLOR -u NO_COLOR .venv/bin/python -m pytest -q tests` (quick), `env -u VIRTUAL_ENV make test` (gate of record).
- `python3 plugins/bitranox/hooks/repo-gate.py --mirrors` in bitranox-skills -> coding-python-logging in sync.
- After release: `curl -s https://pypi.org/pypi/lib_log_rich/json` -> info.version 8.0.3.

Read this, then replace the first line with `# STALE - read <date>, work continued`. Do not delete it - if this
session ends badly it is the only record of where things stood.
