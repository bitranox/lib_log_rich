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

from .lib_log_rich import bind, dump, get, hello_world, i_should_fail, init, logdemo, shutdown, shutdown_async, summary_info

__all__ = [
    "bind",
    "dump",
    "get",
    "hello_world",
    "i_should_fail",
    "init",
    "logdemo",
    "shutdown",
    "shutdown_async",
    "summary_info",
]
