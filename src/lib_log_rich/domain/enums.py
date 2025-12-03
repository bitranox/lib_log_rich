"""Configuration enumerations for logging system settings.

Purpose
-------
Provide type-safe alternatives to string literals for configuration options
that have a fixed set of valid values.

Contents
--------
* :class:`QueuePolicy` - queue backpressure handling strategies.
* :class:`ConsoleStream` - console output destination options.
* :class:`GraylogProtocol` - network transport for Graylog.

System Role
-----------
Centralises configuration constants so validators, adapters, and CLI help
share a single source of truth, reducing string comparison bugs.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache


class QueuePolicy(str, Enum):
    """Queue backpressure handling strategy.

    Defines how the queue adapter behaves when capacity is reached.

    Example:
        >>> QueuePolicy.BLOCK.value
        'block'
        >>> QueuePolicy.from_str('drop') is QueuePolicy.DROP
        True

    """

    BLOCK = "block"
    DROP = "drop"

    @classmethod
    @lru_cache(maxsize=4)
    def from_str(cls, value: str) -> QueuePolicy:
        """Parse a string into a QueuePolicy.

        Args:
            value: Human-entered string, typically from CLI or config.

        Returns:
            Matching enum member.

        Raises:
            ValueError: If value is not a valid policy.

        Example:
            >>> QueuePolicy.from_str('BLOCK') is QueuePolicy.BLOCK
            True
            >>> QueuePolicy.from_str('  drop  ') is QueuePolicy.DROP
            True

        """
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Invalid queue policy: {value!r}; must be 'block' or 'drop'")


class ConsoleStream(str, Enum):
    """Console output destination selector.

    Controls where console adapter writes log output.

    Example:
        >>> ConsoleStream.STDERR.value
        'stderr'
        >>> ConsoleStream.from_str('both') is ConsoleStream.BOTH
        True

    """

    STDOUT = "stdout"
    STDERR = "stderr"
    BOTH = "both"
    CUSTOM = "custom"
    NONE = "none"

    @classmethod
    @lru_cache(maxsize=8)
    def from_str(cls, value: str) -> ConsoleStream:
        """Parse a string into a ConsoleStream.

        Args:
            value: Human-entered string, typically from CLI or config.

        Returns:
            Matching enum member.

        Raises:
            ValueError: If value is not a valid stream option.

        Example:
            >>> ConsoleStream.from_str('STDERR') is ConsoleStream.STDERR
            True
            >>> ConsoleStream.from_str('  none  ') is ConsoleStream.NONE
            True

        """
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Invalid console stream: {value!r}; must be one of 'stdout', 'stderr', 'both', 'custom', or 'none'")


class GraylogProtocol(str, Enum):
    """Network transport protocol for Graylog GELF messages.

    Example:
        >>> GraylogProtocol.TCP.value
        'tcp'
        >>> GraylogProtocol.from_str('udp') is GraylogProtocol.UDP
        True

    """

    TCP = "tcp"
    UDP = "udp"

    @classmethod
    @lru_cache(maxsize=4)
    def from_str(cls, value: str) -> GraylogProtocol:
        """Parse a string into a GraylogProtocol.

        Args:
            value: Human-entered string, typically from CLI or config.

        Returns:
            Matching enum member.

        Raises:
            ValueError: If value is not a valid protocol.

        Example:
            >>> GraylogProtocol.from_str('TCP') is GraylogProtocol.TCP
            True
            >>> GraylogProtocol.from_str('  udp  ') is GraylogProtocol.UDP
            True

        """
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Invalid Graylog protocol: {value!r}; must be 'tcp' or 'udp'")


__all__ = ["QueuePolicy", "ConsoleStream", "GraylogProtocol"]
