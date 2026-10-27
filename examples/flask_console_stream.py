"""Minimal Flask app streaming lib_log_rich console output via SSE."""

from __future__ import annotations

import contextlib
from queue import Queue
from typing import TYPE_CHECKING

from flask import Flask, Response, stream_with_context

from lib_log_rich.adapters.console import QueueConsoleAdapter
from lib_log_rich.domain import ConsoleTheme, ExportStyle, LogLevel
from lib_log_rich.runtime import ConsoleAppearance, RuntimeConfig, bind, getLogger, init, shutdown

if TYPE_CHECKING:
    from collections.abc import Iterator

    from lib_log_rich.application.ports.console import ConsolePort

console_queue: Queue[str] = Queue()


def console_adapter_factory(appearance: ConsoleAppearance) -> ConsolePort:
    """Build a queue-backed console adapter that renders each event as an HTML document."""
    return QueueConsoleAdapter(
        console_queue,
        export_style=ExportStyle.HTML,
        force_color=appearance.force_color,
        no_color=appearance.no_color,
        styles=appearance.styles,
        format_preset=appearance.format_preset,
        format_template=appearance.format_template,
    )


def configure_runtime() -> None:
    """Initialize the lib_log_rich runtime with queue-backed console adapter."""
    # Suppress RuntimeError if already initialised in this process.
    with contextlib.suppress(RuntimeError):
        init(
            RuntimeConfig(
                service="flask-demo",
                environment="dev",
                console_level=LogLevel.INFO,
                backend_level=LogLevel.WARNING,
                # Rich's HTML export uses a white page background; the default DARK
                # theme renders INFO in bright white, which would be unreadable there.
                console_theme=ConsoleTheme.CLASSIC,
                console_adapter_factory=console_adapter_factory,
            )
        )


def create_app() -> Flask:
    """Create and configure the Flask application with SSE log streaming routes."""
    app = Flask(__name__)

    configure_runtime()

    @app.route("/logs")
    def stream_logs() -> Response:
        """Stream console log output as Server-Sent Events."""

        def event_stream() -> Iterator[str]:
            """Generate SSE messages from the console queue."""
            while True:
                fragment = console_queue.get()
                # An SSE message ends with a blank line; every line of a
                # multi-line HTML fragment needs its own "data:" prefix.
                data = "".join(f"data: {line}\n" for line in fragment.splitlines())
                yield f"{data}\n"

        return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

    @app.route("/emit/<message>")
    def emit(message: str) -> str:
        """Emit an INFO log message with the given text."""
        logger = getLogger("flask-demo")
        with bind(extra={"route": "/emit"}):
            logger.info(message)
        return "ok"

    @app.route("/shutdown")
    def shutdown_runtime_route() -> str:
        """Gracefully shut down the logging runtime."""
        shutdown()
        return "stopped"

    return app


app = create_app()


if __name__ == "__main__":
    configure_runtime()
    app.run(debug=True, port=5001)
