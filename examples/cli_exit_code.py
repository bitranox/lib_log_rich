"""A CLI entry point whose exit code survives a failed logging shutdown.

Run it with ``python examples/cli_exit_code.py`` (exit 0) or
``python examples/cli_exit_code.py --fail`` (exit 1).

``shutdown()`` raises ``RuntimeError("Queue worker failed to stop within the
allotted timeout")`` when a sink keeps the queue worker busy past
``queue_stop_timeout``. Called bare in ``main()``'s ``finally``, that exception
replaces the command's return value: a run that already did its work ends with a
traceback and exit code 1. The command's own result is what the caller (a shell,
cron, systemd, CI) needs, so the teardown is best effort: report the lost events
on stderr and keep the exit code.
"""

from __future__ import annotations

import sys

import lib_log_rich as log


def run(argv: list[str]) -> int:
    """The command itself: logs a line and returns its exit code."""
    logger = log.getLogger("example.cli")
    if "--fail" in argv:
        logger.error("the command failed")
        return 1
    logger.info("the command did its work")
    return 0


def shutdown_logging() -> None:
    """Stop the logging runtime without letting a teardown failure replace the command's result.

    A stalled sink makes ``shutdown()`` raise after the queue deadline; the events still queued
    are dropped either way, so say so on stderr and return. The runtime then stays initialised,
    which no longer matters: the process is about to exit.
    """
    try:
        log.shutdown()
    # Any failure here comes after the command decided its result, so none may replace it.
    except Exception as exc:
        sys.stderr.write(f"Warning: logging did not shut down cleanly, some log events may be lost: {exc}\n")


def main(argv: list[str] | None = None) -> int:
    log.init(log.RuntimeConfig(service="example-cli", environment="dev"))
    try:
        return run(sys.argv[1:] if argv is None else argv)
    finally:
        shutdown_logging()


if __name__ == "__main__":
    raise SystemExit(main())
