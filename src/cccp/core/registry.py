"""Generic type-safe registry pattern for pluggable components."""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Generic registry for pluggable components (backends, calculators, protocols).

    Items are stored keyed by lowercased name for case-insensitive lookup.
    """

    def __init__(self) -> None:
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T) -> None:
        """Register a component under *name* (case-insensitive)."""
        self._items[name.lower()] = item

    def get(self, name: str) -> T:
        """Retrieve a registered component by name.

        Raises KeyError if the name is not registered.
        """
        key = name.lower()
        if key not in self._items:
            available = list(self._items.keys())
            raise KeyError(f"Item '{name}' not registered. Available: {available}")
        return self._items[key]

    def has(self, name: str) -> bool:
        """Check if a component is registered (case-insensitive)."""
        return name.lower() in self._items

    def list_all(self) -> list[str]:
        """Return all registered component names."""
        return list(self._items.keys())

    def unregister(self, name: str) -> None:
        """Remove a registered component by name (no-op if missing)."""
        self._items.pop(name.lower(), None)
