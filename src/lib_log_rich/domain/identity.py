"""System identity value objects for runtime composition.

Purpose
-------
Expose a typed representation of host identity (user, hostname, process id)
so application and adapter layers can depend on a stable contract rather than
reaching into :mod:`os`, :mod:`socket`, or :mod:`getpass` directly.

System Role
-----------
Lives in the domain layer to keep Clean Architecture boundaries intact while
making it obvious which pieces of metadata are propagated into logging
contexts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SystemIdentity:
    """Host/process identity resolved by outer-layer adapters.

    Attributes:
        user_name: Best-effort login/user identifier; may be ``None`` when
            unavailable.
        hostname: Short hostname for the current machine; may be ``None``.
        process_id: Operating system process identifier for the running
            interpreter.

    """

    user_name: str | None
    hostname: str | None
    process_id: int


__all__ = ["SystemIdentity"]
