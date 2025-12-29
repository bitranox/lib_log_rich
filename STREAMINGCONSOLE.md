# Streaming Console Adapters

This guide documents the queue-backed console adapters shipped with
`lib_log_rich`. These adapters let you consume Rich-rendered log lines without
binding directly to the terminal, enabling GUI panes, WebSocket/SSE bridges, or
background telemetry collectors. The runtime exposes both a threaded and an
asyncio variant; you opt into either (or both) via the
`console_adapter_factory` hook when calling `lib_log_rich.init(RuntimeConfig(...))`.

## Why queue-backed console adapters?

Traditional logging handlers write to `stderr` synchronously, which couples your
application threads to terminal I/O. The queue-backed adapters:

- decouple producers from the console by buffering rendered output in
  thread-safe or asyncio queues;
- preserve Rich styling (ANSI or HTML) so downstream consumers can reuse the
  same output in GUIs or dashboards;
- respect the runtime’s format presets and templates, keeping output consistent
  regardless of the transport;
- expose backpressure settings identical to the main logging queue, protecting
  the application from stalled consumers.

## Key components

### `QueueConsoleAdapter`

**Purpose**: render console lines into a standard `queue.Queue` so background
threads (or external processes) can consume and forward them.

**Constructor signature**:

```python
QueueConsoleAdapter(
    queue: queue.Queue[str],
    *,
    export_style: ExportStyle = "ansi",
    force_color: bool = False,
    no_color: bool = False,
    styles: Mapping[str, str] | None = None,
    format_preset: str | None = None,
    format_template: str | None = None,
    console_width: int | None = None,
)
```

- Provide the `queue.Queue[str]` instance yourself. Pick an appropriate
  `maxsize` so consumers keep up and to bound memory usage.
- `export_style` selects the payload format: `"ansi"` keeps Rich colour
  codes; `"html"` produces snippets ready for web panels.
- The adapter calls `queue.put` for each rendered segment. When the queue is
  full the call blocks until space is available, so run your consumers on
  separate threads to avoid stalling producers.
- Rich appearance options (`force_color`, `no_color`, `styles`,
  `format_preset`, `format_template`, `console_width`) mirror the runtime
  configuration so queue consumers see the same layout as terminal users.

The adapter implements the console port expected by the runtime. The runtime
performs the same console-level filtering before calling it, and the internal
Rich formatter is identical to the default console adapter. Each call to
`emit(event, colorize=...)` renders the event and places the final string on the
queue.

### `AsyncQueueConsoleAdapter`

**Purpose**: provide the same behaviour for asyncio applications via
`asyncio.Queue`.

**Constructor signature** mirrors the threaded adapter, swapping
`asyncio.Queue[str]` for the `queue` parameter.

The adapter exposes a synchronous `emit` method because the runtime’s console
port contract is synchronous. The runtime still applies the same console level
gate before invoking it. Internally it calls `queue.put_nowait`; if the queue is
full the chunk is dropped, so size the queue generously or drain it promptly when
loss is unacceptable.

### `ExportStyle`

A type alias (`Literal["ansi", "html"]`) with two values: `"ansi"` and `"html"`. Use ANSI for
terminal mirroring and HTML for embedding logs in web frontends.

### `console_adapter_factory`

`lib_log_rich.init(RuntimeConfig(...))` accepts an optional `console_adapter_factory` keyword
argument:

```python
import queue

import lib_log_rich as log

console_queue = queue.Queue(maxsize=1024)

def console_factory(appearance: ConsoleAppearance) -> ConsolePort:
    return QueueConsoleAdapter(
        queue=console_queue,
        export_style="ansi",
        force_color=appearance.force_color,
        no_color=appearance.no_color,
        styles=appearance.styles,
        format_preset=appearance.format_preset,
        format_template=appearance.format_template,
        console_width=appearance.console_width,
    )

config = log.RuntimeConfig(..., console_adapter_factory=console_factory)
log.init(config)
```

- The runtime constructs an adapter by calling the factory each time it needs
  a console port. This ensures per-runtime isolation: tests, GUIs, and CLIs can
  inject their own adapters without global monkey-patching.
- The `ConsoleAppearance` argument captures the resolved Rich appearance (theme,
  styles, template, width, colour flags). Always pass these through to your
  adapter so configuration remains consistent.
- A single factory can fan out to multiple adapters. The stresstest CLI, for
  example, builds both `QueueConsoleAdapter` and `AsyncQueueConsoleAdapter` and
  returns a composite that emits to each.

## Integration patterns

### Threaded consumer example

