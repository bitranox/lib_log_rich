"""Use case exporting buffered events through a dump adapter.

Purpose
-------
Provide the application-layer glue between the ring buffer and dump adapter.

System Role
-----------
Invoked by :func:`lib_log_rich.dump` to render, persist, and flush buffered
events.

Alignment Notes
---------------
The callable returned here mirrors the behaviour described in
``docs/systemdesign/module_reference.md`` for dump workflows (filtering by level,
optional templates, colour toggles).
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
    """Return a callable capturing the current dependencies.

    Why
    ---
    Exposing a closure allows the composition root to configure dump behaviour
    once while giving the public API a pure function focussing on rendering.

    Parameters
    ----------
    ring_buffer:
        Buffer supplying the events to export.
    dump_port:
        Adapter responsible for formatting and persistence.
    default_template:
        Optional fallback text template when none is provided at call time.

    Returns
    -------
    Callable[[DumpFormat, Path | None, LogLevel | None, str | None, bool], str]
        Function that renders events and returns the produced payload.

    Examples
    --------
    >>> class DummyDump(DumpPort):
    ...     def __init__(self):
    ...         self.calls = []
    ...     def dump(self, events, *, dump_format, path, min_level, text_template, colorize):
    ...         self.calls.append((len(list(events)), dump_format, path, min_level, text_template, colorize))
    ...         return 'payload'
    >>> ring = RingBuffer(max_events=5)
    >>> dump_port = DummyDump()
    >>> capture = create_capture_dump(ring_buffer=ring, dump_port=dump_port, default_template='{message}')
    >>> result = capture(dump_format=DumpFormat.TEXT, path=None, min_level=None, text_template=None, colorize=False)
    >>> result
    'payload'
    >>> dump_port.calls[0][1] is DumpFormat.TEXT
    True
    """

    def capture(
        *,
        dump_format: DumpFormat,
        path: Path | None = None,
        min_level: LogLevel | None = None,
        text_template: str | None = None,
        colorize: bool = False,
    ) -> str:
        """Render the ring buffer and flush it after a successful dump.

        Why
        ---
        Ensures dumps represent the exact events flushed to disk while keeping
        the in-memory buffer clean for subsequent captures.

        Side Effects
        ------------
        Calls :meth:`RingBuffer.flush` after invoking the adapter.
        """

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
