# Handover - lib_log_rich 8.0.3 released

## In flight

Nothing. 8.0.3 is on PyPI (doc/skill audit, exc_info as traceback text, frame compaction, tail cut, hex bytes in
JSON dumps). The skill twin `coding-python-logging` shipped in bitranox-skills 10.3.8. 8.0.2 was never published
to PyPI; its CHANGELOG entry says so.

## Decided, and why

- A string passed as the `exc_info=` ARGUMENT stays ignored; ready-made text goes in `extra["exc_info"]`, which
  moves into the event's `exc_info` field (no API widening in a patch).
- Accepted exotic residuals: a message line copying the full frame shape counts as a frame; CRLF traceback text
  comes back LF after compaction.

## Decided against, and why

- Coercing every dump extra value through `coerce_json_value`: it changed Enum/dataclass/datetime/timedelta
  output; only non-UTF-8 bytes become hex.

## Still open, untouched

- `OPEN-WORK.md` [100]: render exc_info/stack_info in the console, text/HTML dumps, Graylog and journald.

## Lessons for the next nap

- When mirroring a skill whose source repo change is uncommitted, commit the source repo first: the bitranox-skills
  repo-gate compares the twin's COMMITTED text, then refuses the source push until the mirror is on master.
- When a tracked handover.md goes to a public repo, run pushcheck first: it caught a private absolute path.

## Next action

`OPEN-WORK.md` [100], the only open item.

## How to verify

- `env -u VIRTUAL_ENV make test` (gate of record).
- `curl -s https://pypi.org/pypi/lib_log_rich/json` -> info.version 8.0.3 or later.
- In bitranox-skills: `python3 plugins/bitranox/hooks/repo-gate.py --mirrors` -> coding-python-logging in sync.
