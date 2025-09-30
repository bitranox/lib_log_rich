"""Dump adapter supporting text, JSON, and HTML outputs.

Purpose
-------
Turn ring buffer snapshots into shareable artefacts without depending on
external sinks.

Contents
--------
* :class:`DumpAdapter` - implementation of :class:`DumpPort`.

System Role
-----------
Feeds operational tooling (CLI, logdemo) and diagnostics when operators request
text/JSON/HTML dumps.

Alignment Notes
---------------
Output formats and templates align with the behaviour described in
``docs/systemdesign/module_reference.md``.
"""

from __future__ import annotations

import html
import json
from collections.abc import Sequence
from pathlib import Path

from lib_log_rich.application.ports.dump import DumpPort
from lib_log_rich.domain.dump import DumpFormat
from lib_log_rich.domain.events import LogEvent
from lib_log_rich.domain.levels import LogLevel


class DumpAdapter(DumpPort):
    """Render ring buffer snapshots into text, JSON, or HTML."""

    def dump(
        self,
        events: Sequence[LogEvent],
        *,
        dump_format: DumpFormat,
        path: Path | None = None,
        min_level: LogLevel | None = None,
        text_template: str | None = None,
        colorize: bool = False,
    ) -> str:
        """Render ``events`` according to ``dump_format`` and optional filters.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
        >>> DumpAdapter().dump([event], dump_format=DumpFormat.JSON).startswith('[')
        True
        """
        filtered = list(events)
        if min_level is not None:
            filtered = [event for event in filtered if event.level.value >= min_level.value]

        if dump_format is DumpFormat.TEXT:
            content = self._render_text(filtered, template=text_template, colorize=colorize)
        elif dump_format is DumpFormat.JSON:
            content = self._render_json(filtered)
        elif dump_format is DumpFormat.HTML:
            content = self._render_html(filtered)
        else:  # pragma: no cover - exhaustiveness guard
            raise ValueError(f"Unsupported dump format: {dump_format}")

        if path is not None:
            path.write_text(content, encoding="utf-8")
        return content

    @staticmethod
    def _render_text(
        events: Sequence[LogEvent],
        *,
        template: str | None,
        colorize: bool,
    ) -> str:
        """Render text dumps honouring templates and optional colour.

        Examples
        --------
        >>> from datetime import datetime, timezone
        >>> from lib_log_rich.domain.context import LogContext
        >>> from lib_log_rich.domain.levels import LogLevel
        >>> ctx = LogContext(service='svc', environment='prod', job_id='job')
        >>> event = LogEvent('id', datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc), 'svc', LogLevel.INFO, 'msg', ctx)
        >>> DumpAdapter._render_text([event], template='{message}', colorize=False)
        'msg'
        """
        if not events:
            return ""

        pattern = template or "{timestamp} {level:<8} {logger_name} {event_id} {message}"

        level_colours = {
            LogLevel.DEBUG: "[36m",  # cyan
            LogLevel.INFO: "[32m",  # green
            LogLevel.WARNING: "[33m",  # yellow
            LogLevel.ERROR: "[31m",  # red
            LogLevel.CRITICAL: "[35m",  # magenta
        }
        reset = "[0m"

        lines: list[str] = []
        for event in events:
            context_data = event.context.to_dict(include_none=True)
            chain_values = context_data.get("process_id_chain") or []
            chain_str = ">".join(str(value) for value in chain_values)
            data = {
                "timestamp": event.timestamp.isoformat(),
                "level": event.level.severity.upper(),
                "logger_name": event.logger_name,
                "event_id": event.event_id,
                "message": event.message,
                "context": context_data,
                "extra": dict(event.extra),
                "user_name": context_data.get("user_name") or "",
                "hostname": context_data.get("hostname") or "",
                "process_id": context_data.get("process_id") if context_data.get("process_id") is not None else "",
                "process_id_chain": chain_str,
            }
            try:
                line = pattern.format(**data)
            except KeyError as exc:  # pragma: no cover - invalid template
                raise ValueError(f"Unknown placeholder in text template: {exc}") from exc

            if colorize:
                colour = level_colours.get(event.level)
                if colour:
                    line = f"{colour}{line}{reset}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _render_json(events: Sequence[LogEvent]) -> str:
        """Serialise events into a deterministic JSON array.

        Examples
        --------
        >>> DumpAdapter._render_json([])
        '[]'
        """
        payload = [event.to_dict() for event in events]
        return json.dumps(payload, indent=2, sort_keys=True)

    @staticmethod
    def _render_html(events: Sequence[LogEvent]) -> str:
        """Generate a minimal HTML table for quick sharing.

        Examples
        --------
        >>> DumpAdapter._render_html([]).startswith('<html>')
        True
        """
        rows = []
        for event in events:
            context_data = event.context.to_dict(include_none=True)
            chain_values = context_data.get("process_id_chain") or []
            if isinstance(chain_values, (list, tuple)):
                chain_str = ">".join(str(value) for value in chain_values)
            else:
                chain_str = str(chain_values)
            rows.append(
                "<tr>"
                f"<td>{html.escape(event.timestamp.isoformat())}</td>"
                f"<td>{html.escape(event.level.severity.upper())}</td>"
                f"<td>{html.escape(event.logger_name)}</td>"
                f"<td>{html.escape(event.message)}</td>"
                f"<td>{html.escape(str(context_data.get('user_name') or ''))}</td>"
                f"<td>{html.escape(str(context_data.get('hostname') or ''))}</td>"
                f"<td>{html.escape(str(context_data.get('process_id') or ''))}</td>"
                f"<td>{html.escape(chain_str)}</td>"
                "</tr>"
            )
        table = "".join(rows)
        return (
            "<html><head><title>lib_log_rich dump</title></head><body>"
            "<table>"
            "<thead><tr><th>Timestamp</th><th>Level</th><th>Logger</th><th>Message</th><th>User</th><th>Hostname</th><th>PID</th><th>PID Chain</th></tr></thead>"
            f"<tbody>{table}</tbody>"
            "</table>"
            "</body></html>"
        )


__all__ = ["DumpAdapter"]
