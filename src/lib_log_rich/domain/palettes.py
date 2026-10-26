"""Console palette presets shared across CLI and runtime components."""

from __future__ import annotations

from .enums import ConsoleTheme

# Keyed by the enum so a palette can only exist for a theme name the
# configuration boundary accepts; a test pins that every member has one.
CONSOLE_STYLE_THEMES: dict[ConsoleTheme, dict[str, str]] = {
    ConsoleTheme.CLASSIC: {
        "DEBUG": "dim",
        "INFO": "cyan",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold red",
    },
    ConsoleTheme.DARK: {
        "DEBUG": "grey42",
        "INFO": "bright_white",
        "WARNING": "bold gold3",
        "ERROR": "bold red3",
        "CRITICAL": "bold white on red3",
    },
    ConsoleTheme.NEON: {
        "DEBUG": "#00ffd5",
        "INFO": "#39ff14",
        "WARNING": "#fff700",
        "ERROR": "#ff073a",
        "CRITICAL": "bold #ff00ff on black",
    },
    ConsoleTheme.PASTEL: {
        "DEBUG": "aquamarine1",
        "INFO": "light_sky_blue1",
        "WARNING": "khaki1",
        "ERROR": "light_salmon1",
        "CRITICAL": "bold plum1",
    },
}
"""Built-in console palettes keyed by :class:`ConsoleTheme`, each keyed by uppercase level name."""

__all__ = ["CONSOLE_STYLE_THEMES"]
