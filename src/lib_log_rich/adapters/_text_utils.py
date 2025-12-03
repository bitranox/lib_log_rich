"""Text processing utilities for adapters.

Purpose
-------
Provides shared text manipulation functions used by structured logging adapters
to ensure consistent formatting across journald, graylog, and other backends.

Contents
--------
* :data:`_EMOJI_PATTERN` - compiled regex for emoji detection.
* :func:`strip_emoji` - remove emoji and Unicode pictographic symbols.

System Role
-----------
Centralizes text processing logic to avoid duplication across adapter modules.
Used primarily by structured logging backends that require plain-text output
without decorative Unicode symbols.
"""

from __future__ import annotations

import re
from typing import Final

# Regex pattern to match emoji and pictographic symbols
# This covers most emoji ranges including:
# - Emoticons (U+1F600-U+1F64F)
# - Miscellaneous Symbols and Pictographs (U+1F300-U+1F5FF)
# - Transport and Map Symbols (U+1F680-U+1F6FF)
# - Supplemental Symbols and Pictographs (U+1F900-U+1F9FF)
# - Symbols and Pictographs Extended-A (U+1FA70-U+1FAFF)
# - Plus common Unicode symbols like ℹ, ⚠, ✖, ☠
_EMOJI_PATTERN: Final[re.Pattern[str]] = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f700-\U0001f77f"  # alchemical symbols
    "\U0001f780-\U0001f7ff"  # geometric shapes extended
    "\U0001f800-\U0001f8ff"  # supplemental arrows-C
    "\U0001f900-\U0001f9ff"  # supplemental symbols and pictographs
    "\U0001fa00-\U0001fa6f"  # chess symbols
    "\U0001fa70-\U0001faff"  # symbols and pictographs extended-A
    "\U00002600-\U000027bf"  # miscellaneous symbols (includes ☠)
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U00002300-\U000023ff"  # miscellaneous technical
    "\U00002000-\U0000206f"  # general punctuation
    "\U0000200d"  # zero width joiner
    "\U0000fe0e"  # text variation selector
    "\u2600-\u27bf"  # misc symbols including ⚠, ✖
    "\u2100-\u214f"  # letterlike symbols including ℹ
    "\u2190-\u21ff"  # arrows
    "\u2300-\u23ff"  # misc technical
    "\U0001f1e0-\U0001f1ff"  # flags (iOS)
    "]+",
    flags=re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Remove emoji and Unicode pictographic symbols from text.

    Used by structured logging adapters (journald, graylog) to ensure
    clean, emoji-free log messages for system logs where decorative
    symbols may cause display issues or are semantically redundant.

    Args:
        text: Input string potentially containing emoji/icons.

    Returns:
        Text with all emoji and icons removed.

    Example:
        >>> strip_emoji("Error ✖ occurred")
        'Error  occurred'
        >>> strip_emoji("Info ℹ message")
        'Info  message'
        >>> strip_emoji("Warning ⚠ found")
        'Warning  found'
        >>> strip_emoji("Plain text")
        'Plain text'
        >>> strip_emoji("")
        ''

    """
    return _EMOJI_PATTERN.sub("", text)


__all__ = ["strip_emoji"]
