"""Configuration enumerations for logging system settings.

Purpose
-------
Provide type-safe alternatives to string literals for configuration options
that have a fixed set of valid values.

Contents
--------
* :class:`StrValueEnum` - base for string enums that format as their value.
* :class:`QueuePolicy` - queue backpressure handling strategies.
* :class:`ConsoleStream` - console output destination options.
* :class:`GraylogProtocol` - network transport for Graylog.
* :class:`FormatPreset` - named console and dump line layouts.
* :class:`ConsoleTheme` - named console colour palettes.
* :class:`ExportStyle` - rendering of queue console segments.
* :class:`DropReason` - why an event was not delivered.
* :class:`DiagnosticEvent` - event names sent to the diagnostic hook.

System Role
-----------
Centralises configuration constants so validators, adapters, and CLI help
share a single source of truth, reducing string comparison bugs.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import TypeVar


class StrValueEnum(str, Enum):
    """String enum whose members format as their value on every Python version.

    A plain ``str, Enum`` member formats as its value on 3.10 but as
    ``Name.MEMBER`` on 3.11+, so one interpolated into a log line, a filename
    or a caller's f-string would change shape across the supported versions.
    This is the behaviour ``enum.StrEnum`` has on 3.11+, which the 3.10 floor
    cannot import.

    Example:
        >>> f"{QueuePolicy.DROP}" == str(QueuePolicy.DROP) == "drop"
        True

    """

    def __str__(self) -> str:
        """Return the member's value."""
        return str(self.value)

    def __format__(self, format_spec: str) -> str:
        """Format the member's value, so width and alignment specs apply to it."""
        return format(str(self.value), format_spec)


class QueuePolicy(StrValueEnum):
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


class ConsoleStream(StrValueEnum):
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


class GraylogProtocol(StrValueEnum):
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


class FormatPreset(StrValueEnum):
    """Named line layout for the console and for text dumps.

    The console renders every member. Text dumps render all but
    ``SHORT_LOC_ICON``, which needs a terminal for its icon.

    Example:
        >>> FormatPreset.from_str(' Short_Loc ') is FormatPreset.SHORT_LOC
        True

    """

    FULL = "full"
    SHORT = "short"
    FULL_LOC = "full_loc"
    SHORT_LOC = "short_loc"
    SHORT_LOC_ICON = "short_loc_icon"

    @classmethod
    def from_str(cls, value: str) -> FormatPreset:
        """Parse a human-entered preset name (case and surrounding space ignored).

        Raises:
            ValueError: If ``value`` names no preset.

        """
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        valid = ", ".join(repr(member.value) for member in cls)
        raise ValueError(f"Invalid format preset: {value!r}; must be one of {valid}")

    @property
    def renders_in_text_dump(self) -> bool:
        """Whether a text dump can render this preset (all but ``SHORT_LOC_ICON``).

        Example:
            >>> [preset.value for preset in FormatPreset if preset.renders_in_text_dump]
            ['full', 'short', 'full_loc', 'short_loc']

        """
        return self is not FormatPreset.SHORT_LOC_ICON


class ConsoleTheme(StrValueEnum):
    """Named console colour palette; ``CONSOLE_STYLE_THEMES`` holds one per member.

    Example:
        >>> ConsoleTheme.from_str(' Neon ') is ConsoleTheme.NEON
        True

    """

    CLASSIC = "classic"
    DARK = "dark"
    NEON = "neon"
    PASTEL = "pastel"

    @classmethod
    def from_str(cls, value: str) -> ConsoleTheme:
        """Parse a human-entered theme name (case and surrounding space ignored).

        Raises:
            ValueError: If ``value`` names no theme.

        Example:
            >>> ConsoleTheme.from_str('nope')
            Traceback (most recent call last):
            ...
            ValueError: Invalid console theme: 'nope'; must be one of 'classic', 'dark', 'neon', 'pastel'

        """
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        valid = ", ".join(repr(member.value) for member in cls)
        raise ValueError(f"Invalid console theme: {value!r}; must be one of {valid}")


