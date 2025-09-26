"""Structured backend adapters for journald and Windows Event Log.

Purpose
-------
Surface operating-system specific adapters so the composition root can attach
journald and Windows Event Log sinks when the deployment platform requires
those integrations.

Contents
--------
* :class:`JournaldAdapter` – serialises events into uppercase fields consumed by
  ``systemd.journal.send``.
* :class:`WindowsEventLogAdapter` – maps events to Windows Event Log semantics
  (event IDs, event types, string arrays).

System Role
-----------
Collects the structured adapters in one module so application wiring can remain
explicit about optional sinks while keeping domain and application layers free
from platform-dependent imports.
"""

from __future__ import annotations

from .journald import JournaldAdapter
from .windows_eventlog import WindowsEventLogAdapter

__all__ = ["JournaldAdapter", "WindowsEventLogAdapter"]
