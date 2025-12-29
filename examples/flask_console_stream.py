"""Minimal Flask app streaming lib_log_rich console output via SSE."""

from __future__ import annotations

import contextlib
from queue import Queue

from flask import Flask, Response, stream_with_context

from lib_log_rich.adapters.console import QueueConsoleAdapter
from lib_log_rich.domain import LogLevel
from lib_log_rich.runtime import bind, getLogger, init, shutdown

console_queue: Queue[str] = Queue()


def console_adapter_factory(appearance):
    """Build a queue-backed console adapter with HTML rendering."""
    return QueueConsoleAdapter(
        console_queue,
        export_style="html",
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
            service="flask-demo",
            environment="dev",
            console_level=LogLevel.INFO,
            backend_level=LogLevel.WARNING,
            console_adapter_factory=console_adapter_factory,
        )


def create_app() -> Flask:
    """Create and configure the Flask application with SSE log streaming routes."""
    app = Flask(__name__)

    configure_runtime()

    @app.route("/logs")
    def stream_logs() -> Response:
        """Stream console log output as Server-Sent Events."""

        def event_stream():
            """Generate SSE data lines from the console queue."""
            while True:
                line = console_queue.get()
                yield f"data: {line}"

        return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

    @app.route("/emit/<message>")
    def emit(message: str) -> str:
        """Emit an INFO log message with the given text."""
        logger = getLogger("flask-demo")
        with bind(route="/emit"):
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
