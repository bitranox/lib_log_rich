# Open work

Standing backlog. Edit line by line, never rewrite wholesale. When an item is finished, delete
its line; the reason it closed belongs in the commit message, CHANGELOG or git history.

- [ ] (2026-11-03) [10] FOUND: `import lib_log_rich` costs about 103 ms (median of 8 fresh-interpreter runs on origin/master 904029f), almost all of it adapters imported eagerly at package import: adapters.checkpoint ~42 ms (pydantic models plus the first pydantic/pydantic_core import), adapters.console.queue_console ~26 ms (rich.console, asyncio), domain ~24 ms (one -X importtime run each) | size: unknown; a change across runtime/adapter wiring | open: found by datamax-dpl's performance review, where it is about a third of CLI startup; the demo module is NOT the cost (0.7 ms self: -X importtime charges runtime to demo only because demo is imported first, and removing that import changed nothing measurable) | next: measure which adapters every init() really needs, then defer the rest to first use; prove it with interleaved fresh-interpreter import timings and a sys.modules test

