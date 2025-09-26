"""Module entry point aligning ``python -m lib_log_rich`` with the CLI adapter.

Purpose
-------
Delegate module execution to :mod:`lib_log_rich.cli` while mirroring the error
handling conventions shared across bitranox CLI projects. The wrapper ensures
traceback preferences managed by :mod:`lib_cli_exit_tools` remain consistent
between console scripts and ``python -m`` runs.

System Role
-----------
Lives in the adapters layer. Translates CPython's module entry point into the
shared CLI helper and restores global traceback preferences once execution
completes.
"""

from __future__ import annotations

from typing import Final, Sequence

import lib_cli_exit_tools

from . import cli as cli_module

cli = cli_module.cli

_TRACEBACK_SUMMARY_LIMIT: Final[int] = 500
_TRACEBACK_VERBOSE_LIMIT: Final[int] = 10_000


def _module_main(argv: Sequence[str] | None = None) -> int:
    """Execute the CLI while preserving traceback configuration.

    Returns
    -------
    int
        Normalised exit code derived from the CLI execution.
    """

    previous_traceback = getattr(lib_cli_exit_tools.config, "traceback", False)
    previous_force_color = getattr(lib_cli_exit_tools.config, "traceback_force_color", False)
    try:
        try:
            return int(cli_module.main(argv=argv, restore_traceback=False))
        except BaseException as exc:  # noqa: BLE001 - funnel through shared exit helpers
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
