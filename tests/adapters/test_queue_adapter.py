from __future__ import annotations

import threading
import time

from lib_log_rich.adapters.queue import QueueAdapter
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel
from lib_log_rich.domain.context import LogContext


def _make_event(i: int) -> LogEvent:
    from datetime import datetime, timezone

    return LogEvent(
        event_id=f"evt-{i}",
        timestamp=datetime(2025, 9, 23, 12, i, tzinfo=timezone.utc),
        logger_name="tests",
        level=LogLevel.INFO,
        message=f"message-{i}",
        context=LogContext(service="svc", environment="test", job_id="job"),
    )


def test_queue_adapter_processes_all_events() -> None:
    processed: list[str] = []
    lock = threading.Lock()

    def _worker(event: LogEvent) -> None:
        with lock:
            processed.append(event.event_id)

    adapter = QueueAdapter(worker=_worker)
    adapter.start()
    for i in range(5):
        adapter.put(_make_event(i))
    time.sleep(0.2)
    adapter.stop()
    assert processed == [f"evt-{i}" for i in range(5)]


def test_queue_adapter_drains_on_stop() -> None:
    processed: list[str] = []
    lock = threading.Lock()

    def _worker(event: LogEvent) -> None:
        time.sleep(0.05)
        with lock:
            processed.append(event.event_id)

    adapter = QueueAdapter(worker=_worker)
    adapter.start()
    for i in range(3):
        adapter.put(_make_event(i))
    adapter.stop(drain=True)
    assert len(processed) == 3
