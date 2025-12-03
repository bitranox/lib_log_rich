"""Use cases orchestrating logging behaviour."""

from .dump import create_capture_dump
from .process_event import create_process_log_event
from .shutdown import create_shutdown

__all__ = [
    "create_capture_dump",
    "create_process_log_event",
    "create_shutdown",
]
