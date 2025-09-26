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
    """Export buffered events to human-readable or machine formats."""

    def dump(
        self,
        events: Sequence[LogEvent],
        *,
        dump_format: DumpFormat,
        path: Path | None = None,
        min_level: LogLevel | None = None,
        text_template: str | None = None,
        colorize: bool = False,
    ) -> str:
        """Render ``events`` according to the requested format."""


__all__ = ["DumpPort"]
