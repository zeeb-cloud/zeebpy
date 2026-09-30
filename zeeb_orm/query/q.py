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

    An empty ``Q()`` is the identity of ``&`` and ``|`` (it adds no
    condition), exactly as in Django — so it is *not* a "match everything"
    value: ``Q() | Q(x=1)`` is ``Q(x=1)`` and ``~Q()`` is still ``Q()``.
    Code that needs a condition which is true (or false) for every row uses
    :meth:`match_all` / :meth:`match_none`, which obey boolean algebra under
    every combinator.
    """

    #: ``None`` for an ordinary node; ``True`` / ``False`` for the
    #: :meth:`match_all` / :meth:`match_none` constants.
    _constant: bool | None = None

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

    # Constants

    @classmethod
    def match_all(cls) -> Q:
        """A condition true for every row.

        Unlike an empty ``Q()`` it is absorbing under OR
        (``match_all() | q`` is ``match_all()``) and negates to
        :meth:`match_none`.
        """
        q = cls()
        q._constant = True
        return q

    @classmethod
    def match_none(cls) -> Q:
        """A condition false for every row.

        Absorbing under AND (``match_none() & q`` is ``match_none()``) and
        negates to :meth:`match_all`.
        """
        q = cls()
        q._constant = False
        return q

    @property
    def is_match_all(self) -> bool:
        """True for the :meth:`match_all` constant."""
        return self._constant is True

    @property
    def is_match_none(self) -> bool:
        """True for the :meth:`match_none` constant."""
        return self._constant is False

    def _is_empty(self) -> bool:
        """An empty ``Q()`` — no children and not a constant."""
        return not self.children and self._constant is None

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
        if self._constant is not None:
            return Q.match_none() if self._constant else Q.match_all()
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
        # An empty Q() is the identity on either side (Django parity).
        if self._is_empty():
            return other
        if other._is_empty():
            return self

        # Constants follow boolean algebra: match_all absorbs OR and is the
        # identity of AND; match_none absorbs AND and is the identity of OR.
        for constant, rest in ((self, other), (other, self)):
            if constant._constant is None:
                continue
            absorbing = constant._constant is (connector is QOperator.OR)
            return constant if absorbing else rest

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
        if self._constant is not None:
            return "Q.match_all()" if self._constant else "Q.match_none()"
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
        """Q object is truthy if it has any children (or is a constant)."""
        return bool(self.children) or self._constant is not None

    def resolve(self) -> tuple[QOperator, bool, list[Q | tuple[str, Any]]]:
        """Resolve Q object into its components for query building."""
        return self.connector, self.negated, self.children

    def deconstruct(self) -> dict[str, Any]:
        """Deconstruct Q object for serialization.

        Leaf conditions stay ``(lookup, value)`` tuples; nested Q objects
        are deconstructed recursively, in their original position.  The
        :meth:`match_all` / :meth:`match_none` constants carry an extra
        ``"match": "all" | "none"`` key.
        """
        data: dict[str, Any] = {
            "connector": self.connector.value,
            "negated": self.negated,
            "children": [
                child if isinstance(child, tuple) else child.deconstruct()
                for child in self.children
            ],
        }
        if self._constant is not None:
            data["match"] = "all" if self._constant else "none"
        return data


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


def _resolves_as_field(model: type, name: str) -> bool:
    """Whether ``name`` addresses a field on ``model``.

    Accepts both the Meta-level field name and the database column name, so a
    ForeignKey declared as ``guild`` is reachable as ``guild`` *and* as
    ``guild_id`` — the same convention ``JoinContext.column`` already applies
    when it resolves the column.
    """
    meta = getattr(model, "_meta", None)
    if meta is None:
        return False
    return meta.get_field(name) is not None or meta.get_field_by_column(name) is not None


def parse_path(model: type, lookup_string: str) -> tuple[list[str], str, str | None, str]:
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
        parse_path(Post, 'author__in')             -> ([], 'author_id', None, 'in')
        parse_path(Author, 'posts__isnull')        -> (['posts'], 'pk', None, 'isnull')
        parse_path(Post, 'author__profile_id')     -> (['author'], 'profile_id', None, 'exact')

    A relation may be followed directly by a lookup operator
    (``author__in``, ``author__isnull``); the relation then resolves the same
    way as a bare terminal relation — the local FK column for forward
    FK/O2O (no join), the related model's PK for reverse and M2M accessors.
    A field of the same name on the related model takes precedence over the
    lookup interpretation.

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

    # 2. The path ended on a relation itself, e.g. filter(author=obj), or on a
    #    relation plus a lookup operator, e.g. filter(author__in=[...]).
    terminal_lookup: str | None = None
    if not parts:
        terminal_lookup = "exact"
    elif (
        relation_parts
        and len(parts) == 1
        and parts[0] in LOOKUP_EXPRESSIONS
        and not _resolves_as_field(current_model, parts[0])
    ):
        # A field on the related model wins over a lookup of the same name.
        terminal_lookup = parts[0]

    if terminal_lookup is not None:
        if last_relation is not None and last_relation.kind in ("fk", "o2o"):
            # Forward relation: compare the local FK column directly (no join)
            relation_parts.pop()
            return relation_parts, last_relation.fk_column, None, terminal_lookup
        # Reverse/M2M relation: join and compare the related model's PK
        return relation_parts, "pk", None, terminal_lookup

    # 3. Next part is the field name
    field_name = parts.pop(0)
    if relation_parts:
        # After traversing a relation we can validate against the model
        meta = getattr(current_model, "_meta", None)
        if (
            field_name != "pk"
            and meta is not None
            and not _resolves_as_field(current_model, field_name)
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
            f"Unsupported lookup path {lookup_string!r}: too many parts after field '{field_name}'."
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