```python
import queue
import threading
from lib_log_rich import init, getLogger, shutdown
from lib_log_rich.runtime import QueueConsoleAdapter, ConsoleAppearance

log_lines: "queue.Queue[str]" = queue.Queue(maxsize=1024)

def console_factory(appearance: ConsoleAppearance):
    return QueueConsoleAdapter(
        queue=log_lines,
        export_style="ansi",
        force_color=appearance.force_color,
        no_color=appearance.no_color,
        styles=appearance.styles,
        format_preset=appearance.format_preset,
        format_template=appearance.format_template,
        console_width=appearance.console_width,
    )

init(service="demo", environment="dev", console_adapter_factory=console_factory)

stop = object()

def consumer() -> None:
    while True:
        chunk = log_lines.get()
        if chunk is stop:
            break
        # Forward to a GUI widget, a file, or a network socket.
        print(f"STREAM> {chunk}")

thread = threading.Thread(target=consumer, daemon=True)
thread.start()

logger = getLogger("app.stream")
logger.info("hello from the queue")

log_lines.put(stop)
shutdown()
thread.join()
```

### Async consumer example

```python
import asyncio
from lib_log_rich import init, getLogger, shutdown
from lib_log_rich.runtime import AsyncQueueConsoleAdapter, ConsoleAppearance

log_lines: "asyncio.Queue[str]" = asyncio.Queue(maxsize=1024)

async def main() -> None:
    def console_factory(appearance: ConsoleAppearance):
        return AsyncQueueConsoleAdapter(
            queue=log_lines,
            export_style="html",
            force_color=appearance.force_color,
            no_color=appearance.no_color,
            styles=appearance.styles,
            format_preset=appearance.format_preset,
            format_template=appearance.format_template,
            console_width=appearance.console_width,
        )

    init(
        service="demo",
        environment="dev",
        console_adapter_factory=console_factory,
    )

    async def consumer() -> None:
        while True:
            chunk = await log_lines.get()
            if chunk is None:
                break
            await broadcast_over_websocket(chunk)

    consumer_task = asyncio.create_task(consumer())

    logger = getLogger("app.async")
    logger.warning("async stream ready")

    await log_lines.put(None)  # sentinel
    await consumer_task
    shutdown()

asyncio.run(main())
```

`broadcast_over_websocket` represents your own dissemination logic (FastAPI
websocket endpoint, Textual widget, etc.). The example sets `html`
so downstream consumers can render colourful log fragments in browsers without
manual conversion.

### Composite factories

To feed both threaded and async consumers simultaneously, build a composite that
wraps both adapters:

```python
from lib_log_rich.adapters.console import AsyncQueueConsoleAdapter, QueueConsoleAdapter
from lib_log_rich.application.ports.console import ConsolePort
from lib_log_rich.runtime import ConsoleAppearance

thread_queue = queue.Queue[str]()
async_queue = asyncio.Queue[str]()

class StreamingConsole(ConsolePort):
    def __init__(self, appearance: ConsoleAppearance) -> None:
        self._threaded = QueueConsoleAdapter(
            queue=thread_queue,
            export_style="ansi",
            force_color=appearance.force_color,
            no_color=appearance.no_color,
            styles=appearance.styles,
            format_preset=appearance.format_preset,
            format_template=appearance.format_template,
            console_width=appearance.console_width,
        )
        self._async = AsyncQueueConsoleAdapter(
            queue=async_queue,
            export_style="html",
            force_color=appearance.force_color,
            no_color=appearance.no_color,
            styles=appearance.styles,
            format_preset=appearance.format_preset,
            format_template=appearance.format_template,
            console_width=appearance.console_width,
        )

    def emit(self, event, *, colorize: bool) -> None:
        self._threaded.emit(event, colorize=colorize)
        self._async.emit(event, colorize=colorize)

init(
    service="demo",
    environment="dev",
    console_adapter_factory=StreamingConsole,
)
```

This pattern matches the behaviour of `lib_log_rich`’s built-in stresstest CLI.

## Operational considerations

- **Backpressure**: `QueueConsoleAdapter` blocks producers while the queue
  is full; pick a sensible `maxsize` and drain it promptly.
  `AsyncQueueConsoleAdapter` uses `queue.put_nowait`, so full queues drop additional
  segments. Pair this with a runtime `diagnostic_hook` if you also rely on the
  main logging queue to flag drops.
- **Shutdown**: call `lib_log_rich.shutdown()` to flush console queues before
  tearing down your application. Drain any custom queues after shutdown so
  worker threads or tasks exit cleanly.
- **Testing**: the adapters are deterministic and safe to use in tests. Inject a
  factory that writes to an in-memory queue; assertions can inspect the queue
  content without touching the real terminal.
- **HTML rendering**: when streaming HTML output, ensure your consumer sanitises
  or escapes as needed. Rich’s HTML snippets are trusted output by default;
  treat them accordingly when exposing logs to end users.

## Related resources

- `examples/flask_console_stream.py` – upgrades a Flask app to stream Rich HTML
  log lines over Server-Sent Events.
- `CLI.md` – the `stresstest` command shows how to multiplex threaded and async
  adapters while exercising queue policies.
- `docs/systemdesign/module_reference.md` – architectural reference for the
  console port and queue adapters.
