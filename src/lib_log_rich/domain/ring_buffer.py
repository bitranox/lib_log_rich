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

Algorithm
---------
Uses collections.deque with maxlen for O(1) append and automatic eviction:
- New events appended to right (most recent)
- Oldest events automatically evicted from left when capacity reached
- No manual cleanup needed - deque handles eviction atomically

Thread Safety
------------
RingBuffer is NOT thread-safe. Caller (application layer) must ensure
exclusive access during append operations. Iteration returns a snapshot
(list) which is safe to use across threads.

Performance Characteristics
--------------------------
- Append: O(1) amortized
- Iteration: O(n) where n = capacity
- Memory: O(capacity * avg_event_size)
- Typical capacity: 25,000 events (~50-100MB depending on payload size)
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path

import orjson

from .events import LogEvent


def _parse_checkpoint_line(line: str) -> LogEvent | None:
    """Parse a single checkpoint line into a LogEvent.

    Args:
        line: Raw line from checkpoint file.

    Returns:
        Parsed LogEvent or None if line is empty/whitespace.

    """
    stripped = line.strip()
    if not stripped:
        return None
    payload = orjson.loads(stripped)
    return LogEvent.from_dict(payload)


class RingBuffer:
    """Fixed-size buffer retaining the most recent :class:`LogEvent` objects.

    The architecture emphasises low-latency diagnostics without always-on files.
    A bounded in-memory buffer provides quick access for dumps while keeping
    memory consumption predictable.

    Args:
        max_events: Maximum number of events to store before older entries are
            evicted.
        checkpoint_path: Optional path to a newline-delimited JSON file used to
            hydrate state during startup and persist snapshots during shutdown.

    Example:
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

        Args:
            max_events: Maximum number of entries retained in memory.
            checkpoint_path: Optional path to a newline-delimited JSON checkpoint
                hydrated on startup and flushed via :meth:`flush`.

        Raises:
            ValueError: If ``max_events`` is not positive.

        """
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._max_events = max_events
        self._checkpoint_path = checkpoint_path
        self._buffer: deque[LogEvent] = deque(maxlen=max_events)
        self._dirty = False
        if checkpoint_path and checkpoint_path.exists():
            self._load_checkpoint(checkpoint_path)

    @property
    def max_events(self) -> int:
        """Return the configured buffer size.

        Returns:
            Maximum number of events the buffer can hold.

        Example:
            >>> RingBuffer(max_events=5).max_events
            5

        """
        return self._max_events

    def append(self, event: LogEvent) -> None:
        """Append an event, evicting older entries if necessary.

        Args:
            event: Log event to append to the buffer.

        Note:
            Mutates the internal deque and marks the buffer dirty so a subsequent
            :meth:`flush` persists the new state.

        Example:
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

        Args:
            events: Iterable of log events to append.

        Example:
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

        Returns:
            List of all events currently in the buffer.

        Example:
            >>> buffer = RingBuffer(max_events=2)
            >>> buffer.snapshot()
            []

        """
        return list(self._buffer)

    def __iter__(self) -> Iterator[LogEvent]:
        """Iterate over buffered events from oldest to newest.

        Returns:
            Iterator yielding events in chronological order.

        Example:
            >>> list(RingBuffer(max_events=1))
            []

        """
        return iter(self._buffer)

    def __len__(self) -> int:
        """Return the number of events currently stored.

        Returns:
            Current count of events in the buffer.

        Example:
            >>> len(RingBuffer(max_events=3))
            0

        """
        return len(self._buffer)

    def clear(self) -> None:
        """Remove all buffered events and mark the checkpoint dirty.

        Example:
            >>> buffer = RingBuffer(max_events=1)
            >>> buffer.clear()
            >>> len(buffer)
            0

        """
        self._buffer.clear()
        self._dirty = True

    def flush(self) -> None:
        """Persist the buffer to the checkpoint path if configured.

        Allows ``dump`` consumers and restarts to continue where they left off,
        aligning with the recovery behaviour described in the system design.

        Example:
            >>> import tempfile, json
            >>> from datetime import datetime, timezone
            >>> from lib_log_rich.domain.context import LogContext
            >>> from lib_log_rich.domain.levels import LogLevel
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent('1', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'a', ctx)
            >>> tmp = Path(tempfile.gettempdir()) / 'ring-buffer-checkpoint.jsonl'
            >>> buffer = RingBuffer(max_events=5, checkpoint_path=tmp)
            >>> buffer.append(event); buffer.flush()
            >>> data = [orjson.loads(line) for line in tmp.read_text(encoding='utf-8').splitlines() if line]
            >>> data[0]['event_id']
            '1'

        """
        if not self._checkpoint_path or not self._dirty:
            return
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self._checkpoint_path.open("w", encoding="utf-8") as fh:
            for event in self._buffer:
                fh.write(orjson.dumps(event.to_dict(), option=orjson.OPT_SORT_KEYS).decode())
                fh.write("\n")
        self._dirty = False

    def _load_checkpoint(self, path: Path) -> None:
        """Hydrate the buffer from a newline-delimited JSON checkpoint.

        This helper intentionally swallows missing-file errors to support
        first-time start scenarios.

        Args:
            path: File path to read.

        Note:
            Populates :attr:`_buffer` with deserialised events.
        """
        lines = self._read_checkpoint_lines(path)
        if lines is None:
            return
        events = (_parse_checkpoint_line(line) for line in lines)
        self._buffer.extend(event for event in events if event is not None)

    def _read_checkpoint_lines(self, path: Path) -> list[str] | None:
        """Read checkpoint file lines, returning None if file doesn't exist."""
        try:
            with path.open("r", encoding="utf-8") as fh:
                return fh.readlines()
        except FileNotFoundError:
            return None


__all__ = ["RingBuffer"]
