#!/usr/bin/env python3
"""Preview different icon styles for log levels."""

from rich.console import Console

console = Console()

# Current icons
current = {
    "DEBUG": "🐞",
    "INFO": "ℹ",
    "WARNING": "⚠",
    "ERROR": "✖",
    "CRITICAL": "☠",
}

# Alternative 1: Filled blocks (solid squares)
blocks = {
    "DEBUG": "█",
    "INFO": "█",
    "WARNING": "█",
    "ERROR": "█",
    "CRITICAL": "█",
}

# Alternative 2: Geometric shapes
shapes = {
    "DEBUG": "◆",
    "INFO": "●",
    "WARNING": "▲",
    "ERROR": "■",
    "CRITICAL": "★",
}

# Alternative 3: Arrows and symbols
arrows = {
    "DEBUG": "▸",
    "INFO": "▶",
    "WARNING": "⚡",
    "ERROR": "✘",
    "CRITICAL": "💥",
}

# Alternative 4: Circles
circles = {
    "DEBUG": "◉",
    "INFO": "◎",
    "WARNING": "◐",
    "ERROR": "●",
    "CRITICAL": "◉",
}

# Alternative 5: Brackets with letters
brackets = {
    "DEBUG": "[D]",
    "INFO": "[i]",
    "WARNING": "[!]",
    "ERROR": "[X]",
    "CRITICAL": "[‼]",
}

# Alternative 6: Double characters
double = {
    "DEBUG": "⚙⚙",
    "INFO": "ℹℹ",
    "WARNING": "⚠⚠",
    "ERROR": "✖✖",
    "CRITICAL": "☠☠",
}

# Alternative 7: Box drawing
boxes = {
    "DEBUG": "▫",
    "INFO": "▪",
    "WARNING": "▣",
    "ERROR": "▪",
    "CRITICAL": "◼",
}

# Alternative 8: Mixed modern
modern = {
    "DEBUG": "⚙",
    "INFO": "💡",
    "WARNING": "⚠",
    "ERROR": "🔴",
    "CRITICAL": "💀",
}

# Alternative 9: ASCII only
ascii_only = {
    "DEBUG": "[•]",
    "INFO": "[ i ]",
    "WARNING": "[!]",
    "ERROR": "[X]",
    "CRITICAL": "[!!!]",
}

# Alternative 10: Single characters
single = {
    "DEBUG": "·",
    "INFO": "•",
    "WARNING": "▴",
    "ERROR": "✕",
    "CRITICAL": "✖",
}

icon_sets = [
    ("Current (Emoji)", current),
    ("Filled Blocks", blocks),
    ("Geometric Shapes", shapes),
    ("Arrows & Symbols", arrows),
    ("Circles", circles),
    ("Brackets", brackets),
    ("Double Characters", double),
    ("Box Drawing", boxes),
    ("Modern Mixed", modern),
    ("ASCII Only", ascii_only),
    ("Single Characters", single),
]

levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
styles = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
}

console.print("\n[bold]Log Level Icon Alternatives[/bold]\n")

for name, icons in icon_sets:
    console.print(f"\n[bold underline]{name}[/bold underline]")
    for level in levels:
        icon = icons[level]
        style = styles[level]
        console.print(f"  [{style}]{icon:>3} {level:>8}[/{style}]  |  Sample log message with {level.lower()} level")

console.print("\n")
