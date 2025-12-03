"""Port for redacting sensitive information.

Alignment Notes
---------------
Encodes the scrubbing contract referenced in ``docs/systemdesign/module_reference.md``
so sensitive data never leaves the application boundary unsanitised.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class ScrubberPort(Protocol):
    """Scrub sensitive values from log events before emission.

    Allows the runtime to plug in configurable scrubbing policies while keeping
    the use case agnostic to implementation details.

    Example:
        >>> class NoopScrubber:
        ...     def scrub(self, event: LogEvent) -> LogEvent:
        ...         return event
        >>> isinstance(NoopScrubber(), ScrubberPort)
        True

    """

    def scrub(self, event: LogEvent) -> LogEvent:
        """Return a (possibly) redacted copy of ``event``."""
        ...


__all__ = ["ScrubberPort"]
