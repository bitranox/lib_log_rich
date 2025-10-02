"""Console palette presets shared across CLI and runtime components."""

from __future__ import annotations

CONSOLE_STYLE_THEMES: dict[str, dict[str, str]] = {
    "classic": {
        "DEBUG": "dim",
        "INFO": "cyan",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold red",
    },
    "dark": {
        "DEBUG": "grey42",
        "INFO": "bright_white",
        "WARNING": "bold gold3",
        "ERROR": "bold red3",
        "CRITICAL": "bold white on red3",
    },
    "neon": {
        "DEBUG": "#00ffd5",
        "INFO": "#39ff14",
        "WARNING": "#fff700",
        "ERROR": "#ff073a",
        "CRITICAL": "bold #ff00ff on black",
    },
    "pastel": {
        "DEBUG": "aquamarine1",
        "INFO": "light_sky_blue1",
        "WARNING": "khaki1",
        "ERROR": "light_salmon1",
        "CRITICAL": "bold plum1",
    },
}
"""Built-in console palettes keyed by theme name."""

__all__ = ["CONSOLE_STYLE_THEMES"]
