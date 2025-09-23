"""Core library helpers exposed by the public package surface.

The project intentionally ships a tiny, deterministic runtime surface while the
Rich-powered logging backbone is implemented. The helpers contained here remain
import-only so the library can be exercised from Python code and doctests
without relying on CLI entry points.

Contents
--------
* :func:`hello_world` – emits the canonical greeting used in documentation and
  smoke tests. This gives developers a stable, human-readable success path.
* :func:`i_should_fail` – raises an intentional error so that failure handling
  can be validated end-to-end.
* :func:`summary_info` – returns the metadata string rendered by
  :func:`lib_log_rich.__init__conf__.print_info` for programmatic access.

System Context
--------------
The module is part of the domain/presentation placeholder used by tests and
documentation. Without CLI entry points, these helpers constitute the entire
public runtime contract until the richer logging capabilities replace the
scaffold.
"""

from __future__ import annotations


def hello_world() -> None:
    """Emit the canonical greeting used to verify the happy-path workflow.

    Why
        The scaffold ships with a deterministic success path so developers can
        check their packaging and documentation quickly without waiting for the
        richer logging helpers.

    What
        Prints the literal ``"Hello World"`` string followed by a newline to
        ``stdout``.

    Parameters
    ----------
    None
        The helper accepts no inputs; invocation always exhibits the same
        behavior.

    Returns
    -------
    None
        The function is used for its side effect only and returns ``None``.

    Side Effects
        Writes directly to the process ``stdout`` stream.

    Examples
    --------
    >>> hello_world()
    Hello World
    """

    print("Hello World")


def i_should_fail() -> None:
    """Intentionally raise ``RuntimeError`` to test error propagation paths.

    Why
        Tests and integration scaffolds need a deterministic failure scenario to
        ensure error-handling branches stay verifiable as the project evolves.

    What
        Raises ``RuntimeError`` with the message ``"I should fail"`` every time
        it is called.

    Parameters
    ----------
    None
        The helper accepts no inputs so callers can focus on exercising failure
        handling logic.

    Returns
    -------
    None
        The exception interrupts normal control flow; successful return is not
        expected.

    Side Effects
        None besides raising the exception.

    Raises
        RuntimeError: Always, so downstream adapters can verify their error
        handling branches.

    Examples
    --------
    >>> i_should_fail()
    Traceback (most recent call last):
    ...
    RuntimeError: I should fail
    """

    raise RuntimeError("I should fail")


def summary_info() -> str:
    """Return the human-readable metadata summary for the package.

    Why
        Documentation and integrations previously relied on
        :func:`lib_log_rich.__init__conf__.print_info` via CLI entry points.
        With the CLI removed, callers still need programmatic access to the
        formatted metadata block.

    What
        Delegates to :func:`lib_log_rich.__init__conf__.print_info` and returns
        the resulting string instead of printing it.

    Parameters
    ----------
    None
        This helper accepts no inputs so callers can rely on a deterministic
        metadata snapshot.

    Returns
    -------
    str
        The formatted metadata block.

    Side Effects
        None. The function only captures text produced by
        :func:`lib_log_rich.__init__conf__.print_info`.

    Examples
    --------
    >>> summary = summary_info()
    >>> "Info for lib_log_rich" in summary
    True
    >>> summary.endswith(chr(10))
    True
    """

    from . import __init__conf__

    lines: list[str] = []

    def _capture(text: str) -> None:
        lines.append(text)

    __init__conf__.print_info(writer=_capture)
    return "".join(lines)


if __name__ == "__main__":
    raise SystemExit("lib_log_rich is import-only; CLI entry points were removed.")
