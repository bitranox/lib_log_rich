# Open work

Standing backlog. Edit line by line, never rewrite wholesale. When an item is finished, delete
its line; the reason it closed belongs in the commit message, CHANGELOG or git history.


- [ ] (2026-10-19) [10] FOUND: drop the ruff timestamp floor pin once ruff 0.17.1 is older than the 7-day cooldown | size: one pyproject line | open: `[tool.uv] exclude-newer-package` holds `ruff = "2026-10-15T15:23:19Z"`, which bmk added to keep `ruff>=0.17.1` resolvable under the 7-day cooldown; a timestamp REPLACES the default window for ruff, so left in place it holds ruff back for good; bmk reconcile_floor_pins is expected to remove it itself | next: on the first `make test` on or after 2026-10-23, `grep -n ruff pyproject.toml`; if the ruff entry is still under exclude-newer-package, delete it, relock with make test, commit
