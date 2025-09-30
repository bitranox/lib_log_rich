"""Dump port defining snapshot export contracts.

Purpose
-------
Describe how ring-buffer snapshots are transformed into shareable artefacts so
application-level use cases can trigger exports without coupling to adapter
implementations.

Contents
--------
* :class:`DumpPort` – protocol specifying required arguments for dump
  operations.

System Role
-----------
Establishes the boundary between the core system and dump adapters (text,
JSON, HTML) referenced in ``docs/systemdesign/module_reference.md``.

Alignment Notes
---------------
Parameter names mirror CLI flags and public API options ensuring documentation
and runtime stay aligned.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


@runtime_checkable
class DumpPort(Protocol):
    """Export buffered events to human-readable or machine formats.

    Why
    ---
    Allows multiple dump adapters (text, JSON, HTML) to coexist without
    hard-coding behaviour into the use case. Supporting tests can supply simple
    fakes.

    Parameters
    ----------
    events:
        Snapshot from the ring buffer.
    dump_format:
        Target format (text/json/html).
    path:
        Optional destination path; ``None`` indicates in-memory only.
    min_level:
        Optional severity filter.
    format_preset:
        Optional template preset identifier (``full``, ``short``).
    format_template:
        Optional literal template string (overrides the preset when provided).
    text_template:
        Deprecated alias for ``format_template`` retained for backwards compatibility.
    colorize:
        Toggle for ANSI colour output in text dumps.

    Returns
    -------
    str
        Rendered payload for immediate consumption (e.g., CLI output).

    Examples
    --------
    >>> class Recorder:
    ...     def dump(self, events, *, dump_format, path, min_level, format_preset, format_template, text_template, colorize):
    ...         template = format_template or text_template or format_preset
    ...         return f"{len(list(events))}:{dump_format.value}:{template}:{colorize}"
    >>> isinstance(Recorder(), DumpPort)
    True
    >>> Recorder().dump([], dump_format=DumpFormat.TEXT, path=None, min_level=None, format_preset=None, format_template=None, text_template=None, colorize=False)
    '0:text:None:False'
    """

    def dump(
        self,
        events: Sequence[LogEvent],
        *,
        dump_format: DumpFormat,
        path: Path | None = None,
        min_level: LogLevel | None = None,
        format_preset: str | None = None,
        format_template: str | None = None,
        text_template: str | None = None,
        colorize: bool = False,
    ) -> str:
        """Render ``events`` according to the requested format."""


__all__ = ["DumpPort"]
