#!/usr/bin/env python3
"""Preview more modern UTF-8 icon styles for log levels."""

from rich.console import Console

console = Console()

# Alternative 1: Nerd Font / Dev Icons style
nerd_font = {
    "DEBUG": "󰃤",  # Bug icon
    "INFO": "",  # Info circle
    "WARNING": "",  # Alert triangle
    "ERROR": "",  # Error circle
    "CRITICAL": "",  # Fire
}

# Alternative 2: Circled symbols
circled = {
    "DEBUG": "⊛",
    "INFO": "ⓘ",
    "WARNING": "⚠",
    "ERROR": "⊗",
    "CRITICAL": "⊙",
}

# Alternative 3: Bold geometric
bold_geo = {
    "DEBUG": "◈",
    "INFO": "◉",
    "WARNING": "◭",
    "ERROR": "◼",
    "CRITICAL": "◆",
}

# Alternative 4: Triangles and diamonds
triangles = {
    "DEBUG": "◇",
    "INFO": "◆",
    "WARNING": "▲",
    "ERROR": "▼",
    "CRITICAL": "◈",
}

# Alternative 5: Hexagons and octagons
hex_oct = {
    "DEBUG": "⬡",
    "INFO": "⬢",
    "WARNING": "⬟",
    "ERROR": "⬣",
    "CRITICAL": "⬢",
}

# Alternative 6: Circles with variations
circle_var = {
    "DEBUG": "◯",
    "INFO": "◉",
    "WARNING": "◎",
    "ERROR": "⦿",
    "CRITICAL": "⊙",
}

# Alternative 7: Stars and sparkles
stars = {
    "DEBUG": "✦",
    "INFO": "✧",
    "WARNING": "✶",
    "ERROR": "✴",
    "CRITICAL": "✷",
}

# Alternative 8: Crosses and plus
crosses = {
    "DEBUG": "✚",
    "INFO": "✓",
    "WARNING": "⚠",
    "ERROR": "✗",
    "CRITICAL": "✖",
}

# Alternative 9: Diamonds
diamonds = {
    "DEBUG": "◇",
    "INFO": "◆",
    "WARNING": "◈",
    "ERROR": "◉",
    "CRITICAL": "◊",
}

# Alternative 10: Dotted patterns
dots = {
    "DEBUG": "⋅",
    "INFO": "•",
    "WARNING": "⚫",
    "ERROR": "⬤",
    "CRITICAL": "⬢",
}

# Alternative 11: Arrows variations
arrow_var = {
    "DEBUG": "➤",
    "INFO": "➜",
    "WARNING": "⮕",
    "ERROR": "⯈",
    "CRITICAL": "⯇",
}

# Alternative 12: Boxed
boxed = {
    "DEBUG": "▢",
    "INFO": "▣",
    "WARNING": "▤",
    "ERROR": "▥",
    "CRITICAL": "▦",
}

# Alternative 13: Filled circles sized
sized_circles = {
    "DEBUG": "⚬",
    "INFO": "○",
    "WARNING": "◉",
    "ERROR": "⬤",
    "CRITICAL": "⬢",
}

# Alternative 14: Modern clean
modern_clean = {
    "DEBUG": "◦",
    "INFO": "●",
    "WARNING": "◆",
    "ERROR": "■",
    "CRITICAL": "▲",
}

# Alternative 15: Thick borders
thick = {
    "DEBUG": "◫",
    "INFO": "◪",
    "WARNING": "▣",
    "ERROR": "▦",
    "CRITICAL": "▧",
}

# Alternative 16: Mixed modern v2
modern_v2 = {
    "DEBUG": "⚡",
    "INFO": "◉",
    "WARNING": "⬢",
    "ERROR": "✖",
    "CRITICAL": "▲",
}

# Alternative 17: Minimal dots
minimal = {
    "DEBUG": "·",
    "INFO": "○",
    "WARNING": "◎",
    "ERROR": "●",
    "CRITICAL": "◉",
}

# Alternative 18: Pointing
pointing = {
    "DEBUG": "☞",
    "INFO": "☛",
    "WARNING": "☜",
    "ERROR": "☟",
    "CRITICAL": "⚠",
}

icon_sets = [
    ("Nerd Font Style", nerd_font),
    ("Circled Symbols", circled),
    ("Bold Geometric", bold_geo),
    ("Triangles & Diamonds", triangles),
    ("Hexagons & Octagons", hex_oct),
    ("Circle Variations", circle_var),
    ("Stars & Sparkles", stars),
    ("Crosses & Checks", crosses),
    ("Diamonds", diamonds),
    ("Dotted Patterns", dots),
    ("Arrow Variations", arrow_var),
    ("Boxed", boxed),
    ("Sized Circles", sized_circles),
    ("Modern Clean", modern_clean),
    ("Thick Borders", thick),
    ("Mixed Modern v2", modern_v2),
    ("Minimal Dots", minimal),
    ("Pointing Hands", pointing),
]

levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
styles = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
}

console.print("\n[bold]More Modern UTF-8 Icon Alternatives[/bold]\n")

for name, icons in icon_sets:
    console.print(f"\n[bold underline]{name}[/bold underline]")
    for level in levels:
        icon = icons[level]
        style = styles[level]
        console.print(f"  [{style}]{icon:>3} {level:>8}[/{style}]  |  [{style}]{icon}[/{style}] Sample log message")

console.print("\n")
