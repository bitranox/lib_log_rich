# Development

## Make Targets

| Target            | Description                                                                                |
|-------------------|--------------------------------------------------------------------------------------------|
| `help`            | Show help                                                                                  |
| `install`         | Install package editable                                                                   |
| `dev`             | Install package with dev extras                                                            |
| `test`            | Lint, type-check, run tests with coverage, upload to Codecov                               |
| `run`             | Run module CLI (requires dev install or src on PYTHONPATH)                                 |
| `version-current` | Print current version from pyproject.toml                                                  |
| `bump`            | Bump version (updates pyproject.toml and CHANGELOG.md)                                     |
| `bump-patch`      | Bump patch version (X.Y.Z -> X.Y.(Z+1))                                                    |
| `bump-minor`      | Bump minor version (X.Y.Z -> X.(Y+1).0)                                                    |
| `bump-major`      | Bump major version ((X+1).0.0)                                                             |
| `clean`           | Remove caches, build artifacts, and coverage                                               |
| `push`            | Run tests, prompt for/accept a commit message, create (allow-empty) commit, push to remote |
| `build`           | Build wheel/sdist and attempt conda, brew, and nix builds (auto-installs tools if missing) |
| `menu`            | Interactive TUI to run targets and edit parameters (requires dev dep: textual)             |

### Target Parameters (env vars)

- **Global**
  - `PY` (default: `python3`) — interpreter used to run scripts
  - `PIP` (default: `pip`) — pip executable used by bootstrap/install

- **install**
  - No specific parameters (respects `PY`, `PIP`).

- **dev**
  - No specific parameters (respects `PY`, `PIP`).

- **test**
  - `COVERAGE=on|auto|off` (default: `on`) — controls pytest coverage run and Codecov upload
  - `SKIP_BOOTSTRAP=1` — skip auto-install of dev tools if missing
  - `TEST_VERBOSE=1` — echo each command executed by the test harness
  - Also respects `CODECOV_TOKEN` when uploading to Codecov

- **run**
  - No parameters via `make` (always shows `--help`). For custom args: `python scripts/run_cli.py -- <args>`.

- **version-current**
  - No parameters

- **bump**
  - `VERSION=X.Y.Z` — explicit target version
  - `PART=major|minor|patch` — semantic part to bump (default if `VERSION` not set: `patch`)

- **bump-patch** / **bump-minor** / **bump-major**
  - No parameters; shorthand for `make bump PART=...`

- **clean**
  - No parameters

- **push**
  - `REMOTE=<name>` (default: `origin`) — git remote to push to
  - `COMMIT_MESSAGE="..."` — optional commit message used by the automation; if unset, the target prompts (or uses the default `chore: update` when non-interactive).

- **build**
  - No parameters via `make`. Advanced: call the script directly, e.g. `python scripts/build.py --no-conda --no-nix`.

- **release**
  - `REMOTE=<name>` (default: `origin`) — git remote to push to
  - Advanced (via script): `python scripts/release.py --retries 5 --retry-wait 3.0`

## Interactive Menu (Textual)

`make menu` launches a Textual-powered TUI to browse targets, edit parameters, and run them with live output.

Install dev extras if you haven’t:

```bash
pip install -e .[dev]
```

Run the menu:

```bash
make menu
```

### Target Details

- `test`: single entry point for local CI — runs ruff lint + format check, import-linter, pyright, pip-audit, pytest (including doctests) with coverage (enabled by default), and uploads coverage to Codecov if configured (reads `.env`).
  - Auto-bootstrap: `make test` will try to install dev tools (`pip install -e .[dev]`) if `ruff`/`pyright`/`pytest` are missing. Set `SKIP_BOOTSTRAP=1` to skip this behavior.
- `build`: creates wheel/sdist and ensures build backends are available.
- `version-current`: prints current version from `pyproject.toml`.
- `bump`: updates `pyproject.toml` version and appends a section in `CHANGELOG.md`. Use `VERSION=X.Y.Z make bump` or `make bump-minor`/`bump-major`/`bump-patch`.
- Additional scripts (`pipx-*`, `uv-*`, `which-cmd`, `verify-install`) provide install/run diagnostics.

## Development Workflow

```bash
make test                 # ruff + pyright + pytest + coverage (default ON)
SKIP_BOOTSTRAP=1 make test  # skip auto-install of dev deps
COVERAGE=off make test       # disable coverage locally
COVERAGE=on make test        # force coverage and generate coverage.xml/codecov.xml

**Automation notes**

- `make test` expects the `codecovcli` binary (installed via `pip install -e .[dev]`) and `pip-audit`. When `CODECOV_TOKEN` is not configured and the run is outside CI, the harness skips the upload instead of mutating git history. Set `SKIP_PIP_AUDIT=1` to bypass dependency scanning temporarily (for example when offline), but ensure it runs before committing.

- The harness auto-suppresses GHSA-4xh5-x5gv-qwph (pip) while we wait for the upstream patch. Export `PIP_AUDIT_IGNORE` to add/remove IDs explicitly and drop it once the dependency is fixed.
Set `PIP_AUDIT_IGNORE=ID1,ID2` when you must temporarily suppress a known advisory (for example when waiting on an upstream pip release).
Remove the variable as soon as the dependency ships a fix so CI regains full coverage.
- `make push` prompts for a commit message (or accepts `COMMIT_MESSAGE="..."`) and always performs a commit—even when nothing is staged—before pushing.
```

### Versioning & Metadata

- Single source of truth for package metadata is `pyproject.toml` (`[project]`).
- `make` automation mirrors those fields into `src/lib_log_rich/__init__conf__.py`; the module exposes read-only constants consumed by the CLI and docs.
- Do not edit `__init__conf__.py` manually—bump `pyproject.toml`, update `CHANGELOG.md`, then run the relevant script (`make bump`, `make push`, etc.) to regenerate it.
- Console script name is discovered from entry points; defaults to `lib_log_rich`.

### CI & Publishing

GitHub Actions workflows:

- `.github/workflows/ci.yml` — lint/type/test, build wheel/sdist, and verify pipx/uv installs.
- `.github/workflows/release.yml` — on tags `v*.*.*`, builds artifacts and publishes to PyPI when `PYPI_API_TOKEN` is configured.

Release checklist:

1. Bump `pyproject.toml` version and update `CHANGELOG.md`.
2. Tag the commit (`git tag vX.Y.Z && git push --tags`).
3. Ensure `PYPI_API_TOKEN` secret is configured.
4. Let CI publish artifacts to PyPI.

### Local Codecov uploads

- `make test` (coverage enabled) produces `coverage.xml` and `codecov.xml`, deletes intermediate `.coverage*` SQLite shards, then invokes `codecovcli upload-coverage` when a token or CI environment is present.
- For private repos, set `CODECOV_TOKEN` (see `.env.example`) or export it in your shell.
- Public repos typically do not require a token, but the CLI still expects a git commit to exist so run inside a repository with at least one commit.
- If the CLI is missing or configuration is incomplete, the harness emits a warning and skips the upload without creating commits or modifying git state.

## Refactor Backlog

- [ ] Extract helper modules from `src/lib_log_rich/application/use_cases/process_event.py` (context refresh, queue fan-out, payload diagnostics) to reduce module size and keep single-purpose files. Track progress in upcoming iterations before adding new adapters.
