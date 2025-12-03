"""Application layer factories binding ports to use cases.

Purpose
-------
Expose the use-case factories required by the composition root when turning
abstract ports into callable orchestrators.

Contents
--------
* :func:`create_process_log_event` – build the primary logging pipeline.
* :func:`create_capture_dump` – snapshot the ring buffer via dump adapters.
* :func:`create_shutdown` – construct the shutdown coroutine for adapter cleanup.

System Role
-----------
Provides the seam between the application layer and the outer adapter wiring, as
outlined in ``docs/systemdesign/concept_architecture.md``.
"""

from .use_cases._types import ProcessPipelineDependencies
from .use_cases.dump import create_capture_dump
from .use_cases.process_event import create_process_log_event
from .use_cases.shutdown import create_shutdown

__all__ = [
    "create_process_log_event",
    "ProcessPipelineDependencies",
    "create_capture_dump",
    "create_shutdown",
]
