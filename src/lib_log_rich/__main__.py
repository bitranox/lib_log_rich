"""Module entry point aligning ``python -m lib_log_rich`` with the CLI."""

from __future__ import annotations

import os
from typing import Final, Sequence

import lib_cli_exit_tools

from . import cli as cli_module
from . import config as config_module

cli = cli_module.cli

_TRACEBACK_SUMMARY_LIMIT: Final[int] = 500
_TRACEBACK_VERBOSE_LIMIT: Final[int] = 10_000


def _extract_dotenv_flag(argv: Sequence[str] | None) -> bool | None:
    """Return the last explicit ``--use-dotenv`` flag if present."""

    if not argv:
        return None
    flag: bool | None = None
    for token in argv:
        if token == "--use-dotenv":
            flag = True
        elif token == "--no-use-dotenv":
            flag = False
    return flag


def _maybe_enable_dotenv(argv: Sequence[str] | None) -> None:
    """Load ``.env`` entries when CLI flags or environment request it."""

    explicit = _extract_dotenv_flag(argv)
    env_toggle = os.getenv(config_module.DOTENV_ENV_VAR)
    if config_module.should_use_dotenv(explicit=explicit, env_value=env_toggle):
        config_module.enable_dotenv()


def _module_main(argv: Sequence[str] | None = None) -> int:
    """Execute the CLI while preserving traceback configuration."""
    _maybe_enable_dotenv(argv)
    previous_traceback = getattr(lib_cli_exit_tools.config, "traceback", False)
    previous_force_color = getattr(lib_cli_exit_tools.config, "traceback_force_color", False)
    try:
        try:
            return int(cli_module.main(argv=argv, restore_traceback=False))
        except BaseException as exc:  # noqa: BLE001
            lib_cli_exit_tools.print_exception_message(
                trace_back=lib_cli_exit_tools.config.traceback,
                length_limit=(_TRACEBACK_VERBOSE_LIMIT if lib_cli_exit_tools.config.traceback else _TRACEBACK_SUMMARY_LIMIT),
            )
            return lib_cli_exit_tools.get_system_exit_code(exc)
    finally:
        lib_cli_exit_tools.config.traceback = previous_traceback
        lib_cli_exit_tools.config.traceback_force_color = previous_force_color


def main(argv: Sequence[str] | None = None) -> int:
    """Public entry point used by the console script declaration."""

    return _module_main(argv)


if __name__ == "__main__":
    raise SystemExit(_module_main())
