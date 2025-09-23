"""Console entry point providing the documented metadata banner.

Purpose
-------
Expose a minimal CLI so packaging checks and smoke tests can execute
`python -m lib_log_rich` or the `lib_log_rich` console script while the Rich
logging backbone is under construction.

Contents
--------
* :func:`main` - integrates the Click-based command runner with our tests.
* :func:`cli` - Click command that supports `--hello` and `--version` flags.

System Role
-----------
Serves the presentation layer in the placeholder architecture: it surfaces the
same metadata produced by :func:`lib_log_rich.summary_info` without introducing
new behavior. The CLI keeps legacy automation working while documentation and
integration tests transition to the richer logging features.
"""

from __future__ import annotations

from typing import Sequence

import click

from . import __init__conf__
from .lib_log_rich import hello_world, summary_info


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--hello",
    is_flag=True,
    help="Print the canonical Hello World greeting before the metadata banner.",
)
@click.option(
    "--version",
    "-V",
    is_flag=True,
    help="Print the installed version and exit.",
)
def cli(*, hello: bool, version: bool) -> None:
    """Emit the metadata banner or version number according to CLI flags."""

    if version:
        click.echo(__init__conf__.version)
        return

    if hello:
        hello_world()
    # ``summary_info`` already returns a string ending with a newline.
    click.echo(summary_info(), nl=False)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Click command in a test-friendly manner.

    Why
        Historical automation invokes ``python -m lib_log_rich`` or the
        ``lib_log_rich`` console script after installation. Wrapping the Click
        command keeps that workflow viable while allowing doctests to call this
        helper directly.

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
    >>> main(["--version"])  # doctest: +ELLIPSIS
    0.0...
    0
    >>> main(["--hello"])  # doctest: +ELLIPSIS
    Hello World
    Info for lib_log_rich:
    ...
    0
    """

    args = list(argv) if argv is not None else None
    try:
        cli.main(args=args, standalone_mode=False)
    except click.ClickException as error:
        error.show()
        return error.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
