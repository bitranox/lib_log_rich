"""Ports for time, identifiers, and unit-of-work semantics."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class ClockPort(Protocol):
    """Provide the current timestamp."""

    def now(self) -> datetime: ...


@runtime_checkable
class IdProvider(Protocol):
    """Generate unique identifiers for log events."""

    def __call__(self) -> str: ...


@runtime_checkable
class UnitOfWork(Protocol[T]):
    """Execute a callable within an adapter-managed transactional scope."""

    def run(self, fn: Callable[[], T]) -> T: ...


__all__ = ["ClockPort", "IdProvider", "UnitOfWork"]
