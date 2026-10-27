# Contributing Guide

Thanks for helping improve **lib_log_rich**. The sections below summarise the day-to-day workflow, highlight the repository automation, and list the checks that must pass before a change is merged.

## 1. Workflow Overview

1. Fork and branch - use short, imperative branch names (`feature/cli-extension`, `fix/codecov-token`).
2. Make focused commits - keep unrelated refactors out of the same change.
3. Run `make test` locally before pushing (see the automation note below).
4. Update documentation and changelog entries that are affected by the change.
5. Open a pull request referencing any relevant issues.

## 2. Commits & Pushes

- Commit messages should be imperative (`Add rich handler`, `Fix CLI exit codes`).
- `make push` runs `make test`, then stages everything (`git add -A`), commits and pushes. Pass the message as `make push MSG="..."` (or set `BMK_COMMIT_MESSAGE`); `MSG=` is safe for any punctuation. Run interactively without one, it prompts; otherwise it uses `chores`. With nothing staged it creates an empty commit.

## 3. Coding Standards

- Apply the repository's Clean Architecture / SOLID rules (see `CLAUDE.md` for architecture overview and coding guidelines).
- Prefer small, single-purpose modules and functions; avoid mixing orthogonal concerns.
- Free functions and modules use `snake_case`; classes are `PascalCase`.
- Keep runtime dependencies minimal. Use the standard library where practical.

## 4. Tests & Tooling

- `make test` provisions `.venv` with uv from `uv.lock` and runs the full gate; [DEVELOPMENT.md](DEVELOPMENT.md) lists its stages, and `make help` lists every target.
- `make test` writes `coverage.xml`; `make codecov` uploads it. For private repositories set `CODECOV_TOKEN` in your environment or `.env`.

## 5. Documentation Checklist

Before opening a PR, confirm the following:

- [ ] `make test` passes locally.
- [ ] Relevant documentation (`README.md`, `DEVELOPMENT.md`, `docs/systemdesign/*`) is updated.
- [ ] No generated artefacts or virtual environments are committed.
- [ ] Version bumps, when required, go through `make bump-*` (it updates `pyproject.toml`, `.claude-plugin/plugin.json`, `__init__conf__.py` and `uv.lock`, and renames the `CHANGELOG.md` `[Unreleased]` section); never edit `__init__conf__.py` by hand.

## 6. Security & Configuration

- Never commit secrets. Tokens (Codecov, PyPI) belong in `.env` (ignored by git) or CI secrets.
- Do not log secrets. Add sensitive key patterns to `scrub_patterns` (env `LOG_SCRUB_PATTERNS`) rather than redacting at call sites.

Happy hacking!
