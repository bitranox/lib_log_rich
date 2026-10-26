# Open work

Standing backlog. Edit line by line, never rewrite wholesale. When an item is finished, delete
its line; the reason it closed belongs in the commit message, CHANGELOG or git history.


- [ ] (2026-10-26) [25] FOUND: raise the "Free Threading :: 2 - Beta" classifier to "3 - Stable" once the free-threaded CI cells (3.14t and 3.15t) stay green | size: one pyproject line plus a CHANGELOG entry | open: 7.2.0 shipped free-threaded 3.15 support, 7.3.0 adds 3.14t; the 3.15t cells had two green runs on 2026-10-26 after the QueueAdapter.stop() race fix; the 3.14t cells failed on 9ea1bed (a test race, fixed in 1632544) and had their first green run on 1632544 (ubuntu and windows, 2026-10-26) | next: after several green free-threaded runs (scheduled CI included), `gh run list --workflow default_cicd_public.yml --json conclusion,headSha` and the 3.14t/3.15t job results; if all green, change the classifier and ship it with the next release
- [ ] (2026-10-22) [30] FOUND: drop the certifi timestamp pin once certifi 2026.10.19 is older than the 7-day cooldown | size: one pyproject line | open: commit 8b342ed added `certifi = "2026-10-20T02:24:17Z"` to `[tool.uv] exclude-newer-package` beside the `certifi>=2026.10.19` dev floor (PYSEC-2026-4320); like the ruff pin in [10], a timestamp REPLACES the default window for certifi, so left in place it holds certifi back for good; first seen here | next: on the first `make test` on or after 2026-10-28, `grep -n certifi pyproject.toml`; if the certifi entry is still under exclude-newer-package, delete it, relock with make test, commit
