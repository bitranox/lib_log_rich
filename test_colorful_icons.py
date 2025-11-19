#!/usr/bin/env python3
"""Preview colorful emoji icon styles for log levels."""

from rich.console import Console

console = Console()

# Alternative 1: Colored circles
colored_circles = {
    "DEBUG": "🔵",  # Blue circle
    "INFO": "🟢",  # Green circle
    "WARNING": "🟡",  # Yellow circle
    "ERROR": "🔴",  # Red circle
    "CRITICAL": "🟣",  # Purple circle
}

# Alternative 2: Status lights
status_lights = {
    "DEBUG": "💙",  # Blue heart
    "INFO": "💚",  # Green heart
    "WARNING": "💛",  # Yellow heart
    "ERROR": "❤️",  # Red heart
    "CRITICAL": "🖤",  # Black heart
}

# Alternative 3: Colored squares
colored_squares = {
    "DEBUG": "🟦",  # Blue square
    "INFO": "🟩",  # Green square
    "WARNING": "🟨",  # Yellow square
    "ERROR": "🟥",  # Red square
    "CRITICAL": "🟪",  # Purple square
}

# Alternative 4: Traffic light style
traffic = {
    "DEBUG": "⚪",  # White circle
    "INFO": "🟢",  # Green circle
    "WARNING": "🟡",  # Yellow circle
    "ERROR": "🟠",  # Orange circle
    "CRITICAL": "🔴",  # Red circle
}

# Alternative 5: Colored diamonds
colored_diamonds = {
    "DEBUG": "🔹",  # Small blue diamond
    "INFO": "🔷",  # Large blue diamond
    "WARNING": "🔶",  # Large orange diamond
    "ERROR": "🔸",  # Small orange diamond
    "CRITICAL": "🔺",  # Red triangle
}

# Alternative 6: Mixed colorful symbols
mixed_colorful = {
    "DEBUG": "🔧",  # Wrench (blue/grey)
    "INFO": "💡",  # Light bulb (yellow)
    "WARNING": "⚠️",  # Warning (yellow/black)
    "ERROR": "🔴",  # Red circle
    "CRITICAL": "💥",  # Explosion (red/yellow)
}

# Alternative 7: Nature themed
nature = {
    "DEBUG": "🌿",  # Herb (green)
    "INFO": "💧",  # Droplet (blue)
    "WARNING": "🌻",  # Sunflower (yellow)
    "ERROR": "🔥",  # Fire (red/orange)
    "CRITICAL": "⚡",  # Lightning (yellow)
}

# Alternative 8: Emoji faces
faces = {
    "DEBUG": "😐",  # Neutral
    "INFO": "😊",  # Smiling
    "WARNING": "😮",  # Surprised
    "ERROR": "😨",  # Fearful
    "CRITICAL": "💀",  # Skull
}

# Alternative 9: Weather
weather = {
    "DEBUG": "⛅",  # Partly cloudy
    "INFO": "☀️",  # Sun
    "WARNING": "⛈️",  # Storm
    "ERROR": "🌧️",  # Rain
    "CRITICAL": "❄️",  # Snowflake
}

# Alternative 10: Tech themed
tech = {
    "DEBUG": "🔍",  # Magnifying glass
    "INFO": "📘",  # Blue book
    "WARNING": "⚡",  # Lightning
    "ERROR": "🚨",  # Siren
    "CRITICAL": "💣",  # Bomb
}

# Alternative 11: Status indicators
status = {
    "DEBUG": "🔵",  # Blue circle
    "INFO": "✅",  # Check mark (green)
    "WARNING": "⚠️",  # Warning (yellow)
    "ERROR": "❌",  # X mark (red)
    "CRITICAL": "🛑",  # Stop sign (red)
}

# Alternative 12: Animals
animals = {
    "DEBUG": "🐛",  # Bug (green)
    "INFO": "🐝",  # Bee (yellow/black)
    "WARNING": "🦁",  # Lion (orange)
    "ERROR": "🐍",  # Snake (green/yellow)
    "CRITICAL": "🦂",  # Scorpion (brown)
}

# Alternative 13: Flags
flags = {
    "DEBUG": "🏳️",  # White flag
    "INFO": "🏴",  # Black flag
    "WARNING": "🚩",  # Red flag
    "ERROR": "⛔",  # No entry
    "CRITICAL": "🔴",  # Red circle
}

# Alternative 14: Fruits
fruits = {
    "DEBUG": "🫐",  # Blueberries (blue)
    "INFO": "🍏",  # Green apple
    "WARNING": "🍋",  # Lemon (yellow)
    "ERROR": "🍎",  # Red apple
    "CRITICAL": "🍇",  # Grapes (purple)
}

# Alternative 15: Geometric colorful
geo_color = {
    "DEBUG": "🔵",  # Blue circle
    "INFO": "🟢",  # Green circle
    "WARNING": "🟨",  # Yellow square
    "ERROR": "🟥",  # Red square
    "CRITICAL": "🔺",  # Red triangle
}

# Alternative 16: Signal strength
signal = {
    "DEBUG": "📶",  # Signal bars
    "INFO": "🟢",  # Green
    "WARNING": "🟡",  # Yellow
    "ERROR": "🔴",  # Red
    "CRITICAL": "⭕",  # Hollow red circle
}

# Alternative 17: Simple colored
simple_color = {
    "DEBUG": "🔷",  # Blue diamond
    "INFO": "🟩",  # Green square
    "WARNING": "🟨",  # Yellow square
    "ERROR": "🟥",  # Red square
    "CRITICAL": "⬛",  # Black square
}

# Alternative 18: Playful
playful = {
    "DEBUG": "🎯",  # Dart (red/white)
    "INFO": "🎨",  # Palette (colorful)
    "WARNING": "⚡",  # Lightning (yellow)
    "ERROR": "💢",  # Anger (red)
    "CRITICAL": "💥",  # Boom (red/yellow)
}

icon_sets = [
    ("Colored Circles", colored_circles),
    ("Status Lights (Hearts)", status_lights),
    ("Colored Squares", colored_squares),
    ("Traffic Light Style", traffic),
    ("Colored Diamonds", colored_diamonds),
    ("Mixed Colorful Symbols", mixed_colorful),
    ("Nature Themed", nature),
    ("Emoji Faces", faces),
    ("Weather", weather),
    ("Tech Themed", tech),
    ("Status Indicators", status),
    ("Animals", animals),
    ("Flags", flags),
    ("Fruits", fruits),
    ("Geometric Colorful", geo_color),
    ("Signal Strength", signal),
    ("Simple Colored", simple_color),
    ("Playful", playful),
]

levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
styles = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
}

console.print("\n[bold]Colorful Emoji Icon Alternatives[/bold]\n")

for name, icons in icon_sets:
    console.print(f"\n[bold underline]{name}[/bold underline]")
    for level in levels:
        icon = icons.get(level, "?")
        style = styles[level]
        # Show without style to preserve emoji colors
        console.print(f"  {icon:>3} [{style}]{level:>8}[/{style}]  |  {icon} Sample log message")

console.print("\n")
