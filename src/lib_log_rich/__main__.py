"""Module entry point wiring ``python -m lib_log_rich`` through the CLI.

Purpose
-------
Keep module execution aligned with the console-script entry point while
respecting ``lib_cli_exit_tools``' session lifecycle. The helper mirrors the
traceback budgeting used by :mod:`lib_log_rich.cli` and honours the ``.env``
loading toggle before delegating to the shared CLI runner.

Contents
--------
* :func:`_open_cli_session` – configures the managed CLI session.
* :func:`_command_to_run` / :func:`_command_name` – expose the Click command and
  program label.
* :func:`_module_main` – prepares environment opts and executes the CLI.

System Role
-----------
Lives in the presentation adapter layer, bridging CPython's ``-m`` execution to
the richer CLI while leaning on ``lib_cli_exit_tools`` to manage traceback
state.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from typing import Final, cast

import rich_click as click
from lib_cli_exit_tools import cli_session

from . import __init__conf__
from . import cli as cli_module
from . import config as config_module

CommandRunner = Callable[..., int]


# Match the CLI defaults so the traceback behaviour stays consistent for module
# execution and the console script.
TRACEBACK_SUMMARY_LIMIT: Final[int] = cli_module.TRACEBACK_SUMMARY_LIMIT
TRACEBACK_VERBOSE_LIMIT: Final[int] = cli_module.TRACEBACK_VERBOSE_LIMIT

_DOTENV_ENABLE_FLAG: Final[str] = "--use-dotenv"  # CLI toggle, not a credential
_DOTENV_DISABLE_FLAG: Final[str] = "--no-use-dotenv"  # CLI toggle, not a credential


def _open_cli_session() -> AbstractContextManager[CommandRunner]:
    """Return a ``cli_session`` configured with the project's traceback limits."""
    return cast(
        AbstractContextManager[CommandRunner],
        cli_session(
            summary_limit=TRACEBACK_SUMMARY_LIMIT,
            verbose_limit=TRACEBACK_VERBOSE_LIMIT,
        ),
    )


def _command_to_run() -> click.Command:
    """Expose the Click command executed by the module entry point."""
    return cli_module.cli


def _command_name() -> str:
    """Return the program label used when invoking the CLI through ``python -m``."""
    return __init__conf__.shell_command


def _extract_dotenv_flag(argv: Sequence[str] | None) -> bool | None:
    """Return the last explicit ``--use-dotenv`` flag if present."""
    if not argv:
        return None
    flag: bool | None = None
    for token in argv:
        if token == _DOTENV_ENABLE_FLAG:
            flag = True
        elif token == _DOTENV_DISABLE_FLAG:
            flag = False
    return flag


def _maybe_enable_dotenv(argv: Sequence[str] | None) -> None:
    """Load ``.env`` entries when CLI flags or environment request it."""
    explicit = _extract_dotenv_flag(argv)
    env_toggle = os.getenv(config_module.DOTENV_ENV_VAR)
    if config_module.should_use_dotenv(explicit=explicit, env_value=env_toggle):
        config_module.enable_dotenv()


def _module_main(argv: Sequence[str] | None = None) -> int:
    """Execute the CLI while delegating traceback handling to ``cli_session``."""
    _maybe_enable_dotenv(argv)
    with _open_cli_session() as run:
        result = run(
            _command_to_run(),
            argv=list(argv) if argv is not None else None,
            prog_name=_command_name(),
        )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Public entry point used by the console script declaration."""
    return _module_main(argv)


if __name__ == "__main__":
    raise SystemExit(_module_main())
