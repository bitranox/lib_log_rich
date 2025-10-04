"""Application-layer port definitions.

Purpose
-------
Re-export all application-level ports so adapters can depend on a single module,
matching the structure defined in ``concept_architecture.md``.
"""

from __future__ import annotations

from .console import ConsolePort
from .dump import DumpPort
from .graylog import GraylogPort
from .identity import SystemIdentityPort
from .queue import QueuePort
from .rate_limiter import RateLimiterPort
from .scrubber import ScrubberPort
from .structures import StructuredBackendPort
from .time import ClockPort, IdProvider, UnitOfWork

__all__ = [
    "ClockPort",
    "ConsolePort",
    "DumpPort",
    "GraylogPort",
    "SystemIdentityPort",
    "IdProvider",
    "QueuePort",
    "RateLimiterPort",
    "ScrubberPort",
    "StructuredBackendPort",
    "UnitOfWork",
]
