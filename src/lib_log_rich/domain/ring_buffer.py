"""Ring buffer storing the most recent log events.

Purpose
-------
Provide in-memory retention for recent events so operators can inspect state
without relying on external targets.

Contents
--------
* :class:`EventCheckpoint`, the persistence port the buffer flushes to.
* :class:`RingBuffer` with checkpointing and iteration helpers.

System Role
-----------
Feeds dump adapters and satisfies the diagnostic requirements captured in
``concept_architecture_plan.md``.

Alignment Notes
---------------
Persistence is a port: the buffer hands events to an :class:`EventCheckpoint`
and never touches a file itself, so parsing what was stored stays in the
adapter layer (``lib_log_rich.adapters.checkpoint.JsonlCheckpoint``), which
validates every line before a :class:`LogEvent` is built from it.

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
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from .events import LogEvent


class EventCheckpoint(Protocol):
    """Durable store a :class:`RingBuffer` hydrates from and flushes to.

    The domain only names the two operations it needs; reading and validating
    the stored form is the implementation's job, so a corrupt record is refused
    at that boundary and never reaches the buffer as raw data.
    """

    def load(self) -> Sequence[LogEvent]:
        """Return every stored event, oldest first (empty when nothing is stored)."""
        ...

    def append(self, events: Sequence[LogEvent]) -> None:
        """Persist ``events`` after whatever is already stored."""
        ...


class RingBuffer:
    """Fixed-size buffer retaining the most recent :class:`LogEvent` objects.

    The architecture emphasises low-latency diagnostics without always-on files.
    A bounded in-memory buffer provides quick access for dumps while keeping
    memory consumption predictable.

    Args:
        max_events: Maximum number of events to store before older entries are
            evicted.
        checkpoint: Optional :class:`EventCheckpoint` the buffer hydrates from
            at construction and appends to on :meth:`flush`.

    Example:
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
        >>> from lib_log_rich.domain.events import LogEvent
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

    def __init__(self, *, max_events: int, checkpoint: EventCheckpoint | None = None) -> None:
        """Create a ring buffer with optional persistence.

        Args:
            max_events: Maximum number of entries retained in memory.
            checkpoint: Optional store hydrated into the buffer now (keeping the
                newest ``max_events``) and appended to by :meth:`flush`.

        Raises:
            ValueError: If ``max_events`` is not positive, or whatever the
                checkpoint raises for a stored record it refuses.

        """
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._max_events = max_events
        self._checkpoint = checkpoint
        self._buffer: deque[LogEvent] = deque(maxlen=max_events)
        self._dirty = False
        if checkpoint is not None:
            self._buffer.extend(checkpoint.load())

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
            >>> from lib_log_rich.domain.events import LogEvent
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
            >>> from lib_log_rich.domain.events import LogEvent
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
        """Append buffer contents to the checkpoint and clear the buffer.

        Events are appended to the checkpoint (not replaced), then the in-memory
        buffer is cleared, so each event is persisted exactly once. Without a
        checkpoint, or with nothing appended since the last flush, this is a
        no-op that leaves the buffer intact.

        Example:
            >>> from datetime import datetime, timezone
            >>> from lib_log_rich.domain.context import LogContext
            >>> from lib_log_rich.domain.levels import LogLevel
            >>> from lib_log_rich.domain.events import LogEvent
            >>> class MemoryCheckpoint:
            ...     def __init__(self): self.stored = []
            ...     def load(self): return list(self.stored)
            ...     def append(self, events): self.stored.extend(events)
            >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
            >>> event = LogEvent('1', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'a', ctx)
            >>> store = MemoryCheckpoint()
            >>> buffer = RingBuffer(max_events=5, checkpoint=store)
            >>> buffer.append(event); buffer.flush()
            >>> len(buffer)  # Buffer is cleared after flush
            0
            >>> [stored.event_id for stored in store.stored]
            ['1']

        """
        if self._checkpoint is None or not self._dirty:
            return
        self._checkpoint.append(list(self._buffer))
        self._buffer.clear()
        self._dirty = False


__all__ = ["EventCheckpoint", "RingBuffer"]
