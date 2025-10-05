"""Legacy entrypoint that delegates to the CLI stresstest UI."""

from __future__ import annotations

from lib_log_rich.cli_stresstest import run


def main() -> None:
    run()


if __name__ == "__main__":  # pragma: no cover - manual execution helper
    main()
