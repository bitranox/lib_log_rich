"""Port describing how runtime code resolves system identity metadata.

Alignment Notes
---------------
Encapsulates the host/user/process lookup so the application layer can refresh
logging context state without importing OS-specific modules, preserving Clean
Architecture boundaries.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from lib_log_rich.domain.identity import SystemIdentity


@runtime_checkable
class SystemIdentityPort(Protocol):
    """Resolve the current process identity (user, hostname, PID).

    Example:
        >>> class StaticIdentity(SystemIdentityPort):
        ...     def resolve_identity(self) -> SystemIdentity:
        ...         return SystemIdentity(user_name='svc', hostname='host', process_id=1234)
        >>> isinstance(StaticIdentity(), SystemIdentityPort)
        True

    """

    def resolve_identity(self) -> SystemIdentity:
        """Return the identity metadata for the running process."""
        ...


__all__ = ["SystemIdentityPort"]
