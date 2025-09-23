"""Console entry point providing the documented metadata banner.

Purpose
-------
Expose a minimal CLI so packaging checks and smoke tests can execute
`python -m lib_log_rich` or the `lib_log_rich` console script while the Rich
logging backbone is under construction.

Contents
--------
* :func:`main` - parses optional switches and prints the metadata banner.

System Role
-----------
Serves the presentation layer in the placeholder architecture: it surfaces the
same metadata produced by :func:`lib_log_rich.summary_info` without introducing
new behavior. The CLI keeps legacy automation working while documentation and
integration tests transition to the richer logging features.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from .lib_log_rich import hello_world, summary_info


def main(argv: Sequence[str] | None = None) -> int:
    """Run a minimal CLI that prints the package metadata banner.

    Why
        Historical automation invokes ``python -m lib_log_rich`` or the
        ``lib_log_rich`` console script after installation. This entry point
        keeps that workflow viable while the library remains import-first.

    What
        Parses the ``--hello`` flag for parity with documentation examples,
        optionally emitting the canonical greeting before printing the metadata
        banner returned by :func:`summary_info`.

    Parameters
    ----------
    argv:
        Optional sequence of argument strings (defaults to ``sys.argv[1:]``).

    Returns
    -------
    int
        Zero on success so packaging smoke tests can assert a clean exit code.

    Examples
    --------
    >>> main(["--hello"])  # doctest: +ELLIPSIS
    Hello World
    Info for lib_log_rich:
    ...
    0
    """

    parser = argparse.ArgumentParser(
        prog="lib_log_rich",
        description="Emit the library metadata banner used by documentation and smoke tests.",
    )
    parser.add_argument(
        "--hello",
        action="store_true",
        help="print the canonical Hello World greeting before the metadata banner",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.hello:
        hello_world()
    print(summary_info(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
