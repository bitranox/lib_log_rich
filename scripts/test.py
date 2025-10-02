from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Callable

import click

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._utils import (  # noqa: E402
    RunResult,
    bootstrap_dev,
    cmd_exists as _cmd_exists,
    get_project_metadata,
    run,
    sync_packaging,
)

cmd_exists = _cmd_exists

PROJECT = get_project_metadata()
COVERAGE_TARGET = PROJECT.coverage_source
_TOML_MODULE: ModuleType | None = None
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TRUTHY = {"1", "true", "yes", "on"}


def _build_default_env() -> dict[str, str]:
    """Return the base environment for subprocess execution."""
    pythonpath = os.pathsep.join(filter(None, [str(PROJECT_ROOT / "src"), os.environ.get("PYTHONPATH")]))
    return os.environ | {"PYTHONPATH": pythonpath}


DEFAULT_ENV = _build_default_env()


def _refresh_default_env() -> None:
    """Recompute DEFAULT_ENV after environment mutations."""
    global DEFAULT_ENV
    DEFAULT_ENV = _build_default_env()


@click.command(help="Run lints, type-check, tests with coverage, and Codecov upload if configured")
@click.option("--coverage", type=click.Choice(["on", "auto", "off"]), default="on")
@click.option("--verbose", "-v", is_flag=True, help="Print executed commands before running them")
def main(coverage: str, verbose: bool) -> None:
    env_verbose = os.getenv("TEST_VERBOSE", "").lower()
    if not verbose and env_verbose in _TRUTHY:
        verbose = True

    def _run(
        cmd: list[str] | str,
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        capture: bool = True,
        label: str | None = None,
    ) -> RunResult:
        display = cmd if isinstance(cmd, str) else " ".join(cmd)
        if label and not verbose:
            click.echo(f"[{label}] $ {display}")
        if verbose:
            click.echo(f"  $ {display}")
            if env:
                overrides = {k: v for k, v in env.items() if os.environ.get(k) != v}
                if overrides:
                    env_view = " ".join(f"{k}={v}" for k, v in overrides.items())
                    click.echo(f"    env {env_view}")
        merged_env = DEFAULT_ENV if env is None else DEFAULT_ENV | env
        result = run(cmd, env=merged_env, check=check, capture=capture)  # type: ignore[arg-type]
        if verbose and label:
            click.echo(f"    -> {label}: exit={result.code} out={bool(result.out)} err={bool(result.err)}")
        return result

    bootstrap_dev()

    skip_packaging_sync = os.getenv("SKIP_PACKAGING_SYNC", "1").strip().lower() in _TRUTHY

    steps: list[tuple[str, Callable[[], None]]] = []
    if skip_packaging_sync:
        click.echo("[skip] Packaging sync disabled (set SKIP_PACKAGING_SYNC=0 to enable)")
    else:
        steps.append(("Sync packaging (conda/brew/nix) with pyproject", sync_packaging))

    format_strict = os.getenv("STRICT_RUFF_FORMAT", "0").strip().lower() in _TRUTHY

    steps.extend(
        [
            (
                "Ruff lint",
                partial(_run, ["ruff", "check", "."], label="ruff-check"),
            ),
            (
                "Ruff format check" if format_strict else "Ruff format (apply)",
                partial(_run, ["ruff", "format", "--check", "."] if format_strict else ["ruff", "format", "."], label="ruff-format"),
            ),
            (
                "Import-linter contracts",
                partial(_run, [sys.executable, "-m", "lint_imports", "--config", "pyproject.toml"], label="import-linter", check=False),
            ),
            (
                "Pyright type-check",
                partial(_run, ["pyright"], label="pyright", check=False),
            ),
        ]
    )

    def _run_pytest() -> None:
        for f in (".coverage", "coverage.xml"):
            try:
                Path(f).unlink()
            except FileNotFoundError:
                pass

        if coverage == "on" or (coverage == "auto" and (os.getenv("CI") or os.getenv("CODECOV_TOKEN"))):
            click.echo("[coverage] enabled")
            fail_under = _read_fail_under(Path("pyproject.toml"))
            with tempfile.TemporaryDirectory() as tmp:
                cov_file = Path(tmp) / ".coverage"
                click.echo(f"[coverage] file={cov_file}")
                env = os.environ | {"COVERAGE_FILE": str(cov_file)}
                pytest_result = _run(
                    [
                        "python",
                        "-m",
                        "pytest",
                        f"--cov={COVERAGE_TARGET}",
                        "--cov-report=xml:coverage.xml",
                        "--cov-report=term-missing",
                        f"--cov-fail-under={fail_under}",
                        "-vv",
                    ],
                    env=env,
                    capture=False,
                    label="pytest",
                )
                if pytest_result.code != 0:
                    click.echo("[pytest] failed; skipping Codecov upload", err=True)
                    raise SystemExit(pytest_result.code)
        else:
            click.echo("[coverage] disabled (set --coverage=on to force)")
            pytest_result = _run(
                ["python", "-m", "pytest", "-vv"],
                capture=False,
                label="pytest-no-cov",
            )
            if pytest_result.code != 0:
                click.echo("[pytest] failed; skipping Codecov upload", err=True)
                raise SystemExit(pytest_result.code)

    pytest_label = "Pytest with coverage" if coverage != "off" else "Pytest"
    steps.append((pytest_label, _run_pytest))

    total = len(steps)
    for index, (description, action) in enumerate(steps, start=1):
        click.echo(f"[{index}/{total}] {description}")
        action()

    _ensure_codecov_token()

    if Path("coverage.xml").exists():
        _prune_coverage_data_files()
        uploaded = _upload_coverage_report(run_command=_run)
        if uploaded:
            click.echo("All checks passed (coverage uploaded)")
        else:
            click.echo("Checks finished (coverage upload skipped or failed)")
    else:
        click.echo("Checks finished (coverage.xml missing, upload skipped)")


