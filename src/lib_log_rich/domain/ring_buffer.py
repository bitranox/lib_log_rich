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
``konzept_architecture_plan.md``.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Deque, Iterable, Iterator

from .events import LogEvent


class RingBuffer:
    """Fixed-size buffer retaining the most recent :class:`LogEvent` objects."""

    def __init__(self, *, max_events: int, checkpoint_path: Path | None = None) -> None:
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
        """Return the configured buffer size."""

        return self._max_events

    def append(self, event: LogEvent) -> None:
        """Append an event to the buffer, evicting older entries if necessary."""

        self._buffer.append(event)
        self._dirty = True

    def extend(self, events: Iterable[LogEvent]) -> None:
        """Append a sequence of events preserving chronological order."""
        for event in events:
            self.append(event)

    def snapshot(self) -> list[LogEvent]:
        """Return a copy of the current buffer state."""

        return list(self._buffer)

    def __iter__(self) -> Iterator[LogEvent]:
        """Iterate over buffered events from oldest to newest."""
        return iter(self._buffer)

    def __len__(self) -> int:
        """Return the number of events currently stored."""
        return len(self._buffer)

    def clear(self) -> None:
        """Remove all buffered events and mark the checkpoint dirty."""
        self._buffer.clear()
        self._dirty = True

    def flush(self) -> None:
        """Persist the buffer to the checkpoint path if configured."""
        if not self._checkpoint_path or not self._dirty:
            return
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        with self._checkpoint_path.open("w", encoding="utf-8") as fh:
            for event in self._buffer:
                fh.write(json.dumps(event.to_dict(), sort_keys=True))
                fh.write("\n")
        self._dirty = False

    def _load_checkpoint(self, path: Path) -> None:
        """Hydrate the buffer from a newline-delimited JSON checkpoint."""
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
