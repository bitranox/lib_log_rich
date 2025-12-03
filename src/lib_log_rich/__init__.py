"""Library façade exposing the temporary helper API.

Purpose
-------
Provide a tiny, import-only surface so documentation, doctests, and prototype
integrations can rely on stable helpers while the Rich-backed logging
architecture specified in ``docs/systemdesign/concept_architecture.md`` is
implemented.

Contents
--------
* :func:`hello_world` – deterministic success path for smoke tests and docs.
* :func:`summary_info` – formatted metadata banner sourced from
  :mod:`lib_log_rich.__init__conf__`.

System Role
-----------
Acts as the package-level façade referenced by README examples and
``module_reference.md`` so downstream code does not import implementation
modules directly.
"""

from __future__ import annotations

from .lib_log_rich import (
    RuntimeConfig,
    SeveritySnapshot,
    bind,
    dump,
    get_minimum_log_level,
    getLogger,
    hello_world,
    i_should_fail,
    init,
    logdemo,
    max_level_seen,
    reset_severity_metrics,
    severity_snapshot,
    shutdown,
    shutdown_async,
    summary_info,
)

__all__ = [
    "RuntimeConfig",
    "SeveritySnapshot",
    "bind",
    "dump",
    "getLogger",
    "get_minimum_log_level",
    "hello_world",
    "i_should_fail",
    "init",
    "logdemo",
    "max_level_seen",
    "reset_severity_metrics",
    "severity_snapshot",
    "shutdown",
    "shutdown_async",
    "summary_info",
]
