"""Port describing the queue infrastructure for fan-out processing.

Alignment Notes
---------------
Matches the queue lifecycle spelled out in ``docs/systemdesign/module_reference.md``
(start → put → graceful stop).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class QueuePort(Protocol):
    """Bridge between producer processes and the listener worker.

    Abstracting the queue keeps multiprocessing concerns out of the application
    use case while enabling alternative implementations (e.g., in-memory for
    tests, multiprocessing for production).

    Example:
        >>> class Recorder:
        ...     def __init__(self):
        ...         self.events = []
        ...     def start(self) -> None:
        ...         self.events.append('start')
        ...     def stop(self, *, drain: bool = True) -> None:
        ...         self.events.append(f'stop:{drain}')
        ...     def put(self, event: LogEvent) -> bool:
        ...         self.events.append(event.logger_name)
        ...         return True
        >>> isinstance(Recorder(), QueuePort)
        True

    """

    def start(self) -> None:
        """Start the queue worker."""
        ...

    def stop(self, *, drain: bool = True, timeout: float | None = 5.0) -> None:
        """Stop the queue worker, optionally draining queued events."""
        ...

    def put(self, event: LogEvent) -> bool:
        """Enqueue ``event`` for asynchronous processing, returning ``True`` when accepted.

        Implementations may return ``False`` when a non-blocking queue drops the payload.
        """
        ...


__all__ = ["QueuePort"]
