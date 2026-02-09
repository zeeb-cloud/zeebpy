"""Q objects for complex query filtering with AND, OR, NOT operations."""

from __future__ import annotations

from enum import Enum
from typing import Any


class QOperator(Enum):
    """Logical operators for combining Q objects."""

    AND = "AND"
    OR = "OR"


class Q:
    """
    Django-style Q object for complex query filtering.

    Supports AND (&), OR (|), and NOT (~) operations.

    Usage:
        Q(name='John')
        Q(age__gte=18) & Q(age__lte=65)
        Q(status='active') | Q(status='pending')
        ~Q(deleted=True)
        Q(name__startswith='A') & (Q(age__gt=20) | Q(role='admin'))
    """

    def __init__(
        self,
        *args: Q,
        _connector: QOperator = QOperator.AND,
        _negated: bool = False,
        **kwargs: Any,
    ) -> None:
        self.connector = _connector
        self.negated = _negated
        self.children: list[Q | tuple[str, Any]] = []

        # Add child Q objects
        for arg in args:
            if isinstance(arg, Q):
                self.children.append(arg)

        # Add keyword filter conditions
        for key, value in kwargs.items():
            self.children.append((key, value))

    def __and__(self, other: Q) -> Q:
        """Combine with AND operator."""
        if not isinstance(other, Q):
            return NotImplemented
        return self._combine(other, QOperator.AND)

    def __or__(self, other: Q) -> Q:
        """Combine with OR operator."""
        if not isinstance(other, Q):
            return NotImplemented
        return self._combine(other, QOperator.OR)

    def __invert__(self) -> Q:
        """Negate with NOT operator."""
        q = Q(_connector=self.connector, _negated=not self.negated)
        q.children = self.children.copy()
        return q

    def __rand__(self, other: Q) -> Q:
        """Support Q() & other."""
        return self.__and__(other)

    def __ror__(self, other: Q) -> Q:
        """Support Q() | other."""
        return self.__or__(other)

    def _combine(self, other: Q, connector: QOperator) -> Q:
        """Combine this Q with another using the given connector."""
        if not self.children:
            return other
        if not other.children:
            return self

        # Optimization: if both have same connector and aren't negated, merge children
        if (
            self.connector == connector
            and other.connector == connector
            and not self.negated
            and not other.negated
        ):
            q = Q(_connector=connector)
            q.children = self.children + other.children
            return q

        q = Q(_connector=connector)
        q.children = [self, other]
        return q

    def __repr__(self) -> str:
        prefix = "NOT " if self.negated else ""
        if len(self.children) == 1:
            child = self.children[0]
            if isinstance(child, tuple):
                return f"{prefix}Q({child[0]}={child[1]!r})"
            return f"{prefix}{child!r}"

        sep = f" {self.connector.value} "
        parts = []
        for child in self.children:
            if isinstance(child, tuple):
                parts.append(f"{child[0]}={child[1]!r}")
            else:
                parts.append(repr(child))
        inner = sep.join(parts)
        return f"{prefix}Q({inner})"

    def __bool__(self) -> bool:
        """Q object is truthy if it has any children."""
        return bool(self.children)

    def resolve(self) -> tuple[QOperator, bool, list[Q | tuple[str, Any]]]:
        """Resolve Q object into its components for query building."""
        return self.connector, self.negated, self.children

    def deconstruct(self) -> dict[str, Any]:
        """Deconstruct Q object for serialization."""
        return {
            "connector": self.connector.value,
            "negated": self.negated,
            "children": [
                (k, v) if isinstance(c, tuple) else c.deconstruct()
                for c in self.children
                for k, v in ([c] if isinstance(c, tuple) else [])
            ],
        }


# Lookup expressions supported
LOOKUP_EXPRESSIONS = {
    "exact": "__eq__",
    "iexact": "ilike",
    "contains": "contains",
    "icontains": "ilike",
    "in": "in_",
    "gt": "__gt__",
    "gte": "__ge__",
    "lt": "__lt__",
    "lte": "__le__",
    "startswith": "startswith",
    "istartswith": "istartswith",
    "endswith": "endswith",
    "iendswith": "iendswith",
    "range": "between",
    "isnull": "is_",
    "regex": "regexp_match",
    "iregex": "regexp_match",
}


def parse_lookup(lookup_string: str) -> tuple[str, str]:
    """
    Parse a Django-style lookup string into field name and lookup type.

    Examples:
        'name' -> ('name', 'exact')
        'name__exact' -> ('name', 'exact')
        'age__gte' -> ('age', 'gte')
        'author__name__contains' -> ('author__name', 'contains')
    """
    parts = lookup_string.rsplit("__", 1)

    if len(parts) == 1:
        return parts[0], "exact"

    field_path, lookup = parts
    if lookup in LOOKUP_EXPRESSIONS:
        return field_path, lookup

    # If the last part isn't a known lookup, it's part of the field path
    return lookup_string, "exact"
