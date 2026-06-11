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


def parse_path(
    model: type, lookup_string: str
) -> tuple[list[str], str, str | None, str]:
    """Parse a Django-style lookup path against ``model``.

    Walks the ``__``-separated parts of ``lookup_string``, consuming leading
    parts that resolve as relations on the current model (forward FK/O2O and
    reverse FK/O2O accessors), then a field name, then an optional datetime
    transform and an optional lookup operator.

    Returns ``(relation_parts, field_name, transform, lookup)`` where
    ``relation_parts`` is the list of relation accessors to traverse (may be
    empty), ``transform`` is a name from ``DATETIME_TRANSFORMS`` or ``None``
    and ``lookup`` is a key of ``LOOKUP_EXPRESSIONS`` (default ``"exact"``).

    Examples (Post has FK ``author``; Author has reverse accessor ``posts``):
        parse_path(Post, 'title')                  -> ([], 'title', None, 'exact')
        parse_path(Post, 'author__name')           -> (['author'], 'name', None, 'exact')
        parse_path(Post, 'author__name__contains') -> (['author'], 'name', None, 'contains')
        parse_path(Post, 'created_at__year__gte')  -> ([], 'created_at', 'year', 'gte')
        parse_path(Author, 'posts__views__gt')     -> (['posts'], 'views', None, 'gt')
        parse_path(Post, 'author')                 -> ([], 'author_id', None, 'exact')

    Raises:
        FieldError: When trailing parts are neither a valid transform nor a
            valid lookup, or a part after a relation is not a field on the
            related model.
    """
    from zeeb_orm.exceptions import FieldError
    from zeeb_orm.models.relations import resolve_relation
    from zeeb_orm.query.transforms import DATETIME_TRANSFORMS

    parts = lookup_string.split("__")
    relation_parts: list[str] = []
    current_model = model
    last_relation = None

    # 1. Consume leading relation accessors
    while parts:
        relation = resolve_relation(current_model, parts[0])
        if relation is None:
            break
        relation_parts.append(parts.pop(0))
        current_model = relation.target_model
        last_relation = relation

    # 2. The path ended on a relation itself, e.g. filter(author=obj)
    if not parts:
        if last_relation is not None and last_relation.kind in ("fk", "o2o"):
            # Forward relation: compare the local FK column directly (no join)
            relation_parts.pop()
            return relation_parts, last_relation.fk_column, None, "exact"
        # Reverse relation: join and compare the related model's PK
        return relation_parts, "pk", None, "exact"

    # 3. Next part is the field name
    field_name = parts.pop(0)
    if relation_parts:
        # After traversing a relation we can validate against the model
        meta = getattr(current_model, "_meta", None)
        if (
            field_name != "pk"
            and meta is not None
            and meta.get_field(field_name) is None
        ):
            field_names = sorted(f.name for f in meta.local_fields)
            raise FieldError(
                f"Cannot resolve keyword '{field_name}' into field on "
                f"{current_model.__name__}. Choices are: "
                f"{', '.join(field_names)}"
            )

    # 4. Remaining parts: optional transform, then optional lookup
    transform: str | None = None
    lookup = "exact"

    if len(parts) > 2:
        raise FieldError(
            f"Unsupported lookup path {lookup_string!r}: too many parts after "
            f"field '{field_name}'."
        )
    if len(parts) == 2:
        transform, lookup = parts
        if transform not in DATETIME_TRANSFORMS:
            raise FieldError(
                f"Unsupported transform {transform!r} for field '{field_name}' "
                f"in {lookup_string!r}. Choices are: "
                f"{', '.join(sorted(DATETIME_TRANSFORMS))}"
            )
        if lookup not in LOOKUP_EXPRESSIONS:
            raise FieldError(
                f"Unsupported lookup {lookup!r} for field '{field_name}' "
                f"in {lookup_string!r}. Choices are: "
                f"{', '.join(sorted(LOOKUP_EXPRESSIONS))}"
            )
    elif len(parts) == 1:
        part = parts[0]
        if part in LOOKUP_EXPRESSIONS:
            lookup = part
        elif part in DATETIME_TRANSFORMS:
            transform = part
        else:
            raise FieldError(
                f"Unsupported lookup or transform {part!r} for field "
                f"'{field_name}' in {lookup_string!r}. Choices are: "
                f"{', '.join(sorted(set(LOOKUP_EXPRESSIONS) | DATETIME_TRANSFORMS))}"
            )

    return relation_parts, field_name, transform, lookup
