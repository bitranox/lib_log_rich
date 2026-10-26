"""A typed stand-in for the runtime's ``process`` callable.

``LoggerProxy`` and ``LoggingRuntime`` take an
:class:`~lib_log_rich.application.use_cases._types.ProcessCallable`, so a test
double has to satisfy that protocol (keyword-only call plus ``deliver``) for
pyright to check the call sites that feed it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib_log_rich.application.use_cases._types import ProcessResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from lib_log_rich.application.use_cases._types import MessageArgs
    from lib_log_rich.domain import LogEvent, LogLevel


@dataclass(frozen=True, slots=True)
class ProcessCall:
    """The keywords one ``process(...)`` call received."""

    logger_name: str
    level: LogLevel
    message: object
    args: MessageArgs
    exc_info: object | None
    stack_info: object | None
    stacklevel: int
    extra: Mapping[str, object] | None


class RecordingProcess:
    """Record every call and answer each with ``result``."""

    def __init__(self, result: ProcessResult | None = None) -> None:
        self.calls: list[ProcessCall] = []
        self.delivered: list[LogEvent] = []
        self._result = ProcessResult(ok=True) if result is None else result
        self.deliver: Callable[[LogEvent], ProcessResult] = self._deliver

    @property
    def last(self) -> ProcessCall:
        """The most recent call; fails the test when there was none."""
        assert self.calls, "process was never called"
        return self.calls[-1]

    def __call__(
        self,
        *,
        logger_name: str,
        level: LogLevel,
        message: object,
        args: MessageArgs = (),
        exc_info: object | None = None,
        stack_info: object | None = None,
        stacklevel: int = 1,
        extra: Mapping[str, object] | None = None,
    ) -> ProcessResult:
        self.calls.append(
            ProcessCall(
                logger_name=logger_name,
                level=level,
                message=message,
                args=args,
                exc_info=exc_info,
                stack_info=stack_info,
                stacklevel=stacklevel,
                extra=extra,
            )
        )
        return self._result

    def _deliver(self, event: LogEvent) -> ProcessResult:
        self.delivered.append(event)
        return self._result
