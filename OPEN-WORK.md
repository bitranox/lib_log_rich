# Open work

Standing backlog. Edit line by line, never rewrite wholesale. When an item is finished, delete
its line; the reason it closed belongs in the commit message, CHANGELOG or git history.

- [ ] (2026-10-27) [100] FOUND: render `exc_info` and `stack_info` in the sinks that drop them today - the Rich console, the text and HTML dumps, Graylog (GELF `full_message` or a `_exc_info` field) and journald (`EXCEPTION_INFO`/`STACK_INFO`-style fields) - so `logger.exception()` shows its traceback wherever stdlib logging would | size: unknown; four adapters plus their limits and docs, a minor release (new behaviour) | open: measured 2026-10-27 by grep, src/lib_log_rich/adapters reads exc_info only in checkpoint.py and _schemas.py (JSON dump), and a console probe printed no traceback; 8.0.3 makes the text itself correct (it held a tuple repr before), so rendering it is now worth doing; first seen here | next: decide per sink where the traceback goes and how it is bounded (console: indented block under the line; GELF: full_message), then RED tests per adapter

