# Handover - lib_log_rich after the 8.1.0 release

## In flight

Nothing. 8.1.0 (OPEN-WORK [100], tracebacks in every sink) is committed, pushed, tagged `v8.1.0` and released; the
skill twin is mirrored in bitranox-skills 10.3.18.

## Committed, or not

Everything for 8.1.0 is committed and pushed. This file is committed after the release, so `v8.1.0` points at the
commit before it.

## Decided, and why

- Tracebacks show in every sink with no setting to turn them off, and journald gets them in MESSAGE plus
  EXCEPTION_TEXT / STACK_TEXT: both user decisions, quoted verbatim under User decisions in `EXECUTION-USER-REVIEW.md`
  (gitignored).
- Console and text dumps indent the block four spaces; the HTML table puts it in the message cell as an escaped
  `<pre>` so the columns stay fixed. No `{exc_info}` template token: stdlib appends the text after the line too.
- The scrubber does not touch traceback text, as it does not touch the message; said in CHANGELOG and README.
- Review residuals accepted, each with its reason in `EXECUTION-USER-REVIEW.md` (2026-10-28 [100] section):
  `stacklevel` ignored by `stack_info=True`, a caller file inside the package dir losing frames, a `<string>` frame
  with the cwd in the package dir, `extra={"stack_info": True}` stored as text, and a GELF datagram over the UDP
  limit with a very large `extra_max_value_chars`.

## Decided against, and why

- Unwrapping long traceback lines on a console with a width: Rich wraps every long line there, messages included;
  not new in 8.1.0.

## Still open, untouched

- OPEN-WORK [110]: journald field keys are neither validated nor escaped (predates 8.1.0).

## Lessons for the next nap

- When pushing to the bitranox-skills marketplace while other sessions are active, expect the version you picked to
  be taken during the five-minute pre-push suite: fetch right before the push, and on a reject rebase, renumber
  plugin.json, pyproject and the CHANGELOG heading, and push again (two rejects in a row on 2026-10-28).

## Next action

OPEN-WORK [110]: decide whether a journald field key with invalid characters is renamed (invalid characters to `_`)
or dropped with a diagnostic, then write the RED test with the `A=B` multi-line case in
`tests/adapters/test_traceback_rendering.py` or a new journald test module.

## How to verify

- `env -u VIRTUAL_ENV -u FORCE_COLOR -u NO_COLOR make test` (gate of record).
- CI on HEAD: `uv run <bitranox plugin>/skills/compuse-toolbox/scripts/ci_wait.py --ref HEAD --repo bitranox/lib_log_rich`.
- 8.1.0 on PyPI: `curl -s https://pypi.org/pypi/lib_log_rich/json | jq -r .info.version` and the simple index.
