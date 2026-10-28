# Handover - lib_log_rich after the 8.1.1 release

## In flight

Nothing. 8.1.1 (backlog item [110], journald field names) is committed, pushed, tagged `v8.1.1` and released; the
skill twin is mirrored in bitranox-skills 10.3.21.

## Committed, or not

Everything is committed and pushed. This file is committed after the release, so `v8.1.1` points at the commit
before it.

## Decided, and why

- 8.1.0: tracebacks show in every sink, journald gets them in MESSAGE plus EXCEPTION_TEXT / STACK_TEXT (user
  decisions, verbatim in `EXECUTION-USER-REVIEW.md`, gitignored).
- No setting to turn traceback rendering off (user decision 2026-10-28, "No setting"): the call site already decides
  (`logger.error` or `exc_info=False` prints no traceback, measured through a real runtime), as in stdlib.
- 8.1.1: a journald `extra` key journald cannot take as a field name is renamed, not dropped (user decision
  2026-10-28, "Rename to a valid name"): other characters become `_`, a leading digit or `_` gets `EXTRA_`, names are
  cut to 64; a clash adds `EXTRA_` while taken, then a numeric suffix. Measured on the real journal before and after.

## Decided against, and why

- Unwrapping long traceback lines on a console with a width: Rich wraps every long line there; not new.

## Still open, untouched

- `OPEN-WORK.md` has no open item.

## Lessons for the next nap

- When a journald field name comes from caller data, measure the real journal's answer per case: an invalid name
  loses only that field, `A=B` spoofs field `A`, and `A=B` with a multi-line value drops the entry depending on the
  value's length bytes (a 3-byte value survived, 38- and 203-byte values did not).
- When auto mode refuses `make release` as public surface, ask once in this conversation with AskUserQuestion; the
  release then runs.

## Next action

None queued. Ask the user what to work on next.

## How to verify

- `env -u VIRTUAL_ENV -u FORCE_COLOR -u NO_COLOR make test` (gate of record).
- CI on HEAD: `uv run <bitranox plugin>/skills/compuse-toolbox/scripts/ci_wait.py --ref HEAD --repo bitranox/lib_log_rich`.
- 8.1.1 on PyPI: `curl -s https://pypi.org/pypi/lib_log_rich/json | jq -r .info.version` and the simple index.
