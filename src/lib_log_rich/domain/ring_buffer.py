"""Ring buffer storing the most recent log events.

Purpose
-------
Provide in-memory retention for recent events so operators can inspect state
without relying on external targets.

Contents
--------
* :class:`RingBuffer` with checkpointing and iteration helpers.

System Role
-----------
Feeds dump adapters and satisfies the diagnostic requirements captured in
``concept_architecture_plan.md``.

Alignment Notes
---------------
The persistence format (newline-delimited JSON) matches the expectations noted
in ``docs/systemdesign/module_reference.md`` for offline analysis tooling.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Deque, Iterable, Iterator

from .events import LogEvent


class RingBuffer:
    """Fixed-size buffer retaining the most recent :class:`LogEvent` objects.

    Why
    ---
    The architecture emphasises low-latency diagnostics without always-on files.
    A bounded in-memory buffer provides quick access for dumps while keeping
    memory consumption predictable.

    Parameters
    ----------
    max_events:
        Maximum number of events to store before older entries are evicted.
    checkpoint_path:
        Optional path to a newline-delimited JSON file used to hydrate state
        during startup and persist snapshots during shutdown.

    Examples
    --------
    >>> from datetime import datetime, timezone
    >>> from lib_log_rich.domain.context import LogContext
    >>> from lib_log_rich.domain.levels import LogLevel
    >>> buffer = RingBuffer(max_events=2)
    >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
    >>> event = LogEvent(
    ...     event_id='1',
    ...     timestamp=datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc),
    ...     logger_name='svc.worker',
    ...     level=LogLevel.INFO,
    ...     message='started',
    ...     context=ctx,
    ... )
    >>> buffer.append(event)
    >>> len(buffer)
    1
    """

    def __init__(self, *, max_events: int, checkpoint_path: Path | None = None) -> None:
        """Create a ring buffer with optional persistence.

        Parameters
        ----------
        max_events:
            Maximum number of entries retained in memory.
        checkpoint_path:
            Optional path to a newline-delimited JSON checkpoint hydrated on
            startup and flushed via :meth:`flush`.

        Raises
        ------
        ValueError
            If ``max_events`` is not positive.
        """
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._max_events = max_events
        self._checkpoint_path = checkpoint_path
        self._buffer: Deque[LogEvent] = deque(maxlen=max_events)
        self._dirty = False
        if checkpoint_path and checkpoint_path.exists():
            self._load_checkpoint(checkpoint_path)

    @property
    def max_events(self) -> int:
        """Return the configured buffer size.

        Examples
        --------
        >>> RingBuffer(max_events=5).max_events
        5
        """

        return self._max_events

    def append(self, event: LogEvent) -> None:
        """Append an event, evicting older entries if necessary.

        Side Effects
        ------------
        Mutates the internal deque and marks the buffer dirty so a subsequent
        :meth:`flush` persists the new state.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> buffer = RingBuffer(max_events=1)
        >>> e1 = LogEvent('1', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'a', ctx)
        >>> e2 = LogEvent('2', datetime(2025, 9, 30, 12, 1, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'b', ctx)
        >>> buffer.append(e1); buffer.append(e2)
        >>> [e.event_id for e in buffer]
        ['2']
        """

        self._buffer.append(event)
        self._dirty = True

    def extend(self, events: Iterable[LogEvent]) -> None:
        """Append a sequence of events preserving chronological order.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> events = [
        ...     LogEvent('1', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'a', ctx),
        ...     LogEvent('2', datetime(2025, 9, 30, 12, 1, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'b', ctx),
        ... ]
        >>> buffer = RingBuffer(max_events=5)
        >>> buffer.extend(events)
        >>> len(buffer)
        2
        """

        for event in events:
            self.append(event)

    def snapshot(self) -> list[LogEvent]:
        """Return a copy of the current buffer state.

        Examples
        --------
        >>> buffer = RingBuffer(max_events=2)
        >>> buffer.snapshot()
        []
        """

        return list(self._buffer)

    def __iter__(self) -> Iterator[LogEvent]:
        """Iterate over buffered events from oldest to newest.

        Examples
        --------
        >>> list(RingBuffer(max_events=1))
        []
        """

        return iter(self._buffer)

    def __len__(self) -> int:
        """Return the number of events currently stored.

        Examples
        --------
        >>> len(RingBuffer(max_events=3))
        0
        """

        return len(self._buffer)

    def clear(self) -> None:
        """Remove all buffered events and mark the checkpoint dirty.

        Examples
        --------
        >>> buffer = RingBuffer(max_events=1)
        >>> buffer.clear()
        >>> len(buffer)
        0
        """

        self._buffer.clear()
        self._dirty = True

    def flush(self) -> None:
        """Persist the buffer to the checkpoint path if configured.

        Why
        ---
        Allows ``dump`` consumers and restarts to continue where they left off,
        aligning with the recovery behaviour described in the system design.

        Examples
        --------
        >>> import tempfile, json
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('1', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'a', ctx)
        >>> tmp = Path(tempfile.gettempdir()) / 'ring-buffer-checkpoint.jsonl'
        >>> buffer = RingBuffer(max_events=5, checkpoint_path=tmp)
        >>> buffer.append(event); buffer.flush()
        >>> data = [json.loads(line) for line in tmp.read_text(encoding='utf-8').splitlines() if line]
        >>> data[0]['event_id']
        '1'
        """

        if not self._checkpoint_path or not self._dirty:
            return
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self._checkpoint_path.open("w", encoding="utf-8") as fh:
            for event in self._buffer:
                fh.write(json.dumps(event.to_dict(), sort_keys=True))
                fh.write("\n")
        self._dirty = False

    def _load_checkpoint(self, path: Path) -> None:
        """Hydrate the buffer from a newline-delimited JSON checkpoint.

        This helper intentionally swallows missing-file errors to support
        first-time start scenarios.

        Parameters
        ----------
        path:
            File path to read.

        Side Effects
        ------------
        Populates :attr:`_buffer` with deserialised events.
        """

        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    payload = json.loads(line)
                    self._buffer.append(LogEvent.from_dict(payload))
        except FileNotFoundError:
            return


__all__ = ["RingBuffer"]
