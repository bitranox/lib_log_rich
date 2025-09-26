"""Use case exporting buffered events through a dump adapter.

Purpose
-------
Provide the application-layer glue between the ring buffer and dump adapter.

System Role
-----------
Invoked by :func:`lib_log_rich.dump` to render, persist, and flush buffered
events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from lib_log_rich.domain import RingBuffer
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.application.ports.dump import DumpPort
from lib_log_rich.domain.levels import LogLevel


def create_capture_dump(
    *,
    ring_buffer: RingBuffer,
    dump_port: DumpPort,
    default_template: str | None = None,
) -> Callable[[DumpFormat, Path | None, LogLevel | None, str | None, bool], str]:
    """Return a callable capturing the current dependencies."""

    def capture(
        *,
        dump_format: DumpFormat,
        path: Path | None = None,
        min_level: LogLevel | None = None,
        text_template: str | None = None,
        colorize: bool = False,
    ) -> str:
        """Render the ring buffer and flush it after a successful dump."""
        template = text_template if text_template is not None else default_template
        events = ring_buffer.snapshot()
        payload = dump_port.dump(
            events,
            dump_format=dump_format,
            path=path,
            min_level=min_level,
            text_template=template,
            colorize=colorize,
        )
        ring_buffer.flush()
        return payload

    return capture


__all__ = ["create_capture_dump"]
