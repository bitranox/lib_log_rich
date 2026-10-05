"""Provide the ``python -m lib_log_rich`` entry point.

Runs :func:`lib_log_rich.cli.main`, the function the console script runs, so
exit codes, traceback handling and ``.env`` loading are identical however the
CLI is started. :func:`main` stays importable as a thin delegate for console
scripts and callers that reference ``lib_log_rich.__main__:main``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import cli

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return its exit code.

    Args:
        argv: Optional argument list overriding ``sys.argv[1:]``.

    Returns:
        Process exit code produced by :func:`lib_log_rich.cli.main`.
    """
    return cli.main(argv)


if __name__ == "__main__":
    raise SystemExit(cli.main())
