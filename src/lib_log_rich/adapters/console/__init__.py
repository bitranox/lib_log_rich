"""Console adapter exports for Rich-backed rendering.

Purpose
-------
Expose the Rich-based console adapter so application composition roots can bind
:class:`lib_log_rich.application.ports.console.ConsolePort` to the Rich
implementation documented in ``concept_architecture.md``.

Contents
--------
* :class:`RichConsoleAdapter` – renders human-facing log lines with theme and
  colour overrides.

System Role
-----------
Acts as the adapter layer boundary for terminal output, making it explicit which
implementation satisfies the console port in the Clean Architecture stack.
"""

from __future__ import annotations

from .rich_console import RichConsoleAdapter

__all__ = ["RichConsoleAdapter"]
