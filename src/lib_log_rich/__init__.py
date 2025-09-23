"""Public package surface exposing the stable greetings helper.

The project intentionally keeps the runtime surface area tiny while the richer
logging utilities are designed. Exporting :func:`hello_world` here allows both
``import lib_log_rich`` and ``python -m lib_log_rich``
flows to exercise the same domain function, as documented in
``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

from .lib_log_rich import hello_world

__all__ = ["hello_world"]
