"""Filtering helpers for dump snapshots.

Purpose
-------
Model reusable field predicates so dump workflows can filter buffered events
without leaking adapter logic into the application layer.

Contents
--------
* :class:`DumpFilter` – immutable aggregate of field filters for context and
  extra payloads.
* :class:`FieldFilter` – groups predicates applied to a single field with
  OR semantics.
* :func:`build_dump_filter` – parse user-friendly specifications into
  :class:`DumpFilter` instances.

System Role
-----------
Sits inside the domain layer to keep predicate evaluation pure, allowing the
application use case and runtime façade to compose filtering without
hard-coding the matching rules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from re import Pattern
from typing import Any, cast

from .context import LogContext
from .events import LogEvent


class PredicateKind(str, Enum):
    """Supported predicate types for field matching."""

    EXACT = "exact"
    CONTAINS = "contains"
    ICONTAINS = "icontains"
    REGEX = "regex"


@dataclass(slots=True, frozen=True)
class FieldPredicate:
    """Single predicate applied to a field value.

    Keeps predicate evaluation branch-free inside the matching loop while
    retaining enough metadata for error reporting and doctests.
    """

    kind: PredicateKind
    expected: Any
    pattern: re.Pattern[str] | None = None

    def matches(self, candidate: Any) -> bool:
        """Return ``True`` when ``candidate`` satisfies the predicate."""
        if self.kind is PredicateKind.EXACT:
            return candidate == self.expected
        text = _to_text(candidate)
        if text is None:
            return False
        if self.kind is PredicateKind.CONTAINS:
            return str(self.expected) in text
        if self.kind is PredicateKind.ICONTAINS:
            return str(self.expected).lower() in text.lower()
        if self.pattern is None:
            return False
        return bool(self.pattern.search(text))


@dataclass(slots=True, frozen=True)
class FieldFilter:
    """Collection of predicates evaluated against a single field."""

    field: str
    predicates: tuple[FieldPredicate, ...]

    def matches(self, candidate: Any) -> bool:
        """Return ``True`` when any predicate matches the candidate."""
        return any(predicate.matches(candidate) for predicate in self.predicates)


@dataclass(slots=True, frozen=True)
class DumpFilter:
    """Immutable filter describing which events to include in a dump.

    Allows the application use case to stay declarative, deferring predicate
    evaluation to the domain layer while keeping filtering configuration
    serialisable for diagnostics.

    Attributes:
        context: Filters applied to top-level :class:`LogContext` attributes.
        context_extra: Filters applied to ``LogContext.extra`` entries.
        extra: Filters applied to ``LogEvent.extra`` entries.

    """

    context: tuple[FieldFilter, ...] = ()
    context_extra: tuple[FieldFilter, ...] = ()
    extra: tuple[FieldFilter, ...] = ()

    def is_active(self) -> bool:
        """Return ``True`` when any filter is configured."""
        return bool(self.context or self.context_extra or self.extra)

    def matches(self, event: LogEvent) -> bool:
        """Return ``True`` when ``event`` satisfies all configured filters."""
        if not self.is_active():
            return True
        if not _match_context(event.context, self.context):
            return False
        if not _match_mapping(event.context.extra, self.context_extra):
            return False
        return _match_mapping(event.extra, self.extra)


FilterSpecValue = str | re.Pattern[str] | Mapping[str, Any] | Sequence[Any]
FilterSpec = Mapping[str, FilterSpecValue]


def build_dump_filter(
    *,
    context: FilterSpec | None = None,
    context_extra: FilterSpec | None = None,
    extra: FilterSpec | None = None,
) -> DumpFilter:
    """Create a DumpFilter from user-facing filter specifications.

    Specifications support exact match, contains, icontains, pattern, or sequences (OR).

    Args:
        context: Filter specifications for LogContext attributes.
        context_extra: Filter specifications for LogContext.extra entries.
        extra: Filter specifications for LogEvent.extra entries.

    Returns:
        Configured DumpFilter instance.

    Example:
        >>> filters = build_dump_filter(context={"service": "svc"}, extra={"request": {"icontains": "ABC"}})
        >>> filters.matches(event)  # doctest: +SKIP
        True

    """
    return DumpFilter(
        context=_build_field_filters(context or {}),
        context_extra=_build_field_filters(context_extra or {}),
        extra=_build_field_filters(extra or {}),
    )


def _build_field_filters(spec: FilterSpec) -> tuple[FieldFilter, ...]:
    """Convert mapping specifications into :class:`FieldFilter` tuples."""
    filters: list[FieldFilter] = []
    for field, raw in spec.items():
        predicates = tuple(_parse_predicate_options(field, raw))
        if not predicates:
            raise ValueError(f"No predicates defined for field {field!r}")
        filters.append(FieldFilter(field=field, predicates=predicates))
    return tuple(filters)


def _parse_predicate_options(field: str, raw: FilterSpecValue) -> Iterable[FieldPredicate]:
    """Yield predicates for ``field`` based on ``raw`` specification."""
    if isinstance(raw, (list, tuple, set)):
        iterable = cast(Iterable[Any], raw)
        for entry in iterable:
            yield from _parse_predicate_options(field, entry)
        return
    if isinstance(raw, re.Pattern):
        yield FieldPredicate(kind=PredicateKind.REGEX, expected=raw.pattern, pattern=raw)
        return
    if isinstance(raw, Mapping):
        yield _parse_mapping_predicate(field, raw)
        return
    yield FieldPredicate(kind=PredicateKind.EXACT, expected=raw)


_SIMPLE_PREDICATE_MODES: dict[str, PredicateKind] = {
    "exact": PredicateKind.EXACT,
    "contains": PredicateKind.CONTAINS,
    "icontains": PredicateKind.ICONTAINS,
}


def _parse_mapping_predicate(field: str, raw: Mapping[str, Any]) -> FieldPredicate:
    """Create a predicate from a mapping specification."""
    options = {key.lower(): value for key, value in raw.items()}
    kinds = [key for key in ("exact", "contains", "icontains", "pattern") if key in options]
    if len(kinds) != 1:
        raise ValueError(f"Field {field!r} must specify exactly one predicate mode; got {sorted(options)}")
    mode = kinds[0]
    if mode in _SIMPLE_PREDICATE_MODES:
        value = options[mode] if mode == "exact" else str(options[mode])
        return FieldPredicate(kind=_SIMPLE_PREDICATE_MODES[mode], expected=value)
    return _build_regex_predicate(field, options)


def _build_regex_predicate(field: str, options: Mapping[str, Any]) -> FieldPredicate:
    """Create a regex predicate after validating the specification."""
    if not options.get("regex"):
        raise ValueError(f"Field {field!r} must set 'regex': True to enable pattern filters")
    pattern = options.get("pattern")
    if isinstance(pattern, re.Pattern):
        compiled = cast(Pattern[str], pattern)
    else:
        if pattern is None:
            raise ValueError(f"Field {field!r} requires a 'pattern' value when regex is enabled")
        flags = _parse_regex_flags(options.get("flags"))
        compiled = re.compile(str(pattern), flags=flags)
    return FieldPredicate(kind=PredicateKind.REGEX, expected=compiled.pattern, pattern=compiled)


def _parse_regex_flags(raw: Any) -> int:
    """Return combined :mod:`re` flags from ``raw`` specifications."""
    if raw is None:
        return 0
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        return _FLAG_LOOKUP.get(raw.lower(), 0)
    if isinstance(raw, Sequence):
        flags = 0
        sequence = cast(Sequence[Any], raw)
        for entry in sequence:
            flags |= _parse_regex_flags(entry)
        return flags
    raise ValueError(f"Unsupported regex flag specification: {raw!r}")


_FLAG_LOOKUP: dict[str, int] = {name.lower(): getattr(re, name) for name in dir(re) if name.isupper() and isinstance(getattr(re, name), int)}


def _to_text(value: Any) -> str | None:
    """Return a textual representation for substring/regex predicates."""
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        return value.decode() if isinstance(value, bytes) else value
    return str(value)


def _match_context(ctx: LogContext, filters: tuple[FieldFilter, ...]) -> bool:
    """Return ``True`` when ``ctx`` satisfies every filter."""
    for field_filter in filters:
        candidate = getattr(ctx, field_filter.field, None)
        if not field_filter.matches(candidate):
            return False
    return True


def _match_mapping(payload: Mapping[str, Any], filters: tuple[FieldFilter, ...]) -> bool:
    """Return ``True`` when mapping ``payload`` satisfies every filter."""
    for field_filter in filters:
        candidate = payload.get(field_filter.field)
        if not field_filter.matches(candidate):
            return False
    return True


__all__ = [
    "DumpFilter",
    "FieldFilter",
    "FieldPredicate",
    "PredicateKind",
    "build_dump_filter",
]
