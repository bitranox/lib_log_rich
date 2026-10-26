# Open work

Standing backlog. Edit line by line, never rewrite wholesale. When an item is finished, delete
its line; the reason it closed belongs in the commit message, CHANGELOG or git history.


- [ ] (2026-10-22) [30] FOUND: drop the certifi timestamp pin once certifi 2026.10.19 is older than the 7-day cooldown | size: one pyproject line | open: commit 8b342ed added `certifi = "2026-10-20T02:24:17Z"` to `[tool.uv] exclude-newer-package` beside the `certifi>=2026.10.19` dev floor (PYSEC-2026-4320); like the ruff pin in [10], a timestamp REPLACES the default window for certifi, so left in place it holds certifi back for good; first seen here | next: on the first `make test` on or after 2026-10-28, `grep -n certifi pyproject.toml`; if the certifi entry is still under exclude-newer-package, delete it, relock with make test, commit