class ExportStyle(StrValueEnum):
    """How a queue console adapter renders each segment it enqueues.

    Example:
        >>> ExportStyle.from_str('HTML') is ExportStyle.HTML
        True

    """

    ANSI = "ansi"
    HTML = "html"

    @classmethod
    def from_str(cls, value: str) -> ExportStyle:
        """Parse a human-entered export style (case and surrounding space ignored).

        Raises:
            ValueError: If ``value`` names no export style.

        """
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        raise ValueError(f"Invalid export style: {value!r}; must be 'ansi' or 'html'")


class DropReason(StrValueEnum):
    """Why an event was not delivered; ``ProcessResult.reason`` carries one.

    ``RATE_LIMITED``, ``QUEUE_FULL`` and ``ADAPTER_ERROR`` are also the keys
    of ``severity_snapshot().drops_by_reason``.
    """

    RATE_LIMITED = "rate_limited"
    QUEUE_FULL = "queue_full"
    QUEUE_FAILURE = "queue_failure"
    ADAPTER_ERROR = "adapter_error"
    LOGGER_LEVEL = "logger_level"


class DiagnosticEvent(StrValueEnum):
    """Event names the runtime sends to the diagnostic hook (see DIAGNOSTIC.md).

    The hook receives each name as a plain ``str`` equal to the member's value.
    """

    QUEUED = "queued"
    EMITTED = "emitted"
    RATE_LIMITED = "rate_limited"
    ADAPTER_ERROR = "adapter_error"
    QUEUE_FULL = "queue_full"
    QUEUE_DROPPED = "queue_dropped"
    QUEUE_DROP_CALLBACK_ERROR = "queue_drop_callback_error"
    QUEUE_WORKER_ERROR = "queue_worker_error"
    QUEUE_DEGRADED_DROP_MODE = "queue_degraded_drop_mode"
    QUEUE_SHUTDOWN_TIMEOUT = "queue_shutdown_timeout"
    EXTRA_INVALID = "extra_invalid"
    MESSAGE_FORMAT_FAILED = "message_format_failed"
    MESSAGE_TRUNCATED = "message_truncated"
    EXC_INFO_TRUNCATED = "exc_info_truncated"
    STACK_INFO_TRUNCATED = "stack_info_truncated"
    EXTRA_VALUE_TRUNCATED = "extra_value_truncated"
    EXTRA_VALUE_TRUNCATED_DEPTH_COLLAPSED = "extra_value_truncated_depth_collapsed"
    EXTRA_KEYS_DROPPED = "extra_keys_dropped"
    EXTRA_TOTAL_TRIMMED = "extra_total_trimmed"
    CONTEXT_EXTRA_VALUE_TRUNCATED = "context_extra_value_truncated"
    CONTEXT_EXTRA_VALUE_TRUNCATED_DEPTH_COLLAPSED = "context_extra_value_truncated_depth_collapsed"
    CONTEXT_EXTRA_KEYS_DROPPED = "context_extra_keys_dropped"
    CONTEXT_EXTRA_TOTAL_TRIMMED = "context_extra_total_trimmed"


_EnumT = TypeVar("_EnumT", bound=Enum)


def require_enum_member(value: object, enum_type: type[_EnumT], *, parameter: str) -> _EnumT:
    """Return ``value`` when it is a member of ``enum_type``, else raise ``TypeError``.

    Shared by the adapters, ``dump()`` and ``logdemo()``; deliberately left out of
    ``__all__`` because it is not part of the library's public API.

    These enums subclass ``str``, so a member compares and hashes equal to its
    value string. Without this check a plain ``"full"`` would still find its
    template by hash, and a plain ``"html"`` would fail every ``is`` test and
    silently render ANSI: the string half-works instead of being refused.

    Example:
        >>> require_enum_member(FormatPreset.FULL, FormatPreset, parameter='format_preset') is FormatPreset.FULL
        True
        >>> require_enum_member('full', FormatPreset, parameter='format_preset')
        Traceback (most recent call last):
        ...
        TypeError: format_preset must be a member of FormatPreset, got 'full'

    """
    if isinstance(value, enum_type):
        return value
    raise TypeError(f"{parameter} must be a member of {enum_type.__name__}, got {value!r}")


__all__ = [
    "ConsoleStream",
    "ConsoleTheme",
    "DiagnosticEvent",
    "DropReason",
    "ExportStyle",
    "FormatPreset",
    "GraylogProtocol",
    "QueuePolicy",
    "StrValueEnum",
]
