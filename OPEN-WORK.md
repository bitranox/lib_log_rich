# Open work

Standing backlog. Edit line by line, never rewrite wholesale. When an item is finished, delete
its line; the reason it closed belongs in the commit message, CHANGELOG or git history.

- [ ] (2026-10-28) [110] FOUND: journald field keys are neither validated nor escaped - a caller `extra` / `bind(extra=...)` key containing `=` with a multi-line value makes journald drop the whole event (measured by the 8.1.0 re-review against the real journal: 0 entries), with a single-line value it spoofs a field (`A=B` becomes field `A`), and a key with `-` or a newline loses that field | size: small; one key check in `adapters/structured/journald.py` (`[A-Z0-9_]`, not leading `_`) plus a rule for what happens to a refused key, tests, docs | open: predates 8.1.0 (the old encoder had the same gap); first seen here | next: decide whether a refused key is renamed (e.g. invalid chars to `_`) or dropped with a diagnostic, then RED test with the `A=B` multi-line case