def _get_toml_module() -> ModuleType:
    global _TOML_MODULE
    if _TOML_MODULE is not None:
        return _TOML_MODULE

    try:
        import tomllib as module  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        try:
            import tomli as module  # type: ignore[import-not-found, assignment]
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError("tomllib/tomli modules are unavailable. Install the 'tomli' package for Python < 3.11.") from exc

    _TOML_MODULE = module
    return module


def _read_fail_under(pyproject: Path) -> int:
    try:
        toml_module = _get_toml_module()
        data = toml_module.loads(pyproject.read_text())
        return int(data["tool"]["coverage"]["report"]["fail_under"])
    except Exception:
        return 80


def _upload_coverage_report(*, run_command: Callable[..., RunResult]) -> bool:
    """Upload ``coverage.xml`` via the official Codecov CLI when available."""

    if not Path("coverage.xml").is_file():
        return False

    if not os.getenv("CODECOV_TOKEN") and not os.getenv("CI"):
        click.echo("[codecov] CODECOV_TOKEN not configured; skipping upload (set CODECOV_TOKEN or run in CI)")
        return False

    uploader = shutil.which("codecovcli")
    if uploader is None:
        click.echo(
            "[codecov] 'codecovcli' not found; install with 'pip install codecov-cli' to enable uploads",
            err=True,
        )
        return False

    commit_sha = _resolve_commit_sha()
    if commit_sha is None:
        click.echo("[codecov] Unable to resolve git commit; skipping upload", err=True)
        return False

    branch = _resolve_git_branch()
    label = "codecov-upload"
    args = [
        uploader,
        "upload-coverage",
        "--file",
        "coverage.xml",
        "--disable-search",
        "--fail-on-error",
        "--sha",
        commit_sha,
        "--name",
        f"local-{platform.system()}-{platform.python_version()}",
        "--flag",
        "local",
    ]
    if branch:
        args.extend(["--branch", branch])

    env_overrides = {"CODECOV_NO_COMBINE": "1"}
    result = run_command(args, env=env_overrides, check=False, capture=False, label=label)
    if result.code == 0:
        click.echo("[codecov] upload succeeded")
        return True

    click.echo(f"[codecov] upload failed (exit {result.code})", err=True)
    return False


def _resolve_commit_sha() -> str | None:
    sha = os.getenv("GITHUB_SHA")
    if sha:
        return sha.strip()
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    candidate = proc.stdout.strip()
    return candidate or None


def _resolve_git_branch() -> str | None:
    branch = os.getenv("GITHUB_REF_NAME")
    if branch:
        return branch.strip()
    proc = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    candidate = proc.stdout.strip()
    if candidate in {"", "HEAD"}:
        return None
    return candidate


def _ensure_codecov_token() -> None:
    if os.getenv("CODECOV_TOKEN"):
        _refresh_default_env()
        return
    env_path = Path(".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "CODECOV_TOKEN":
            token = value.strip().strip("\"'")
            if token:
                os.environ.setdefault("CODECOV_TOKEN", token)
                _refresh_default_env()
            break


def _prune_coverage_data_files() -> None:
    """Delete SQLite coverage data shards to keep the Codecov CLI simple."""

    for path in Path.cwd().glob(".coverage*"):
        # keep the primary XML report and directories untouched
        if path.is_dir() or path.suffix == ".xml":
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            click.echo(f"[coverage] warning: unable to remove {path}: {exc}", err=True)


if __name__ == "__main__":
    main()
