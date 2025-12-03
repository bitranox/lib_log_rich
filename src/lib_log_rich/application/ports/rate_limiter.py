"""Port for rate limiting filters protecting downstream sinks.

Alignment Notes
---------------
Encapsulates the throttle behaviour described in the resilience section of
``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.events import LogEvent


@runtime_checkable
class RateLimiterPort(Protocol):
    """Decide whether a log event may pass through the pipeline.

    Allows production deployments to plug in smarter rate limiting while tests
    can rely on deterministic fakes.

    Example:
        >>> class AllowAll:
        ...     def allow(self, event: LogEvent) -> bool:
        ...         return True
        >>> isinstance(AllowAll(), RateLimiterPort)
        True

    """

    def allow(self, event: LogEvent) -> bool:
        """Return ``True`` when ``event`` is permitted to proceed."""
        ...


__all__ = ["RateLimiterPort"]
