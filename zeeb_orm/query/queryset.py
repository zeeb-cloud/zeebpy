"""QuerySet implementation with lazy evaluation and chaining."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
)

from sqlalchemy import (
    ClauseElement,
    Executable,
    Select,
    and_,
    delete,
    func,
    literal_column,
    not_,
    or_,
    select,
    update,
)
from sqlalchemy.ext.compiler import compiles

from zeeb_orm.query.q import Q, QOperator, parse_path

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model
    from zeeb_orm.query.joins import JoinContext

ModelT = TypeVar("ModelT", bound="Model")


class QuerySet(Generic[ModelT]):
    """
    Django-style QuerySet with lazy evaluation.

    QuerySets are lazy - they don't hit the database until evaluated.
    Each filter/exclude/order_by operation returns a new QuerySet.

    Usage:
        qs = User.objects.filter(active=True)  # No DB hit
        qs = qs.filter(age__gte=18)  # Still no DB hit
        users = await qs  # DB hit here
        # or
        async for user in qs:  # DB hit here
            print(user)
    """

    def __init__(self, model: type[ModelT]) -> None:
        self.model = model
        self._query: Select[tuple[ModelT]] | None = None
        self._filters: list[Q] = []
        self._excludes: list[Q] = []
        self._order_by: list[str] = []
        # False once order_by() was called: Meta.ordering no longer applies,
        # so a bare order_by() clears the ordering altogether.
        self._default_ordering: bool = True
        self._distinct_fields: list[str] = []
        self._select_related: list[str] = []
        self._prefetch_related: list[Any] = []
        self._annotations: dict[str, Any] = {}
        self._limit: int | None = None
        self._offset: int | None = None
        self._only_fields: list[str] | None = None
        self._defer_fields: list[str] | None = None
        # `_values_mode` is what makes rows come back as dicts; `_values_fields`
        # stays None for a bare `.values()` (= all local fields), so the two
        # cannot be collapsed into one attribute.
        self._values_mode: bool = False
        self._values_fields: list[str] | None = None
        self._values_list_fields: list[str] | None = None
        self._flat: bool = False
        # Annotations a named values()/values_list() outputs besides the ones
        # it names: those added after it. None = every annotation (model
        # instances, or a values()/values_list() called without fields).
        self._values_annotations: list[str] | None = None
        # The values() names an aggregation groups by, fixed when the
        # aggregate is annotated; None = whole rows (the primary key).
        self._group_by_fields: list[str] | None = None
        self._db_alias: str | None = None
        self._raw_sql: str | None = None
        self._raw_params: list[Any] | None = None
        self._combinator: str | None = None
        self._combined_querysets: list[QuerySet[Any]] = []
        self._for_update: dict[str, Any] | None = None
        self._is_empty: bool = False
        self._result_cache: list[ModelT] | None = None

    @classmethod
    def as_manager(cls) -> Any:
        """Return a Manager instance built from this QuerySet class.

        Equivalent to ``Manager.from_queryset(cls)()`` — the manager's
        ``get_queryset()`` returns instances of this QuerySet class and all
        public custom methods are proxied onto the manager.

        Usage:
            class PostQuerySet(QuerySet):
                def published(self):
                    return self.filter(published=True)

            class Post(Model):
                objects = PostQuerySet.as_manager()
        """
        from zeeb_orm.models.manager import Manager

        return Manager.from_queryset(cls)()

    def _clone(self) -> QuerySet[ModelT]:
        """Create a copy of this QuerySet (preserving the QuerySet subclass)."""
        clone = self.__class__(self.model)
        clone._filters = self._filters.copy()
        clone._excludes = self._excludes.copy()
        clone._order_by = self._order_by.copy()
        clone._default_ordering = self._default_ordering
        clone._distinct_fields = self._distinct_fields.copy()
        clone._select_related = self._select_related.copy()
        clone._prefetch_related = self._prefetch_related.copy()
        clone._annotations = self._annotations.copy()
        clone._limit = self._limit
        clone._offset = self._offset
        clone._only_fields = self._only_fields.copy() if self._only_fields else None
        clone._defer_fields = self._defer_fields.copy() if self._defer_fields else None
        clone._values_mode = self._values_mode
        clone._values_fields = self._values_fields.copy() if self._values_fields else None
        clone._values_list_fields = (
            self._values_list_fields.copy() if self._values_list_fields else None
        )
        clone._flat = self._flat
        clone._values_annotations = (
            list(self._values_annotations) if self._values_annotations is not None else None
        )
        clone._group_by_fields = (
            list(self._group_by_fields) if self._group_by_fields is not None else None
        )
        clone._db_alias = self._db_alias
        clone._raw_sql = self._raw_sql
        clone._raw_params = self._raw_params.copy() if self._raw_params else None
        clone._combinator = self._combinator
        clone._combined_querysets = self._combined_querysets.copy()
        clone._for_update = dict(self._for_update) if self._for_update else None
        clone._is_empty = self._is_empty
        return clone

    def _check_combinator(self, method: str) -> None:
        """Disallow ``method`` after union()/intersection()/difference().

        Only order_by(), slicing (limit/offset) and values()/values_list()
        may be applied to a combined queryset (Django parity).
        """
        if self._combinator is not None:
            from zeeb_orm.exceptions import NotSupportedError

            op = "union" if self._combinator == "union_all" else self._combinator
            raise NotSupportedError(f"Calling QuerySet.{method}() after {op}() is not supported.")

    def _invalidate_cache(self) -> None:
        """Invalidate the result cache."""
        self._result_cache = None

    # Filtering methods

    def all(self) -> QuerySet[ModelT]:
        """
        Return a fresh copy of this QuerySet with an empty result cache.

        Mirrors Manager.all(): a class-level queryset shared across requests
        would otherwise serve its cached results forever.
        """
        return self._clone()

    def none(self) -> QuerySet[ModelT]:
        """Return a QuerySet guaranteed to match nothing.

        The safe base for "this caller may see no rows" — an owner-scoped
        ``get_queryset`` for an anonymous request, say. Filtering on a sentinel
        value instead is a correctness trap: a nullable owner column would make
        ``filter(owner_id=None)`` return every unowned row.

        Chaining stays sound: the empty marker survives ``_clone``, so
        ``.none().filter(...)`` is still empty.
        """
        clone = self._clone()
        clone._is_empty = True
        return clone

    @staticmethod
    def _call_q(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Q | None:
        """One Q for everything a single filter()/exclude() call received.

        Keeping one entry per call is what lets the join machinery apply
        Django's multi-valued rule (conditions of one call refer to the same
        related row, a later call gets its own join) and makes
        ``exclude(a, b)`` mean ``NOT (a AND b)``.
        """
        q_args = [arg for arg in args if isinstance(arg, Q)]
        if not kwargs and len(q_args) == 1:
            return q_args[0]
        if not kwargs and not q_args:
            return None
        return Q(*q_args, **kwargs)

    def filter(self, *args: Q, **kwargs: Any) -> QuerySet[ModelT]:
        """
        Return a new QuerySet with the given filters applied.

        Accepts Q objects and/or keyword arguments.

        Across a multi-valued relation (reverse FK, M2M) the conditions of
        one ``filter()`` call must hold for the same related row, while each
        further ``filter()`` call is matched against any related row
        (Django semantics)::

            .filter(tags__name="a", tags__color="red")    # one red tag named a
            .filter(tags__name="a").filter(tags__name="b")  # tagged a and b

        Usage:
            .filter(name='John')
            .filter(age__gte=18)
            .filter(Q(active=True) | Q(role='admin'))
        """
        self._check_combinator("filter")
        clone = self._clone()
        q = self._call_q(args, kwargs)
        if q is not None:
            clone._filters.append(q)
        return clone

    def exclude(self, *args: Q, **kwargs: Any) -> QuerySet[ModelT]:
        """
        Return a new QuerySet excluding objects matching the given filters.

        ``exclude(a, b)`` removes the rows matching ``a AND b``. Rows where a
        compared column is NULL are kept (``exclude(x=1)`` keeps ``x IS
        NULL``), and a condition across a multi-valued relation excludes the
        objects that have *any* matching related row — objects without
        related rows stay (Django semantics).

        Usage:
            .exclude(deleted=True)
            .exclude(Q(status='inactive'))
            .exclude(tags__name='draft')   # no tag named "draft"
        """
        self._check_combinator("exclude")
        clone = self._clone()
        q = self._call_q(args, kwargs)
        if q is not None:
            clone._excludes.append(q)
        return clone

    # Permission filtering

    def with_permission(self, user: Any, action: str) -> QuerySet[ModelT]:
        """
        Filter queryset by permission for a given action.

        Uses the model's permission rules to generate appropriate filters.

        Args:
            user: User to check permissions for (can be None for anonymous)
            action: Permission action ('read', 'change', 'delete')

        Usage:
            Post.objects.with_permission(user, 'read')
            Post.objects.with_permission(user, 'change').filter(status='draft')
        """
        permission_attr = f"{action}_permission"
        filter_method = f"get_{action}_filter"

        # Check if model has the filter method
        if hasattr(self.model, filter_method):
            q_filter = getattr(self.model, filter_method)(user)
            return self.filter(q_filter)

        # Check if model has the permission rule
        if hasattr(self.model, "_permission_rules"):
            rules = getattr(self.model, "_permission_rules", {})
            if permission_attr in rules:
                rule = rules[permission_attr]
                q_filter = rule.to_q(user, self.model)
                return self.filter(q_filter)

        # No permission defined - return unfiltered
        return self._clone()

    def readable_by(self, user: Any) -> QuerySet[ModelT]:
        """
        Filter queryset to objects readable by the given user.

        Shortcut for .with_permission(user, 'read')

        Usage:
            Post.objects.readable_by(user)
            Post.objects.readable_by(request.user).filter(featured=True)
        """
        return self.with_permission(user, "read")

    def changeable_by(self, user: Any) -> QuerySet[ModelT]:
        """
        Filter queryset to objects changeable by the given user.

        Shortcut for .with_permission(user, 'change')

        Usage:
            Post.objects.changeable_by(user)
        """
        return self.with_permission(user, "change")

    def deletable_by(self, user: Any) -> QuerySet[ModelT]:
        """
        Filter queryset to objects deletable by the given user.

        Shortcut for .with_permission(user, 'delete')

        Usage:
            Post.objects.deletable_by(user)
        """
        return self.with_permission(user, "delete")

    # Ordering

    def order_by(self, *fields: str) -> QuerySet[ModelT]:
        """
        Return a new QuerySet ordered by the given fields.

        Prefix with '-' for descending order.

        Usage:
            .order_by('name')
            .order_by('-created_at')
            .order_by('last_name', 'first_name')
        """
        clone = self._clone()
        clone._order_by = list(fields)
        clone._default_ordering = False
        return clone

    # Distinct

    def distinct(self, *fields: str) -> QuerySet[ModelT]:
        """Return a new QuerySet with distinct results.

        Field arguments (``DISTINCT ON``) are not supported; call
        ``distinct()`` with no arguments for row-level de-duplication.
        """
        self._check_combinator("distinct")
        if fields:
            from zeeb_orm.exceptions import NotSupportedError

            raise NotSupportedError(
                "distinct(*fields) is not supported - call distinct() with "
                "no arguments for row-level DISTINCT."
            )
        clone = self._clone()
        clone._distinct_fields = ["*"]
        return clone

    # Slicing / Limiting

    def __getitem__(self, key: int | slice) -> QuerySet[ModelT] | Any:
        """Support slicing: qs[0], qs[5:10], qs[:5]"""
        clone = self._clone()
        if isinstance(key, slice):
            if key.start is not None:
                clone._offset = key.start
            if key.stop is not None:
                if key.start is not None:
                    clone._limit = key.stop - key.start
                else:
                    clone._limit = key.stop
            return clone
        elif isinstance(key, int):
            if key < 0:
                raise ValueError("Negative indexing is not supported")
            clone._offset = key
            clone._limit = 1
            return clone
        raise TypeError(f"QuerySet indices must be integers or slices, not {type(key).__name__}")

    # Field selection

    def only(self, *fields: str) -> QuerySet[ModelT]:
        """Load only the specified fields."""
        self._check_combinator("only")
        clone = self._clone()
        clone._only_fields = list(fields)
        return clone

    def defer(self, *fields: str) -> QuerySet[ModelT]:
        """Defer loading of the specified fields."""
        self._check_combinator("defer")
        clone = self._clone()
        clone._defer_fields = list(fields)
        return clone

    def values(self, *fields: str) -> QuerySet[ModelT]:
        """Return dictionaries instead of model instances.

        With no arguments every local field is returned (FK fields under
        their ``<name>_id`` column name). Named fields restrict the SELECT
        list and are labeled exactly as given, so ``values("pk")`` and
        ``values("author")`` come back under those keys.

        Annotations: without arguments every annotation is included. Named
        fields include the annotations they name plus every annotation added
        by a later ``annotate()``; an earlier annotation left unnamed is not
        selected. An aggregation keeps the grouping it had when it was
        annotated, so ``values("tool").annotate(n=Count("id")).values("n")``
        is still one row per tool.
        """
        clone = self._clone()
        clone._values_mode = True
        clone._values_fields = list(fields) if fields else None
        clone._values_list_fields = None
        clone._flat = False
        clone._values_annotations = [] if fields else None
        return clone

    def values_list(self, *fields: str, flat: bool = False) -> QuerySet[ModelT]:
        """Return tuples instead of model instances.

        With no fields, all model fields are returned in declaration order
        (FK fields as their ``<name>_id`` column value). Annotations follow
        the rules of ``values()``; the ones not named come after the fields,
        in the order they were annotated, so
        ``values_list("tool").annotate(n=Count("id"))`` yields
        ``("x", 2)``. ``flat=True`` yields the first value of each tuple.
        """
        clone = self._clone()
        clone._values_annotations = [] if fields else None
        if not fields:
            fields = tuple(f.db_column or f.name for f in self.model._meta.local_fields)
        if flat and len(fields) > 1:
            raise TypeError(
                "'flat' is not valid when values_list is called with more than one field."
            )
        clone._values_list_fields = list(fields)
        clone._flat = flat
        clone._values_mode = False
        clone._values_fields = None
        return clone

    # Related object loading

    def select_related(self, *fields: str) -> QuerySet[ModelT]:
        """
        Load related objects in the same query using JOINs.

        Usage:
            Post.objects.select_related('author')
            Post.objects.select_related('author', 'category')
        """
        self._check_combinator("select_related")
        clone = self._clone()
        clone._select_related = list(fields)
        return clone

    def prefetch_related(self, *lookups: Any) -> QuerySet[ModelT]:
        """
        Prefetch related objects in separate queries.

        Usage:
            Author.objects.prefetch_related('posts')
            Author.objects.prefetch_related(Prefetch('posts', queryset=...))
        """
        self._check_combinator("prefetch_related")
        clone = self._clone()
        clone._prefetch_related = list(lookups)
        return clone

    # Annotations and Aggregations

    def annotate(self, **kwargs: Any) -> QuerySet[ModelT]:
        """
        Add computed fields to each result.

        Every value must be an expression (``F()``, ``Value()``, an
        aggregate, ``Case``, ``Subquery``, …). A plain string is rejected
        with ``TypeError`` rather than rendered into the SQL: use
        ``F("field")`` to reference a field and ``Value("text")`` for a
        constant.

        Usage:
            Author.objects.annotate(post_count=Count('posts'))
        """
        self._check_combinator("annotate")
        _require_expressions("annotate", kwargs)
        clone = self._clone()
        clone._annotations.update(kwargs)
        names = clone._requested_value_names()
        if clone._values_annotations is not None:
            clone._values_annotations.extend(
                alias
                for alias in kwargs
                if alias not in (names or ()) and alias not in clone._values_annotations
            )
        if any(expr.contains_aggregate for expr in kwargs.values()):
            # The grouping is fixed here, from the fields selected now: a
            # later values() changes what is returned, not what is grouped.
            clone._group_by_fields = (
                None if names is None else [n for n in names if n not in clone._annotations]
            )
        return clone

    async def aggregate(self, **kwargs: Any) -> dict[str, Any]:
        """
        Compute aggregate values over the entire QuerySet.

        A plain (filtered) queryset is aggregated in one ``SELECT``. A sliced,
        ``distinct()`` or aggregating one, or one whose annotation the
        aggregates read, is aggregated over its own query as a subquery, so
        ``qs.order_by("price")[:10].aggregate(Sum("price"))`` sums those ten
        rows and ``qs.annotate(n=Count("books")).aggregate(Avg("n"))``
        averages the annotation. Over a subquery an aggregate can name the
        columns and annotations that query selects, not traverse relations.

        Usage:
            await Author.objects.aggregate(avg_age=Avg('age'))
        """
        self._check_combinator("aggregate")
        from zeeb_orm.db.connection import get_session

        _require_expressions("aggregate", kwargs)
        stmt = self._build_aggregate_select(kwargs)

        async with get_session(self._db_alias) as (session, _):
            result = await session.execute(stmt)
            row = result.fetchone()
            if row:
                return dict(row._mapping)
            return {alias: None for alias in kwargs}

    def _build_aggregate_select(self, aggregates: dict[str, Any]) -> Any:
        """The ``SELECT`` behind ``aggregate()``.

        A subquery when the rows to reduce are not simply "the filtered
        table": a slice, ``distinct()``, an aggregation (its GROUP BY and
        HAVING), or an annotation the aggregates read. An annotation nothing
        reads changes no row and keeps the single SELECT, which can still
        traverse relations.
        """
        if (
            self._limit is not None
            or self._offset is not None
            or self._distinct_fields
            or self._aggregate_aliases()
            or set(_referenced_names(aggregates.values())) & set(self._annotations)
        ):
            return self._build_subquery_aggregate(aggregates)

        table = self.model._get_table()
        joins = self._make_join_context()
        select_exprs = [
            agg.resolve(self.model, joins=joins).label(alias) for alias, agg in aggregates.items()
        ]

        # Apply filters (may register traversal JOINs)
        where_clause = self._build_where_clause(joins)

        stmt = select(*select_exprs).select_from(joins.apply(table) if joins.has_joins else table)
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        return stmt

    def _build_subquery_aggregate(self, aggregates: dict[str, Any]) -> Any:
        """Aggregate over this queryset's own ``SELECT`` as a subquery.

        The inner query keeps the slice (and the ordering that defines it),
        ``distinct()``, the GROUP BY of an aggregation and its HAVING; the
        outer aggregates read its columns by name. An annotation the
        aggregates name is selected even where ``values()`` would leave it
        out. ``select_related()`` is dropped: it adds columns, not rows.
        """
        from zeeb_orm.exceptions import FieldError

        inner_qs = self._clone()
        inner_qs._select_related = []
        inner_qs._prefetch_related = []
        if inner_qs._values_annotations is not None:
            named = set(_referenced_names(aggregates.values()))
            inner_qs._values_annotations += [
                alias
                for alias in inner_qs._annotations
                if alias in named and alias not in inner_qs._values_annotations
            ]

        inner = inner_qs._build_select()
        if self._limit is None and self._offset is None:
            inner = inner.order_by(None)  # ordering cannot change an aggregate
        subquery = inner.subquery("_aggregate")
        scope = _SubqueryScope(self.model, subquery)

        select_exprs = []
        for alias, agg in aggregates.items():
            try:
                resolved = agg.resolve(scope, joins=None)
            except (FieldError, ValueError) as exc:
                available = ", ".join(subquery.c.keys())
                raise FieldError(
                    f"Cannot compute {alias}={agg!r} over this sliced, distinct or "
                    f"annotated queryset: an aggregate can only read the columns its "
                    f"query selects ({available}), not traverse relations. {exc}"
                ) from exc
            select_exprs.append(resolved.label(alias))
        return select(*select_exprs).select_from(subquery)

    # Database selection

    def using(self, alias: str) -> QuerySet[ModelT]:
        """Use a specific database connection."""
        clone = self._clone()
        clone._db_alias = alias
        return clone

    # Raw SQL

    def raw(
        self, sql: str, params: list[Any] | tuple[Any, ...] | dict[str, Any] | None = None
    ) -> QuerySet[ModelT]:
        """Execute a raw SQL query and return model instances.

        Values are always bound parameters, never spliced into the SQL:

        * a list/tuple binds positionally to ``?`` placeholders —
          ``raw("SELECT * FROM t WHERE a = ? AND b = ?", [1, 2])``;
        * a dict binds by name to ``:name`` placeholders —
          ``raw("SELECT * FROM t WHERE a = :a", {"a": 1})``.

        String literals, quoted identifiers and comments are left alone: a
        ``?`` or ``:word`` inside ``'...'`` is text, not a placeholder. The
        number of ``?`` placeholders must match the number of values
        (``ValueError`` otherwise).
        """
        self._check_combinator("raw")
        clone = self._clone()
        clone._raw_sql = sql
        clone._raw_params = params
        return clone

    # Combinators (UNION / INTERSECT / EXCEPT)

    def _combinator_query(self, combinator: str, *others: QuerySet[Any]) -> QuerySet[ModelT]:
        for other in others:
            if not isinstance(other, QuerySet):
                raise TypeError(
                    f"Combinator arguments must be QuerySets, got {type(other).__name__}"
                )
        # The combined queryset is a fresh wrapper holding all components
        # (including self).  This makes chained combinations nest naturally.
        clone: QuerySet[ModelT] = self.__class__(self.model)
        clone._db_alias = self._db_alias
        clone._combinator = combinator
        clone._combined_querysets = [self._clone(), *others]
        return clone

    def union(self, *other_qs: QuerySet[Any], all: bool = False) -> QuerySet[ModelT]:
        """Combine with other querysets using SQL UNION.

        With ``all=True`` duplicates are kept (UNION ALL).
        After combining, only ``order_by()``, slicing and
        ``values()``/``values_list()`` may be applied.
        """
        if not other_qs:
            return self._clone()
        return self._combinator_query("union_all" if all else "union", *other_qs)

    def intersection(self, *other_qs: QuerySet[Any]) -> QuerySet[ModelT]:
        """Combine with other querysets using SQL INTERSECT.

        Note: not supported on MySQL < 8.0.31.
        """
        if not other_qs:
            return self._clone()
        return self._combinator_query("intersect", *other_qs)

    def difference(self, *other_qs: QuerySet[Any]) -> QuerySet[ModelT]:
        """Combine with other querysets using SQL EXCEPT.

        Note: not supported on MySQL < 8.0.31.
        """
        if not other_qs:
            return self._clone()
        return self._combinator_query("except", *other_qs)

    def _build_component_select(self) -> Select[Any]:
        """SELECT for one side of a set operation.

        Uses an EXPLICIT column list in ``_meta`` field order (never
        ``select(table)``) so all components have an identical, positionally
        deterministic column layout.
        """
        table = self.model._get_table()
        joins = self._make_join_context()
        columns = [table.c[f.db_column or f.name] for f in self.model._meta.local_fields]
        where_clause = self._build_where_clause(joins)

        stmt = select(*columns)
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        if joins.has_joins:
            stmt = stmt.select_from(joins.apply(table))
        return stmt

    def _combined_order_column(self, field_name: str) -> Any:
        """Map a field name to a literal column usable after a set operation."""
        from zeeb_orm.exceptions import FieldError

        name = field_name
        if name == "pk":
            name = self.model._meta.pk_name or "id"
        field = self.model._meta.get_field(name)
        if field is None:
            # Only resolved real model fields may reach literal_column: the raw
            # string is rendered verbatim into SQL (no quoting/parameterization),
            # so an unrecognized name would be a SQL-injection sink. Mirror the
            # non-combined ORDER BY path, which rejects unknown fields.
            raise FieldError(f"Cannot order combined queryset by unknown field {field_name!r}")
        return literal_column(field.db_column or field.name)

    def _build_combined_select(self) -> Any:
        """Build the compound (UNION/INTERSECT/EXCEPT) statement."""
        from sqlalchemy import asc, desc, except_, intersect, union, union_all

        components = [self._build_component_for(qs) for qs in self._combined_querysets]

        op = {
            "union": union,
            "union_all": union_all,
            "intersect": intersect,
            "except": except_,
        }[self._combinator]
        combined = op(*components)

        # order_by / limit / offset apply to the compound statement;
        # ordering uses literal column names (valid after a set operation).
        if self._order_by:
            clauses = []
            for f in self._order_by:
                descending = f.startswith("-")
                col = self._combined_order_column(f[1:] if descending else f)
                clauses.append(desc(col) if descending else asc(col))
            combined = combined.order_by(*clauses)
        if self._limit is not None:
            combined = combined.limit(self._limit)
        if self._offset is not None:
            combined = combined.offset(self._offset)
        return combined

    @staticmethod
    def _build_component_for(qs: QuerySet[Any]) -> Any:
        """Component statement for ``qs`` (recursing into nested combinators)."""
        if qs._combinator is not None:
            return qs._build_combined_select()
        return qs._build_component_select()

    # Row locking

    def select_for_update(
        self,
        *,
        nowait: bool = False,
        skip_locked: bool = False,
        of: tuple[str, ...] | list[str] = (),
    ) -> QuerySet[ModelT]:
        """Lock the selected rows with ``SELECT ... FOR UPDATE``.

        Must be evaluated inside an ``atomic()`` block and is not supported
        on SQLite.  ``nowait`` and ``skip_locked`` map to the corresponding
        SQL options; ``of`` restricts locking to the given relations
        (``"self"`` refers to the queryset's own table).
        """
        clone = self._clone()
        clone._for_update = {
            "nowait": nowait,
            "skip_locked": skip_locked,
            "of": tuple(of),
        }
        return clone

    def _validate_for_update(self, dialect_name: str) -> None:
        """Check that select_for_update may run (dialect + transaction)."""
        from zeeb_orm.exceptions import NotSupportedError, TransactionManagementError

        if dialect_name == "sqlite":
            raise NotSupportedError("select_for_update is not supported on SQLite.")
        if _active_session_on(self._db_alias) is None:
            # A transaction open on another database does not hold locks here.
            raise TransactionManagementError(
                "select_for_update cannot be used outside of a transaction on "
                "its database. Wrap the query in "
                f"'async with atomic({self._db_alias!r}):'."
                if self._db_alias
                else "select_for_update cannot be used outside of a transaction. "
                "Wrap the query in 'async with atomic():'."
            )

    def _apply_for_update(self, stmt: Any) -> Any:
        """Apply the stored FOR UPDATE options to ``stmt``."""
        assert self._for_update is not None
        kwargs: dict[str, Any] = {}
        if self._for_update["nowait"]:
            kwargs["nowait"] = True
        if self._for_update["skip_locked"]:
            kwargs["skip_locked"] = True
        of = self._for_update["of"]
        if of:
            table = self.model._get_table()
            of_targets = []
            for name in of:
                if name == "self":
                    of_targets.append(table)
                else:
                    from zeeb_orm.models.relations import resolve_relation

                    relation = resolve_relation(self.model, name)
                    if relation is None:
                        raise ValueError(
                            f"select_for_update(of=...): {name!r} is not a "
                            f"relation on {self.model.__name__}"
                        )
                    of_targets.append(relation.target_model._get_table())
            kwargs["of"] = of_targets
        return stmt.with_for_update(**kwargs)

    # Query building

    def _make_join_context(self) -> JoinContext:
        """Create a fresh per-statement JoinContext (never stored on self)."""
        from zeeb_orm.query.joins import JoinContext

        return JoinContext(self.model, self.model._get_table())

    def _pk_in_join_subquery(self, table: Any, joins: JoinContext, where_clause: Any) -> Any:
        """``pk IN (SELECT pk FROM <joined tables> WHERE ...)`` condition.

        Used to rewrite UPDATE/DELETE statements whose filters traverse
        relations (UPDATE/DELETE cannot use JOINs portably).
        """
        pk_col_name = self.model._meta.pk.db_column or self.model._meta.pk_name
        pk_col = table.c[pk_col_name]
        subq = select(pk_col).select_from(joins.apply(table))
        if where_clause is not None:
            subq = subq.where(where_clause)
        return pk_col.in_(subq)

    def _build_where_clause(self, joins: JoinContext | None = None) -> Any:
        """Build SQLAlchemy WHERE clause from filters and excludes."""
        if self._is_empty:
            from sqlalchemy import false

            return false()

        conditions = []

        for index, q in enumerate(self._filters):
            condition = self._q_to_condition(q, joins, scope=("filter", index))
            if condition is not None:
                conditions.append(condition)

        for index, q in enumerate(self._excludes):
            condition = self._exclude_condition(q, joins, scope=("exclude", index))
            if condition is not None:
                conditions.append(condition)

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return and_(*conditions)

    def _q_to_condition(self, q: Q, joins: JoinContext | None = None, *, scope: Any = None) -> Any:
        """Convert a Q object to SQLAlchemy condition."""
        return q_to_condition(
            self.model, q, joins=joins, annotations=self._annotations, scope=scope
        )

    def _exclude_condition(
        self, q: Q, joins: JoinContext | None = None, *, scope: Any = None
    ) -> Any:
        """``NOT (q)`` compiled with exclude() semantics.

        The lookups are built knowing they sit under a negation, so nullable
        columns keep their NULL rows and multi-valued paths become
        ``pk IN (subquery)`` tests (see :func:`q_to_condition`).
        """
        condition = q_to_condition(
            self.model,
            q,
            joins=joins,
            annotations=self._annotations,
            scope=scope,
            negated=True,
        )
        return not_(condition) if condition is not None else None

    def _lookup_to_condition(
        self, lookup_string: str, value: Any, joins: JoinContext | None = None
    ) -> Any:
        """Convert a Django-style lookup to SQLAlchemy condition."""
        return lookup_to_condition(
            self.model, lookup_string, value, joins=joins, annotations=self._annotations
        )

    def _resolve_field_path(self, field_path: str, joins: JoinContext | None = None) -> Any:
        """Resolve a field path (potentially with __ for relations) to a column."""
        return resolve_field_path(self.model, field_path, self._annotations, joins)

    def _applies_default_ordering(self) -> bool:
        """Whether ``Meta.ordering`` orders this query.

        Not once ``order_by()`` was called, and never in an aggregation (a
        query with an aggregate annotation, so a GROUP BY). There the default
        ordering names columns that are neither grouped nor aggregated —
        ``values("tool").annotate(n=Count("id"))`` on a model ordered by
        ``-created_at`` — which PostgreSQL and MySQL's ONLY_FULL_GROUP_BY
        reject outright and SQLite answers by picking an arbitrary row per
        group. Django leaves it out for the same reason; an explicit
        ``order_by()`` still applies and must name grouped fields or
        aggregates.
        """
        return self._default_ordering and not self._order_by and not self._aggregate_aliases()

    def _build_order_by(self, joins: JoinContext | None = None) -> list[Any]:
        """Build SQLAlchemy ORDER BY clause.

        Uses the explicit ``order_by()`` fields, else ``Meta.ordering`` unless
        the query aggregates (see ``_applies_default_ordering``). Every name
        must resolve — an unknown one raises ``FieldError`` instead of being
        dropped from the ORDER BY.
        """
        from sqlalchemy import asc, desc

        order_clauses = []
        fields = self._order_by
        if self._applies_default_ordering():
            fields = list(self.model._meta.ordering or [])
        for field in fields:
            descending = field.startswith("-")
            field_name = field[1:] if descending else field
            column = self._order_expression(field_name, joins)
            order_clauses.append(desc(column) if descending else asc(column))

        return order_clauses

    def _order_expression(self, field_name: str, joins: JoinContext | None) -> Any:
        """The expression an ``order_by()`` / ``Meta.ordering`` name sorts by.

        Annotations, local fields (a ForeignKey name sorts by its FK column,
        ``pk`` by the primary key), ``__`` paths across relations and
        datetime transforms (``created_at__year``).
        """
        from zeeb_orm.exceptions import FieldError

        if field_name in self._annotations:
            return self._annotations[field_name].resolve(self.model, joins=joins)

        table = self.model._get_table()
        meta = self.model._meta
        if field_name == "pk":
            return table.c[self._pk_column_name()]
        field = meta.get_field(field_name) or meta.get_field_by_column(field_name)
        if field is not None:
            return table.c[field.db_column or field.name]

        column = self._resolve_path_expression(field_name, joins)
        if column is None:
            choices = sorted({f.name for f in meta.local_fields} | set(self._annotations))
            raise FieldError(
                f"Cannot resolve keyword {field_name!r} into field for ordering "
                f"on {self.model.__name__}. Choices are: {', '.join(choices)}"
            )
        return column

    def _resolve_path_expression(self, path: str, joins: JoinContext | None) -> Any:
        """Resolve a ``__`` path (relations and/or datetime transform) to a
        SQLAlchemy expression, registering JOINs on ``joins`` as needed."""
        from zeeb_orm.query.transforms import apply_transform

        relation_parts, field_name, transform, _lookup = parse_path(self.model, path)
        if relation_parts:
            if joins is None:
                return None
            column = joins.column(relation_parts, field_name)
        else:
            column = self._resolve_field_path(field_name, joins)
        if column is not None and transform is not None:
            column = apply_transform(column, transform)
        return column

    def _select_related_columns(self, joins: JoinContext) -> list[Any]:
        """Register select_related JOINs and return their labeled columns.

        Every cumulative prefix of each select_related path gets its own
        JOIN (shared with filter/order traversal via ``joins``) and its
        columns labeled ``_sr_{prefix}_{col}`` for hydration.
        """
        columns: list[Any] = []
        for prefix in self._select_related_prefixes():
            info = self._select_related_join(joins, prefix)
            label_prefix = "_".join(prefix)
            for col in info.alias.c:
                columns.append(col.label(f"_sr_{label_prefix}_{col.name}"))
        return columns

    def _select_related_prefixes(self) -> list[tuple[str, ...]]:
        """Every cumulative prefix of every select_related path, in order."""
        prefixes: list[tuple[str, ...]] = []
        for field_path in self._select_related:
            parts = field_path.split("__")
            for i in range(1, len(parts) + 1):
                prefix = tuple(parts[:i])
                if prefix not in prefixes:
                    prefixes.append(prefix)
        return prefixes

    @staticmethod
    def _select_related_join(joins: JoinContext, prefix: tuple[str, ...]) -> Any:
        """The (shared) JOIN for one select_related prefix; only FK/O2O hops."""
        info = joins.ensure_join(list(prefix))
        if info.relation.kind not in ("fk", "o2o"):
            raise ValueError(
                f"select_related: '{prefix[-1]}' is not a ForeignKey "
                f"field on {info.relation.source_model.__name__}"
            )
        return info

    def _select_related_pk_columns(self, joins: JoinContext) -> list[Any]:
        """The primary key of every select_related JOIN.

        Grouping by them makes every joined column functionally dependent on
        the GROUP BY, which PostgreSQL and MySQL accept, as they do the base
        table's columns under its primary key.
        """
        columns = []
        for prefix in self._select_related_prefixes():
            info = self._select_related_join(joins, prefix)
            meta = info.target_model._meta
            columns.append(info.alias.c[meta.pk.db_column or meta.pk_name])
        return columns

    def _pk_column_name(self) -> str:
        """The primary key's database column name."""
        meta = self.model._meta
        return (meta.pk.db_column or meta.pk_name) if meta.pk else meta.pk_name

    def _requested_value_names(self) -> list[str] | None:
        """The field names values()/values_list() asked for, or None.

        ``None`` means "every local field" — either no values() mode at all,
        or a bare ``.values()``.
        """
        if self._values_list_fields is not None:
            return list(self._values_list_fields)
        if self._values_mode and self._values_fields is not None:
            return list(self._values_fields)
        return None

    def _values_columns(self, joins: JoinContext) -> list[Any]:
        """Labeled columns for the fields values()/values_list() asked for.

        Every column is labeled with the exact string the caller passed, so
        ``_rows_to_objects`` can read it straight out of ``row._mapping`` —
        which is also what makes ``values("pk")`` and ``values("author")``
        (column ``author_id``) resolve instead of silently yielding None.
        """
        names = self._requested_value_names()
        if names is None:
            return []

        columns = []
        seen: set[str] = set()
        for name in names:
            if name in seen or name in self._annotations:
                # Annotations are appended from _annotations with their own label.
                continue
            seen.add(name)
            columns.append(self._value_column(name, joins).label(name))
        return columns

    def _value_column(self, name: str, joins: JoinContext) -> Any:
        """The (unlabeled) column a values()/values_list() field name reads."""
        table = self.model._get_table()
        meta = self.model._meta
        if "__" in name:
            column = self._resolve_path_expression(name, joins)
        elif name == "pk":
            column = getattr(table.c, self._pk_column_name(), None)
        else:
            field = meta.get_field(name) or meta.get_field_by_column(name)
            col_name = (field.db_column or field.name) if field else name
            column = getattr(table.c, col_name, None)
        if column is None:
            raise ValueError(f"Unknown field: {name}")
        return column

    def _selected_annotation_aliases(self) -> list[str]:
        """The annotations this query selects and returns, in output order.

        Every annotation for model instances and for a values()/values_list()
        called without fields; otherwise the ones the fields name, then the
        ones annotated after the values()/values_list() call. HAVING and
        ORDER BY resolve an annotation themselves, so leaving one out of the
        SELECT list never breaks a filter or an ordering on it.
        """
        names = self._requested_value_names()
        if names is None or self._values_annotations is None:
            return list(self._annotations)
        named = [name for name in names if name in self._annotations]
        later = [
            alias
            for alias in self._values_annotations
            if alias in self._annotations and alias not in named
        ]
        return named + later

    def _get_select_columns(self, table: Any) -> list[Any]:
        """Get columns to select based on only/defer fields."""

        if self._only_fields is not None:
            # Select only specified columns (always include PK)
            pk_col_name = self.model._meta.pk.db_column or self.model._meta.pk_name
            cols = set()
            cols.add(pk_col_name)
            for field_name in self._only_fields:
                field = self.model._meta.get_field(field_name)
                if field is not None:
                    cols.add(field.db_column or field.name)
                else:
                    col = getattr(table.c, field_name, None)
                    if col is not None:
                        cols.add(field_name)
            return [getattr(table.c, c) for c in cols if hasattr(table.c, c)]

        if self._defer_fields is not None:
            # Select all columns except deferred (never defer PK)
            pk_col_name = self.model._meta.pk.db_column or self.model._meta.pk_name
            defer_cols = set()
            for field_name in self._defer_fields:
                field = self.model._meta.get_field(field_name)
                if field is not None:
                    defer_cols.add(field.db_column or field.name)
                else:
                    defer_cols.add(field_name)
            defer_cols.discard(pk_col_name)
            return [c for c in table.c if c.name not in defer_cols]

        return [table]

    def _aggregate_aliases(self) -> set[str]:
        """Annotation aliases whose expression aggregates.

        Walks the expression tree (``Expression.contains_aggregate``), so an
        aggregate wrapped in ``Coalesce``, arithmetic or a ``Case`` groups the
        query too; a type check on the outermost node let those through
        ungrouped, which collapsed every group into one row on SQLite and was
        refused by PostgreSQL. ``Window(Sum(...))`` is a window function, not
        an aggregation, and stays ungrouped.
        """
        return {
            alias
            for alias, expr in self._annotations.items()
            if getattr(expr, "contains_aggregate", False)
        }

    def _build_group_by(
        self,
        table: Any,
        joins: JoinContext,
        annotations: dict[str, Any],
    ) -> list[Any]:
        """GROUP BY columns implied by aggregate annotations.

        Without one, ``values("author").annotate(n=Count("id"))`` collapses to
        a single row instead of one row per author. The GROUP BY covers
        everything the SELECT list holds outside an aggregate, so PostgreSQL
        (and MySQL's ONLY_FULL_GROUP_BY) accept it:

        * the ``values()`` fields selected when the aggregate was annotated
          (``_group_by_fields`` — a later ``values()`` does not regroup), or
          for whole rows the primary key plus the primary key of every
          ``select_related()`` JOIN (each joined column is functionally
          dependent on it);
        * every selected non-aggregate annotation that reads a column
          (``_groups_annotation``), resolved as it is selected.
        """
        if not self._aggregate_aliases():
            return []

        group_by: list[Any] = []
        if self._group_by_fields is not None:
            for name in dict.fromkeys(self._group_by_fields):
                group_by.append(self._value_column(name, joins))
        else:
            pk_column = getattr(table.c, self._pk_column_name(), None)
            if pk_column is not None:
                group_by.append(pk_column)
            if self._select_related and self._requested_value_names() is None:
                group_by.extend(self._select_related_pk_columns(joins))

        for alias, resolved in annotations.items():
            if _groups_annotation(self._annotations[alias]):
                group_by.append(resolved)
        return group_by

    def _split_aggregate_q(self, q: Q, aggregate_aliases: set[str]) -> tuple[Any, Any]:
        """Split ``q`` into a WHERE part and a HAVING part.

        An AND node is split child by child. Any other node (OR, negation) is
        classified as a whole; one that mixes an aggregate reference with a
        plain field cannot be expressed as WHERE + HAVING and is rejected
        rather than silently compiled into invalid SQL.
        """
        from zeeb_orm.exceptions import NotSupportedError
        from zeeb_orm.query.q import Q as QClass
        from zeeb_orm.query.q import QOperator

        def references_aggregate(node: Any) -> bool:
            """True when any leaf of this Q node names an aggregate alias."""
            for child in getattr(node, "children", []):
                if isinstance(child, tuple):
                    key = child[0]
                    if key in aggregate_aliases or key.split("__")[0] in aggregate_aliases:
                        return True
                elif references_aggregate(child):
                    return True
            return False

        def references_plain_field(node: Any) -> bool:
            """True when any leaf of this Q node names a non-aggregate field."""
            for child in getattr(node, "children", []):
                if isinstance(child, tuple):
                    key = child[0]
                    if key not in aggregate_aliases and key.split("__")[0] not in aggregate_aliases:
                        return True
                elif references_plain_field(child):
                    return True
            return False

        splittable = q.connector is QOperator.AND and not q.negated
        if not splittable or not references_aggregate(q):
            if references_aggregate(q):
                if references_plain_field(q):
                    raise NotSupportedError(
                        "Cannot combine a filter on an aggregate annotation with "
                        "a filter on a plain field inside the same OR/NOT group: "
                        "the two belong in HAVING and WHERE respectively. Split "
                        "them into separate filter() calls."
                    )
                return None, q
            return q, None

        where_children: list[Any] = []
        having_children: list[Any] = []
        for child in q.children:
            if isinstance(child, tuple):
                key = child[0]
                is_agg = key in aggregate_aliases or key.split("__")[0] in aggregate_aliases
                (having_children if is_agg else where_children).append(child)
            else:
                child_where, child_having = self._split_aggregate_q(child, aggregate_aliases)
                if child_where is not None:
                    where_children.append(child_where)
                if child_having is not None:
                    having_children.append(child_having)

        def rebuild(children: list[Any]) -> Any:
            """Re-wrap split children into a single AND-joined Q, or None if empty."""
            if not children:
                return None
            node = QClass()
            node.connector = QOperator.AND
            node.children = children
            return node

        return rebuild(where_children), rebuild(having_children)

    def _build_where_and_having(self, joins: JoinContext | None = None) -> tuple[Any, Any]:
        """Build the WHERE and HAVING clauses from filters and excludes."""
        aggregate_aliases = self._aggregate_aliases()
        if not aggregate_aliases:
            return self._build_where_clause(joins), None

        if self._is_empty:
            from sqlalchemy import false

            return false(), None

        where_conditions: list[Any] = []
        having_conditions: list[Any] = []
        for source, wrap in ((self._filters, False), (self._excludes, True)):
            for index, q in enumerate(source):
                scope = ("exclude" if wrap else "filter", index)
                where_q, having_q = self._split_aggregate_q(q, aggregate_aliases)
                for part, bucket in (
                    (where_q, where_conditions),
                    (having_q, having_conditions),
                ):
                    if part is None:
                        continue
                    if wrap:
                        condition = self._exclude_condition(part, joins, scope=scope)
                    else:
                        condition = self._q_to_condition(part, joins, scope=scope)
                    if condition is not None:
                        bucket.append(condition)

        def combine(conditions: list[Any]) -> Any:
            """AND a bucket of SQLAlchemy conditions into one, or None if empty."""
            if not conditions:
                return None
            return conditions[0] if len(conditions) == 1 else and_(*conditions)

        return combine(where_conditions), combine(having_conditions)

    def _build_select(self) -> Select[Any]:
        """Build the complete SELECT statement."""
        if self._combinator is not None:
            return self._build_combined_select()

        table = self.model._get_table()
        joins = self._make_join_context()

        # Build select columns. Named values()/values_list() fields restrict
        # the SELECT list (and win over only()/defer(), as in Django);
        # select_related columns are dead weight there because dicts are
        # built straight from the row mapping.
        if self._requested_value_names() is not None:
            # Even when every name is an annotation (values("total")): the
            # SELECT list is what was asked for, never the whole row.
            columns = self._values_columns(joins)
        else:
            columns = list(self._get_select_columns(table))
            # select_related paths register their JOINs FIRST so filter/order
            # traversal of the same path reuses the same aliases (single JOIN).
            if self._select_related:
                columns.extend(self._select_related_columns(joins))

        annotations = {
            alias: self._annotations[alias].resolve(self.model, joins=joins)
            for alias in self._selected_annotation_aliases()
        }
        columns.extend(resolved.label(alias) for alias, resolved in annotations.items())

        # Apply filters (registers traversal JOINs on the shared context).
        # Conditions on aggregate annotations belong in HAVING, not WHERE.
        where_clause, having_clause = self._build_where_and_having(joins)

        stmt = select(*columns)
        if where_clause is not None:
            stmt = stmt.where(where_clause)

        group_by = self._build_group_by(table, joins, annotations)
        if group_by:
            stmt = stmt.group_by(*group_by)
        if having_clause is not None:
            stmt = stmt.having(having_clause)

        # Apply ordering (explicit order_by, else Meta.ordering unless the
        # query aggregates) - can also order by annotations and "__" paths
        order_clauses = self._build_order_by(joins)
        if order_clauses:
            stmt = stmt.order_by(*order_clauses)

        # One FROM clause containing all registered JOINs
        if joins.has_joins:
            stmt = stmt.select_from(joins.apply(table))
        elif table not in stmt.get_final_froms():
            # Nothing selected reads the table (values("one") over a constant
            # annotation): still one row per object, never a bare SELECT.
            stmt = stmt.select_from(table)

        # Apply distinct
        if self._distinct_fields:
            stmt = stmt.distinct()

        # Apply limit/offset
        if self._limit is not None:
            stmt = stmt.limit(self._limit)
        if self._offset is not None:
            stmt = stmt.offset(self._offset)

        return stmt

    # Execution methods

    def _rows_to_objects(self, rows: list[Any]) -> list[Any]:
        """Convert DB rows to results (dicts, tuples or model instances)."""
        # Handle values() mode - return dicts
        if self._values_mode:
            instances = []
            fields = self._values_fields or [
                f.db_column or f.name for f in self.model._meta.local_fields
            ]
            # Annotations the query selects (see _selected_annotation_aliases)
            all_fields = list(fields)
            for alias in self._selected_annotation_aliases():
                if alias not in all_fields:
                    all_fields.append(alias)
            for row in rows:
                if hasattr(row, "_mapping"):
                    d = {f: row._mapping.get(f) for f in all_fields}
                else:
                    d = {f: row[i] for i, f in enumerate(all_fields) if i < len(row)}
                instances.append(d)
            return instances

        # Handle values_list() mode - return tuples
        if self._values_list_fields is not None:
            instances = []
            fields = list(self._values_list_fields)
            # Annotations not named among the fields come after them.
            fields += [a for a in self._selected_annotation_aliases() if a not in fields]
            for row in rows:
                if hasattr(row, "_mapping"):
                    values = tuple(row._mapping.get(f) for f in fields)
                else:
                    values = tuple(row[i] for i in range(len(fields)) if i < len(row))

                if self._flat:
                    instances.append(values[0])
                else:
                    instances.append(values)
            return instances

        # Default: convert rows to model instances
        instances = []
        for row in rows:
            instance = self._load_instance(self.model, row)
            # Attach annotation values as attributes
            if self._annotations and hasattr(row, "_mapping"):
                for alias in self._annotations:
                    if alias in row._mapping:
                        setattr(instance, alias, row._mapping[alias])
            # Hydrate select_related objects from joined columns
            if self._select_related and hasattr(row, "_mapping"):
                self._hydrate_select_related(instance, row._mapping)
            instances.append(instance)
        return instances

    async def _fetch_all(self) -> list[Any]:
        """Execute the query and return all results."""
        if self._result_cache is not None:
            return self._result_cache

        from zeeb_orm.db.connection import get_connection, get_session

        db = await get_connection(self._db_alias)

        # Handle raw SQL mode
        if self._raw_sql is not None:
            return await self._fetch_raw(db)

        stmt = self._build_select()

        if self._for_update is not None:
            # Raises on SQLite or outside a transaction.
            self._validate_for_update(db.get_engine().dialect.name)
            stmt = self._apply_for_update(stmt)
            # Locks only make sense on the active transaction's session.
            session = _active_session_on(self._db_alias)
            assert session is not None  # guaranteed by _validate_for_update
            result = await session.execute(stmt)
            rows = result.fetchall()
            instances = self._rows_to_objects(rows)
            if self._prefetch_related and instances:
                await self._do_prefetch_related(instances, db)
            self._result_cache = instances
            return instances

        # get_session reuses the active atomic() session so reads inside a
        # transaction see that transaction's uncommitted writes.
        async with get_session(self._db_alias) as (session, _):
            result = await session.execute(stmt)
            rows = result.fetchall()
            instances = self._rows_to_objects(rows)

            # Handle prefetch_related after main fetch
            if (
                not self._values_mode
                and self._values_list_fields is None
                and self._prefetch_related
                and instances
            ):
                await self._do_prefetch_related(instances, db)

            self._result_cache = instances
            return instances

    async def _fetch_raw(self, db: Any) -> list[Any]:
        """Execute a raw SQL query and return model instances."""
        from sqlalchemy import text

        from zeeb_orm.db.connection import get_session

        sql, params = _prepare_raw_sql(
            self._raw_sql or "", self._raw_params, db.get_engine().dialect.name
        )
        async with get_session(self._db_alias) as (session, _):
            stmt = text(sql)
            result = await session.execute(stmt, params)
            rows = result.fetchall()

            instances = [self._load_instance(self.model, row) for row in rows]

            self._result_cache = instances
            return instances

    def _load_instance(self, model: Any, row: Any) -> Any:
        """A persisted ``model`` instance from a result row.

        Goes through ``Model._from_db`` when the model layer provides it
        (loading never applies field defaults and records the columns the
        row lacks as deferred), else through ``_from_row``.
        """
        from_db = getattr(model, "_from_db", None)
        if from_db is not None and hasattr(row, "_mapping"):
            return from_db(row._mapping, self._db_alias)
        instance = model._from_row(row)
        instance._state.db_alias = self._db_alias
        return instance

    def _hydrate_select_related(self, instance: Any, mapping: Any) -> None:
        """Populate select_related objects from joined row data."""
        from zeeb_orm.models.fields import ForeignKeyField

        for field_path in self._select_related:
            parts = field_path.split("__")
            current_model = self.model
            current_instance = instance

            for i, part in enumerate(parts):
                # Find FK field
                fk_field = None
                for f in current_model._fk_fields:
                    if f.name == part:
                        fk_field = f
                        break
                if fk_field is None:
                    break

                target_model = fk_field.get_target_model()
                prefix = "_".join(parts[: i + 1])

                # Extract related object data from the row
                values = {}
                for field in target_model._meta.local_fields:
                    col_name = field.db_column or field.name
                    values[col_name] = mapping.get(f"_sr_{prefix}_{col_name}")
                has_data = any(value is not None for value in values.values())

                if has_data:
                    from_db = getattr(target_model, "_from_db", None)
                    if from_db is not None:
                        related_obj = from_db(values, self._db_alias)
                    else:
                        related_kwargs = {
                            (
                                f"{field.name}_id"
                                if isinstance(field, ForeignKeyField)
                                else field.name
                            ): values[field.db_column or field.name]
                            for field in target_model._meta.local_fields
                        }
                        related_obj = target_model(**related_kwargs)
                        related_obj._state.persisted = True
                        related_obj._state.db_alias = self._db_alias
                    # Cache on the instance so FK access returns it directly
                    setattr(current_instance, f"_cache_{part}", related_obj)
                    current_model = target_model
                    current_instance = related_obj
                else:
                    break

    async def _do_prefetch_related(self, instances: list[Any], db: Any) -> None:
        """Execute separate queries for prefetch_related lookups.

        See :mod:`zeeb_orm.query.prefetch`: nested lookups
        (``"posts__comments"``) are resolved level by level, to-many
        accessors keep their manager API off the prefetched objects, and an
        unknown lookup raises ``FieldError``.
        """
        from zeeb_orm.query.prefetch import prefetch_related_objects

        await prefetch_related_objects(
            instances, self.model, self._prefetch_related, self._db_alias
        )

    async def __aiter__(self) -> AsyncIterator[Any]:
        """Async iteration support."""
        results = await self._fetch_all()
        for item in results:
            yield item

    def _sync_access_error(self, operation: str) -> TypeError:
        return TypeError(
            f"{operation} on an unevaluated QuerySet is not supported: queries are "
            "async. Use 'await qs' (a list), 'async for obj in qs', "
            "'await qs.count()' or 'await qs.exists()'."
        )

    def __iter__(self) -> Iterator[Any]:
        """Iterate an already evaluated queryset.

        Never runs a query: an event loop cannot be driven from sync code
        inside a running loop, and ``asyncio.run()`` would create a second
        loop that the connection pool is not bound to. Evaluate with
        ``await qs`` or ``async for`` first.
        """
        if self._result_cache is None:
            raise self._sync_access_error("Synchronous iteration")
        return iter(self._result_cache)

    def __len__(self) -> int:
        """Length of an already evaluated queryset (see ``__iter__``)."""
        if self._result_cache is None:
            raise self._sync_access_error("len()")
        return len(self._result_cache)

    def __bool__(self) -> bool:
        """Truth of an already evaluated queryset (see ``__iter__``)."""
        if self._result_cache is None:
            raise self._sync_access_error("Truth-testing")
        return bool(self._result_cache)

    def __await__(self) -> Any:
        """Allow awaiting QuerySet directly to get list of results."""
        return self._fetch_all().__await__()

    async def __aenter__(self) -> QuerySet[ModelT]:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    # Streaming iteration

    def iterator(self, chunk_size: int = 2000) -> AsyncIterator[ModelT]:
        """Stream results in chunks without caching them on the QuerySet.

        Uses a server-side streaming cursor (``session.stream()``) and reads
        ``chunk_size`` rows at a time; the database session stays open for
        the lifetime of the generator.

        Usage:
            async for user in User.objects.filter(active=True).iterator():
                ...

        Raises:
            NotSupportedError: when combined with prefetch_related()
                (prefetching requires the full result set).
        """
        from zeeb_orm.exceptions import NotSupportedError

        if self._prefetch_related:
            raise NotSupportedError("iterator() cannot be used with prefetch_related().")
        if chunk_size <= 0:
            raise ValueError("Chunk size must be strictly positive.")

        async def _generate() -> AsyncIterator[ModelT]:
            from zeeb_orm.db.connection import get_session

            stmt = self._build_select()

            # The session must stay open across yields - hold it in the
            # generator so it lives exactly as long as the iteration.
            async with get_session(self._db_alias) as (session, _):
                result = await session.stream(stmt)
                async for partition in result.partitions(chunk_size):
                    for obj in self._rows_to_objects(list(partition)):
                        yield obj

        return _generate()

    # Bulk retrieval

    async def in_bulk(
        self, id_list: list[Any] | None = None, *, field_name: str = "pk"
    ) -> dict[Any, ModelT]:
        """Return a ``{field_value: instance}`` mapping.

        Args:
            id_list: Values to fetch.  ``None`` fetches all objects;
                an empty list returns ``{}``.
            field_name: Field to key the mapping by - must be the primary
                key (default) or a unique field.

        Raises:
            ValueError: when ``field_name`` is not unique.
        """
        if field_name == "pk":
            accessor = self.model._meta.pk_name or "id"
        else:
            field = self.model._meta.get_field(field_name)
            if field is None:
                raise ValueError(
                    f"in_bulk(): {self.model.__name__} has no field named {field_name!r}."
                )
            if not (field.unique or field.primary_key):
                raise ValueError(
                    f"in_bulk()'s field_name must be a unique field, but {field_name!r} isn't."
                )
            accessor = field_name

        if id_list is not None:
            id_list = list(id_list)
            if not id_list:
                return {}
            qs = self.filter(**{f"{field_name}__in": id_list})
        else:
            qs = self._clone()

        objs = await qs._fetch_all()
        return {getattr(obj, accessor): obj for obj in objs}

    # Query plans

    async def explain(self, *, analyze: bool = False) -> str:
        """Return the database's execution plan for this query as a string.

        Uses the dialect-specific syntax: ``EXPLAIN QUERY PLAN`` on SQLite,
        ``EXPLAIN`` / ``EXPLAIN ANALYZE`` on PostgreSQL and ``EXPLAIN``
        on MySQL.
        """
        from zeeb_orm.db.connection import get_connection

        db = await get_connection(self._db_alias)
        dialect = db.get_engine().dialect

        if dialect.name == "sqlite":
            prefix = "EXPLAIN QUERY PLAN"
        elif dialect.name == "postgresql":
            prefix = "EXPLAIN ANALYZE" if analyze else "EXPLAIN"
        else:
            prefix = "EXPLAIN"

        # The EXPLAIN prefix is compiled around the statement, so values stay
        # bound parameters (no literal inlining re-parsed as SQL text).
        from zeeb_orm.db.connection import get_session

        async with get_session(self._db_alias) as (session, _):
            result = await session.execute(_Explain(self._build_select(), prefix))
            rows = result.fetchall()

        return "\n".join(" ".join(str(value) for value in row if value is not None) for row in rows)

    # Single object retrieval

    async def get(self, *args: Q, **kwargs: Any) -> ModelT:
        """
        Get a single object matching the filters.

        Raises DoesNotExist if no object found.
        Raises MultipleObjectsReturned if more than one found.
        """
        clone = self.filter(*args, **kwargs) if args or kwargs else self._clone()
        clone._limit = 2  # Fetch 2 to check for multiple

        results = await clone._fetch_all()

        if not results:
            raise self.model.DoesNotExist(f"{self.model.__name__} matching query does not exist.")
        if len(results) > 1:
            raise self.model.MultipleObjectsReturned(
                f"get() returned more than one {self.model.__name__}"
            )
        return results[0]

    def _effective_ordering(self, method: str = "first") -> list[str]:
        """Explicit order_by, else Meta.ordering, else the primary key.

        Guarantees first()/last() are deterministic and mirror-images of
        each other even on querysets with no explicit ordering.

        An aggregation ignores ``Meta.ordering`` (``_applies_default_ordering``),
        and the primary key is only a valid fallback when the rows are grouped
        by it. Grouped by anything else, there is no ordering to fall back on
        that the database would accept, so — as in Django — ``first()`` /
        ``last()`` raise ``TypeError`` and ask for an explicit ``order_by()``.
        """
        if self._order_by:
            return list(self._order_by)
        if self._applies_default_ordering() and self.model._meta.ordering:
            return list(self.model._meta.ordering)
        if self._aggregate_aliases() and not self._groups_by_pk():
            raise TypeError(
                f"Cannot use {type(self).__name__}.{method}() on an unordered queryset "
                "performing aggregation. Add an ordering with order_by()."
            )
        return [self.model._meta.pk_name or "id"]

    def _groups_by_pk(self) -> bool:
        """Whether an aggregation's GROUP BY includes the primary key.

        Whole rows are grouped by the primary key (``_build_group_by``); a
        ``values()``/``values_list()`` aggregation only when it grouped by it.
        """
        if self._group_by_fields is None:
            return True
        meta = self.model._meta
        pk_names = {"pk", meta.pk_name, self._pk_column_name()}
        return any(name in pk_names for name in self._group_by_fields)

    async def first(self) -> ModelT | None:
        """Get the first object or None (primary-key order when unordered).

        An unordered aggregation that is not grouped by the primary key has no
        such order to fall back on, so it raises ``TypeError`` asking for an
        ``order_by()``.
        """
        clone = self._clone()
        clone._order_by = self._effective_ordering("first")
        clone._limit = 1
        results = await clone._fetch_all()
        return results[0] if results else None

    async def last(self) -> ModelT | None:
        """Get the last object or None.

        Reverses the effective ordering (explicit order_by, Meta.ordering,
        or the primary key) and returns the first row. Raises ``TypeError``
        where ``first()`` does: an unordered aggregation not grouped by the
        primary key.
        """
        clone = self._clone()
        clone._order_by = [
            f[1:] if f.startswith("-") else f"-{f}" for f in self._effective_ordering("last")
        ]
        clone._limit = 1
        results = await clone._fetch_all()
        return results[0] if results else None

    def _build_count_select(self) -> Any:
        """``SELECT count(*)`` for this queryset.

        Counts over the full SELECT as a subquery whenever the row set is not
        simply "the filtered table": a slice, ``distinct()``, a GROUP BY from
        aggregate annotations (whose filters live in HAVING) or a combinator.
        Otherwise the cheap ``count(*) FROM <table> WHERE ...`` form is used.
        """
        if self._combinator is not None:
            return select(func.count()).select_from(self._build_combined_select().subquery())

        if (
            self._limit is not None
            or self._offset is not None
            or self._distinct_fields
            or self._aggregate_aliases()
        ):
            inner = self._build_select()
            if self._limit is None and self._offset is None:
                inner = inner.order_by(None)  # ordering cannot change a count
            return select(func.count()).select_from(inner.subquery("_count"))

        table = self.model._get_table()
        joins = self._make_join_context()
        where_clause = self._build_where_clause(joins)
        stmt = select(func.count()).select_from(joins.apply(table) if joins.has_joins else table)
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        return stmt

    async def count(self) -> int:
        """Count the rows this queryset yields.

        Respects slicing (``qs[:10].count()`` is at most 10), ``distinct()``
        and filters on aggregate annotations. An already evaluated queryset
        answers from its result cache.
        """
        from zeeb_orm.db.connection import get_session

        if self._result_cache is not None:
            return len(self._result_cache)

        stmt = self._build_count_select()
        async with get_session(self._db_alias) as (session, _):
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def exists(self) -> bool:
        """Check if any objects match the query."""
        if self._result_cache is not None:
            return bool(self._result_cache)
        clone = self._clone()
        clone._limit = 1 if self._limit is None else min(self._limit, 1)
        results = await clone._fetch_all()
        return len(results) > 0

    # CRUD operations

    async def create(self, *, validate: bool = True, **kwargs: Any) -> ModelT:
        """Create and save a new object.

        Delegates to :meth:`Model.save`, so ``pre_save``/``post_save`` fire
        with ``created=True`` — as they do in Django, where ``create()`` is
        ``obj.save(force_insert=True)``. Unless ``validate=False``,
        ``full_clean()`` runs before the INSERT.

        Model instances may be passed for ForeignKey fields
        (``create(author=author)``); the descriptor stores the id and caches
        the instance, so a later ``await obj.author`` needs no query.
        """
        instance = self.model(**kwargs)
        await instance.save(validate=validate, using=self._db_alias)
        return instance

    @staticmethod
    def _create_params(defaults: dict[str, Any] | None, lookups: dict[str, Any]) -> dict[str, Any]:
        """Constructor arguments for the create branch of get/update_or_create.

        Lookups with ``__`` (``name__iexact=...``) select, they do not
        assign, so they are dropped; ``defaults`` win over the lookups and
        callable values are called.
        """
        params = {key: value for key, value in lookups.items() if "__" not in key}
        params.update(defaults or {})
        return {key: value() if callable(value) else value for key, value in params.items()}

    async def get_or_create(
        self, defaults: dict[str, Any] | None = None, **kwargs: Any
    ) -> tuple[ModelT, bool]:
        """
        Get an object or create it if it doesn't exist.

        The create runs in its own ``atomic()`` block (a savepoint inside an
        enclosing transaction; on SQLite it joins the enclosing transaction,
        see ``_create_block``). If it hits an ``IntegrityError`` — typically
        a concurrent caller created the same row between the lookup and the
        insert — the lookup is repeated and that row returned; only when it
        still does not exist is the error re-raised.

        Returns (instance, created) tuple.
        """
        from zeeb_orm.exceptions import IntegrityError

        try:
            return await self.get(**kwargs), False
        except self.model.DoesNotExist:
            params = self._create_params(defaults, kwargs)
            try:
                async with _create_block(self._db_alias):
                    instance = await self.create(**params)
                return instance, True
            except IntegrityError:
                try:
                    return await self.get(**kwargs), False
                except self.model.DoesNotExist:
                    pass
                raise

    async def update_or_create(
        self,
        defaults: dict[str, Any] | None = None,
        create_defaults: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[ModelT, bool]:
        """
        Update an object or create it if it doesn't exist.

        Runs in one transaction (joining an enclosing ``atomic()`` on the same
        database). The lookup locks the row with
        ``SELECT ... FOR UPDATE`` (not on SQLite, which locks the whole
        database for a write anyway), the create is race-safe as in
        :meth:`get_or_create`, and the update saves only the ``defaults``
        fields (plus ``auto_now`` fields). ``create_defaults`` replaces
        ``defaults`` for the create branch when given.

        Returns (instance, created) tuple.
        """
        from zeeb_orm.db.connection import get_connection

        update_defaults = defaults or {}
        if create_defaults is None:
            create_defaults = update_defaults

        db = await get_connection(self._db_alias)
        async with _transaction(self._db_alias):
            queryset = self
            if db.get_engine().dialect.name != "sqlite":
                queryset = self.select_for_update()
            instance, created = await queryset.get_or_create(create_defaults, **kwargs)
            if created:
                return instance, True
            for key, value in update_defaults.items():
                setattr(instance, key, value() if callable(value) else value)
            update_fields = self._update_fields_for(update_defaults)
            if update_fields != []:  # nothing to write: no save, no signals
                await instance.save(update_fields=update_fields, using=self._db_alias)
        return instance, False

    def _update_fields_for(self, values: dict[str, Any]) -> list[str] | None:
        """``save(update_fields=...)`` for ``values``' keys, or None (all).

        Keys that are not concrete fields (a property, say) mean a full save;
        an empty list means there is nothing to write.
        """
        from zeeb_orm.models.fields import DateField, DateTimeField

        meta = self.model._meta
        names: list[str] = []
        for key in values:
            field = meta.get_field(key) or meta.get_field_by_column(key)
            if field is None and key == "pk":
                field = meta.pk
            if field is None:
                return None
            names.append(field.name)
        for field in meta.local_fields:
            if (
                isinstance(field, (DateTimeField, DateField))
                and getattr(field, "auto_now", False)
                and field.name not in names
            ):
                names.append(field.name)
        return names

    def _concrete_field(self, name: str, operation: str) -> Any:
        """The local field a write addresses as ``name``.

        Accepts the field name, ``pk``, the column name and a ForeignKey's
        ``<name>_id`` attribute. Many-to-many and unknown names raise
        ``FieldError``.
        """
        from zeeb_orm.exceptions import FieldError

        meta = self.model._meta
        if name == "pk":
            return meta.pk
        field = meta.get_field(name) or meta.get_field_by_column(name)
        if field is None:
            for fk in getattr(self.model, "_fk_fields", []):
                if f"{fk.name}_id" == name:
                    field = fk
                    break
        if field is not None:
            return field
        if any(m2m.name == name for m2m in getattr(self.model, "_m2m_fields", [])):
            raise FieldError(
                f"{operation}() cannot write the many-to-many field {name!r} of "
                f"{self.model.__name__}; use the related manager (add/remove/set)."
            )
        choices = sorted(f.name for f in meta.local_fields)
        raise FieldError(
            f"{operation}(): {self.model.__name__} has no field named {name!r}. "
            f"Choices are: {', '.join(choices)}"
        )

    async def update(self, **kwargs: Any) -> int:
        """
        Update all objects matching the query.

        Keys are field names — a ForeignKey may be given by name with an
        instance or a primary key (``update(author=alice)``) or as
        ``author_id``. Values may be expressions (``F("views") + 1``).

        Returns the number of rows updated.
        """
        from zeeb_orm.db.connection import get_session
        from zeeb_orm.query.expressions import Expression

        self._check_combinator("update")

        table = self.model._get_table()

        # Map field names (a ForeignKey by name or by its <name>_id column)
        # to columns; model instances become their primary key and
        # expressions (F() & co) are resolved against this model.
        values: dict[str, Any] = {}
        for key, value in kwargs.items():
            field = self._concrete_field(key, "update")
            if isinstance(value, Expression):
                value = value.resolve(self.model)
            else:
                value = _instance_pk(value)
            values[field.db_column or field.name] = value

        stmt = update(table).values(values)

        joins = self._make_join_context()
        where_clause = self._build_where_clause(joins)

        if joins.has_joins:
            # UPDATE cannot join: rewrite as pk IN (SELECT pk FROM <joins> ...)
            stmt = stmt.where(self._pk_in_join_subquery(table, joins, where_clause))
        elif where_clause is not None:
            stmt = stmt.where(where_clause)

        async with get_session(self._db_alias) as (session, should_commit):
            result = await session.execute(stmt)
            if should_commit:
                await session.commit()
            return result.rowcount

    async def delete(self) -> int:
        """
        Delete all objects matching the query.

        When other models reference this one through a ForeignKey with a
        non-DO_NOTHING ``on_delete``, the matching objects are fetched and
        deleted through the :class:`~zeeb_orm.models.deletion.Collector`
        (cascades, PROTECT/RESTRICT checks, SET_NULL/SET_DEFAULT updates,
        per-instance delete signals); the returned count then includes
        cascade-deleted rows.  The same route is taken when a ``pre_delete``
        or ``post_delete`` receiver is connected for this model, so a
        registered receiver always runs.  Otherwise a single fast DELETE
        statement runs.

        Returns the number of rows deleted.
        """
        from zeeb_orm.db.connection import get_session
        from zeeb_orm.models.deletion import Collector, model_has_inbound_refs
        from zeeb_orm.signals import post_delete, pre_delete

        self._check_combinator("delete")

        has_delete_receivers = pre_delete.has_listeners(self.model) or post_delete.has_listeners(
            self.model
        )

        if model_has_inbound_refs(self.model) or has_delete_receivers:
            # Fetch, collect and delete in ONE transaction on this queryset's
            # database (joining an enclosing atomic() on it): rows added or
            # re-pointed between the collection and the DELETE could
            # otherwise escape the cascade.
            async with _transaction(self._db_alias):
                objs = await self._clone()._fetch_all()
                if not objs:
                    return 0
                collector = Collector(using=self._db_alias)
                await collector.collect(objs)
                total, _per_model = await collector.delete()
            return total

        table = self.model._get_table()

        stmt = delete(table)

        joins = self._make_join_context()
        where_clause = self._build_where_clause(joins)

        if joins.has_joins:
            # DELETE cannot join: rewrite as pk IN (SELECT pk FROM <joins> ...)
            stmt = stmt.where(self._pk_in_join_subquery(table, joins, where_clause))
        elif where_clause is not None:
            stmt = stmt.where(where_clause)

        async with get_session(self._db_alias) as (session, should_commit):
            result = await session.execute(stmt)
            if should_commit:
                await session.commit()
            return result.rowcount

    async def bulk_create(
        self,
        objs: list[ModelT],
        *,
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        validate: bool = False,
    ) -> list[ModelT]:
        """Insert multiple objects efficiently.

        Rows go in as one multi-row INSERT per ``batch_size`` objects
        (default: all at once; rows whose set of non-NULL columns differs are
        sent as separate statements so column defaults still apply).
        Primary keys generated by the database are read back with
        ``RETURNING`` in parameter order. PostgreSQL does that in batches;
        where the backend cannot tie returned rows to their parameters
        (SQLite) or has no ``RETURNING`` (MySQL), rows with a database-
        generated key are inserted one per statement to learn their ids.
        Client-generated keys (the default UUID primary key) always batch.

        ``ignore_conflicts=True`` skips rows that violate a constraint
        (``ON CONFLICT DO NOTHING`` / ``INSERT IGNORE``); a skipped object is
        left unpersisted. Where the backend cannot report which rows were
        skipped in a multi-row statement (MySQL), those inserts also run one
        by one.

        Validation is OFF by default for performance; pass ``validate=True``
        to run ``full_clean()`` on every object before any insert. No
        ``save()`` and no signals.
        """
        from zeeb_orm.db.connection import get_connection, get_session

        if batch_size is not None and batch_size <= 0:
            raise ValueError("Batch size must be a positive integer.")
        objs = list(objs)
        if not objs:
            return []

        if validate:
            for obj in objs:
                await obj.full_clean()

        table = self.model._get_table()
        pk_name = self.model._meta.pk_name
        pk_col = table.c[self._pk_column_name()]
        db = await get_connection(self._db_alias)
        dialect = db.get_engine().dialect
        insert_stmt = _insert_statement(table, dialect.name, ignore_conflicts)
        can_return = bool(getattr(dialect, "insert_returning", False)) and bool(
            getattr(dialect, "insert_executemany_returning", False)
        )

        def mark_persisted(obj: Any) -> None:
            obj._state.persisted = True
            obj._state.db_alias = self._db_alias

        async def insert_one_by_one(session: Any, rows: list[tuple[Any, dict]]) -> None:
            for obj, values in rows:
                result = await session.execute(insert_stmt.values(**values))
                if ignore_conflicts and result.rowcount == 0:
                    continue  # skipped: leave the object unpersisted
                if getattr(obj, pk_name) is None:
                    setattr(obj, pk_name, result.inserted_primary_key[0])
                mark_persisted(obj)

        size = batch_size or len(objs)
        async with get_session(self._db_alias) as (session, should_commit):
            for start in range(0, len(objs), size):
                # Group the batch by column set: an executemany needs every
                # row to bind the same columns.
                groups: dict[tuple[str, ...], list[tuple[Any, dict]]] = {}
                for obj in objs[start : start + size]:
                    values = obj._to_insert_values()
                    groups.setdefault(tuple(sorted(values)), []).append((obj, values))

                for columns, rows in groups.items():
                    db_pk = pk_col.name not in columns
                    params = [values for _obj, values in rows]
                    if (db_pk or ignore_conflicts) and not can_return:
                        await insert_one_by_one(session, rows)
                    elif db_pk and not ignore_conflicts:
                        result = await session.execute(
                            insert_stmt.returning(pk_col, sort_by_parameter_order=True),
                            params,
                        )
                        for (obj, _values), pk_value in zip(rows, result.scalars().all()):
                            setattr(obj, pk_name, pk_value)
                            mark_persisted(obj)
                    elif db_pk:
                        # ignore_conflicts + database-generated keys: the
                        # returned ids cannot be matched to skipped rows.
                        await insert_one_by_one(session, rows)
                    elif ignore_conflicts:
                        result = await session.execute(insert_stmt.returning(pk_col), params)
                        inserted = set(result.scalars().all())
                        for obj, values in rows:
                            if values[pk_col.name] in inserted:
                                mark_persisted(obj)
                    else:
                        await session.execute(insert_stmt, params)
                        for obj, _values in rows:
                            mark_persisted(obj)

            if should_commit:
                await session.commit()

        return objs

    async def bulk_update(
        self,
        objs: list[ModelT],
        fields: list[str],
        *,
        batch_size: int | None = None,
    ) -> int:
        """Write ``fields`` of every object in ``objs`` back to the database.

        One ``UPDATE ... SET col = CASE WHEN pk = ... END WHERE pk IN (...)``
        per batch of ``batch_size`` objects (default: as many as keep the
        statement under ~900 bind parameters, which every driver accepts). A
        ForeignKey is written from its ``<name>_id`` value, so both
        ``"author"`` and ``"author_id"`` work. Does not call ``save()`` or
        send signals.

        Returns the number of rows matched.
        """
        from sqlalchemy import case, literal

        from zeeb_orm.db.connection import get_session
        from zeeb_orm.models.fields import ForeignKeyField
        from zeeb_orm.query.expressions import Expression

        if batch_size is not None and batch_size <= 0:
            raise ValueError("Batch size must be a positive integer.")
        if not fields:
            raise ValueError("Field names must be given to bulk_update().")
        objs = list(objs)
        if not objs:
            return 0

        targets = []
        for name in dict.fromkeys(fields):
            field = self._concrete_field(name, "bulk_update")
            if field.primary_key:
                raise ValueError("bulk_update() cannot be used with primary key fields.")
            targets.append(field)
        if any(obj.pk is None for obj in objs):
            raise ValueError("All bulk_update() objects must have a primary key set.")

        table = self.model._get_table()
        pk_col = table.c[self._pk_column_name()]

        def value_of(obj: Any, field: Any, column: Any) -> Any:
            if isinstance(field, ForeignKeyField):
                value = getattr(obj, f"_field_{field.name}_id", None)
            else:
                value = getattr(obj, field.name, None)
            if isinstance(value, Expression):
                return value.resolve(self.model)
            return literal(_instance_pk(value), type_=column.type)

        # Per object and field: a pk and a value in the CASE; plus the pk in IN.
        size = batch_size or max(1, 900 // (2 * len(targets) + 1))
        count = 0
        async with get_session(self._db_alias) as (session, should_commit):
            for start in range(0, len(objs), size):
                batch = objs[start : start + size]
                values = {}
                for field in targets:
                    column = table.c[field.db_column or field.name]
                    values[column.name] = case(
                        *[(pk_col == obj.pk, value_of(obj, field, column)) for obj in batch],
                        else_=column,
                    )
                stmt = update(table).where(pk_col.in_([obj.pk for obj in batch])).values(values)
                result = await session.execute(stmt)
                count += result.rowcount

            if should_commit:
                await session.commit()

        return count

    # Representation

    def __repr__(self) -> str:
        return f"<QuerySet [{self.model.__name__}]>"


class Prefetch:
    """
    Customize prefetch_related behavior.

    Usage:
        Author.objects.prefetch_related(
            Prefetch('posts', queryset=Post.objects.filter(published=True))
        )
    """

    def __init__(
        self,
        lookup: str,
        queryset: QuerySet[Any] | None = None,
        to_attr: str | None = None,
    ) -> None:
        self.lookup = lookup
        self.queryset = queryset
        self.to_attr = to_attr


# Shared condition-building machinery.
#
# Module-level so that expression classes (When/Case, Aggregate filter=,
# Subquery) compile their conditions through the exact same path as
# QuerySet.filter() instead of maintaining a divergent lookup subset.


# SQLAlchemy's text() turns every match into a bind parameter
# (TextClause._bind_params_regex); a backslash before the colon keeps it
# literal.
_TEXT_BIND = re.compile(r"(?<![:\w\x5c]):(\w+)(?!:)")


def _escape_text_binds(segment: str) -> str:
    """Make ``segment`` literal for ``text()``: no ``:name`` becomes a bind."""
    return _TEXT_BIND.sub(lambda m: "\\:" + m.group(1), segment)


def _split_raw_sql(sql: str, backslash_escapes: bool) -> list[tuple[bool, str]]:
    """``[(is_code, text), ...]``: ``sql`` split into code and literal runs.

    Literal runs are string literals (``'...'``), quoted identifiers
    (``"..."`` and backtick-quoted) with doubled-quote escapes — plus backslash
    escapes where the backend honours them (MySQL) — and ``-- ...`` /
    ``/* ... */`` comments. An unterminated run extends to the end.
    """
    parts: list[tuple[bool, str]] = []
    code_start = 0
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        end = None
        if ch in "'\"`":
            j = i + 1
            while j < n:
                c = sql[j]
                if backslash_escapes and c == "\\" and ch != "`":
                    j += 2
                    continue
                if c == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            end = min(j + 1, n)
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            end = n if j == -1 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            end = n if j == -1 else j + 2
        if end is None:
            i += 1
            continue
        if code_start < i:
            parts.append((True, sql[code_start:i]))
        parts.append((False, sql[i:end]))
        i = code_start = end
    if code_start < n:
        parts.append((True, sql[code_start:]))
    return parts


def _prepare_raw_sql(sql: str, params: Any, dialect_name: str) -> tuple[str, dict[str, Any]]:
    """``raw()`` SQL and params as ``text()`` SQL plus a bind dict.

    Positional ``?`` placeholders outside literals and comments become
    ``:_raw_N`` binds; every other ``:word`` that ``text()`` would read as
    a bind (inside literals always, in code too unless params are named) is
    escaped. The old implementation replaced the first N ``?`` characters
    anywhere — including inside string literals.
    """
    parts = _split_raw_sql(sql, backslash_escapes=dialect_name in ("mysql", "mariadb"))
    if isinstance(params, dict):
        text_sql = "".join(t if code else _escape_text_binds(t) for code, t in parts)
        return text_sql, dict(params)

    values = list(params) if params is not None else []
    out: list[str] = []
    index = 0
    for code, segment in parts:
        segment = _escape_text_binds(segment)
        if code:
            pieces = segment.split("?")
            rebuilt = [pieces[0]]
            for piece in pieces[1:]:
                rebuilt.append(f":_raw_{index}{piece}")
                index += 1
            segment = "".join(rebuilt)
        out.append(segment)
    if index != len(values):
        raise ValueError(
            f"raw(): the SQL has {index} '?' placeholder(s) but {len(values)} "
            "parameter(s) were given."
        )
    return "".join(out), {f"_raw_{i}": value for i, value in enumerate(values)}


class _Explain(Executable, ClauseElement):
    """``<prefix> <statement>`` — EXPLAIN compiled around a real statement.

    The statement's values stay bound parameters. ``explain()`` used to
    compile the query with literal binds and send the string through
    ``text()``, which re-parsed any ``:name`` inside a value as a new bind
    parameter.
    """

    inherit_cache = False

    def __init__(self, statement: Any, prefix: str) -> None:
        self.statement = statement
        self.prefix = prefix


@compiles(_Explain)
def _compile_explain(element: _Explain, compiler: Any, **kw: Any) -> str:
    sql = compiler.process(element.statement, **kw)
    # The rows are the plan, not the SELECT's columns: drop the SELECT's
    # result map so its type processors are not applied to them.
    compiler._result_columns = []
    return f"{element.prefix} {sql}"


def _active_session_on(alias: str | None) -> Any:
    """The active ``atomic()`` session if it is on ``alias``'s database.

    A transaction open on another database is not one a statement against
    ``alias`` may join (``None`` means the default alias).
    """
    from zeeb_orm.db import connection

    session = connection.get_active_session()
    if session is None:
        return None
    if connection._active_session_alias.get() != (alias or connection._default_alias):
        return None
    return session


@asynccontextmanager
async def _transaction(alias: str | None) -> AsyncIterator[None]:
    """Join the active ``atomic()`` on ``alias``'s database, or open one.

    Atomicity without a savepoint when a transaction is already open (a
    savepoint is only needed to recover from an error, which callers of
    this block do not do).
    """
    from zeeb_orm.db.connection import atomic

    if _active_session_on(alias) is not None:
        yield
    else:
        async with atomic(alias):
            yield


@asynccontextmanager
async def _create_block(alias: str | None) -> AsyncIterator[None]:
    """The block ``get_or_create()`` inserts in: a savepoint when joining.

    The savepoint lets the enclosing transaction survive an
    ``IntegrityError`` (PostgreSQL aborts the whole transaction otherwise).
    SQLite is the exception: it aborts only the failing statement, and
    under the pysqlite driver's transaction handling a SAVEPOINT that is
    the first statement of the enclosing transaction commits on RELEASE —
    the enclosing ``atomic()`` could no longer roll the new row back. There
    the insert simply joins the transaction.
    """
    from zeeb_orm.db.connection import atomic, get_connection

    if _active_session_on(alias) is not None:
        db = await get_connection(alias)
        if db.get_engine().dialect.name == "sqlite":
            yield
            return
    async with atomic(alias):
        yield


class _SubqueryScope:
    """Stands in for a model when expressions resolve against a subquery.

    ``F("price")`` and lookups resolve columns through ``_get_table()``, which
    returns the subquery, and field metadata through ``_meta``, which is the
    model's. Relation traversal has no join context here and is refused.
    """

    def __init__(self, model: Any, subquery: Any) -> None:
        self._model = model
        self._subquery = subquery
        self._meta = model._meta
        self.__name__ = model.__name__

    def _get_table(self) -> Any:
        return self._subquery

    def __repr__(self) -> str:
        return f"<subquery of {self._model.__name__}>"


def _referenced_names(expressions: Any) -> list[str]:
    """The first ``__`` segment of every ``F()`` name inside ``expressions``."""
    from zeeb_orm.query.expressions import F

    names: list[str] = []
    stack = list(expressions)
    while stack:
        node = stack.pop()
        if isinstance(node, F):
            names.append(node.field_name.split("__")[0])
        stack.extend(node.get_source_expressions())
    return names


def _unlabeled(column: Any) -> Any:
    """The expression under a ``.label()``, so GROUP BY never names an alias."""
    from sqlalchemy.sql.elements import Label

    return column.element if isinstance(column, Label) else column


def _groups_annotation(expr: Any) -> bool:
    """Whether a non-aggregate annotation belongs in an aggregation's GROUP BY.

    It is selected next to the aggregates, so every column it reads must be
    grouped. Aggregates and window functions never go into a GROUP BY, nor
    does a subquery (it is evaluated per group); an expression that reads no
    column (``Value(1)``) needs no grouping, and a literal there would be
    refused by PostgreSQL ("non-integer constant in GROUP BY").
    """
    if getattr(expr, "contains_aggregate", False) or getattr(expr, "contains_over_clause", False):
        return False
    return _reads_columns(expr)


def _reads_columns(node: Any) -> bool:
    """Whether an expression tree reads a column of the current query.

    ``F`` reads one; so does any node holding a ``Q`` condition (``When``, a
    ``Case`` given ``(Q, result)`` pairs). A ``Subquery``/``Exists`` reads its
    own query's columns, not this one's.
    """
    from zeeb_orm.query.expressions import Exists, F, Subquery

    if isinstance(node, (Subquery, Exists)):
        return False
    if isinstance(node, F):
        return True

    def holds_q(value: Any) -> bool:
        if isinstance(value, Q):
            return True
        return isinstance(value, (list, tuple)) and any(holds_q(item) for item in value)

    if any(holds_q(value) for value in vars(node).values()):
        return True
    return any(_reads_columns(source) for source in node.get_source_expressions())


def _require_expressions(method: str, values: dict[str, Any]) -> None:
    """Reject annotate()/aggregate() values that are not expressions.

    Anything else used to be rendered verbatim with ``literal_column`` — a
    string from a request became raw SQL.
    """
    from zeeb_orm.query.expressions import Expression

    for alias, value in values.items():
        if not isinstance(value, Expression):
            raise TypeError(
                f"QuerySet.{method}() received a non-expression for {alias!r}: "
                f"{type(value).__name__}. Use an expression such as F('field'), "
                "Value(...) or an aggregate like Count('field'); plain strings "
                "are never interpreted as SQL."
            )


def _insert_statement(table: Any, dialect_name: str, ignore_conflicts: bool) -> Any:
    """``INSERT INTO table`` — conflict-skipping when ``ignore_conflicts``."""
    from sqlalchemy import insert as _sa_insert

    if not ignore_conflicts:
        return _sa_insert(table)
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _pg_insert

        return _pg_insert(table).on_conflict_do_nothing()
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _sqlite_insert

        return _sqlite_insert(table).on_conflict_do_nothing()
    if dialect_name in ("mysql", "mariadb"):
        return _sa_insert(table).prefix_with("IGNORE")

    from zeeb_orm.exceptions import NotSupportedError

    raise NotSupportedError(
        f"bulk_create(ignore_conflicts=True) is not supported on the {dialect_name!r} backend."
    )


def _instance_pk(value: Any) -> Any:
    """A model instance's primary key; any other value unchanged."""
    if hasattr(value, "_state") and hasattr(value, "pk"):
        return value.pk
    return value


def resolve_field_path(
    model: type,
    field_path: str,
    annotations: dict[str, Any] | None = None,
    joins: JoinContext | None = None,
) -> Any:
    """Resolve a plain field name (or annotation) on ``model`` to a column."""
    parts = field_path.split("__")
    table = model._get_table()

    # Handle 'pk' as alias for primary key
    field_name = parts[0]
    if field_name == "pk":
        field_name = model._meta.pk_name or "id"

    # Check if it's an annotation first
    if annotations and field_name in annotations:
        # An aggregate annotation may traverse relations (Count("posts"));
        # it needs the statement's join context.
        return annotations[field_name].resolve(model, joins=joins)

    column = getattr(table.c, field_name, None)

    # NOTE: Related field traversal (e.g., 'author__name') is handled by
    # parse_path() + JoinContext (zeeb_orm.query.joins); this function only
    # resolves plain columns and annotations on the base table.

    return column


def q_to_condition(
    model: type,
    q: Q,
    joins: JoinContext | None = None,
    annotations: dict[str, Any] | None = None,
    *,
    scope: Any = None,
    negated: bool = False,
    branch_negated: bool = False,
) -> Any:
    """Convert a Q object to a SQLAlchemy condition.

    Args:
        scope: Identifies the ``filter()`` call the condition belongs to;
            multi-valued joins are shared only within one scope.
        negated: The caller negates the result (an odd number of enclosing
            NOTs). Lookups on nullable columns then keep their NULL rows,
            as in Django: ``exclude(x=1)`` compiles to
            ``NOT (x = 1 AND x IS NOT NULL)``.
        branch_negated: Some enclosing node is negated. A lookup across a
            multi-valued relation then becomes ``pk IN (subquery)`` instead
            of a condition on a shared LEFT JOIN, so the negation means "no
            related row matches" rather than "some related row does not".
    """
    connector, q_negated, children = q.resolve()

    if q._constant is not None:
        # Q.match_all() / Q.match_none(): a literal TRUE / FALSE, never
        # "no condition" — dropping it would turn match_none into match-all.
        from sqlalchemy import false, true

        return true() if q._constant is not q_negated else false()

    child_negated = negated is not q_negated
    child_branch = branch_negated or negated or q_negated

    sub_conditions = []
    for child in children:
        if isinstance(child, Q):
            cond = q_to_condition(
                model,
                child,
                joins,
                annotations,
                scope=scope,
                negated=child_negated,
                branch_negated=child_branch,
            )
        else:
            # It's a (field_lookup, value) tuple
            field_lookup, value = child
            cond = lookup_to_condition(
                model,
                field_lookup,
                value,
                joins,
                annotations,
                scope=scope,
                negated=child_negated,
                branch_negated=child_branch,
            )
        if cond is not None:
            sub_conditions.append(cond)

    if not sub_conditions:
        return None

    if connector == QOperator.AND:
        result = and_(*sub_conditions) if len(sub_conditions) > 1 else sub_conditions[0]
    else:  # OR
        result = or_(*sub_conditions) if len(sub_conditions) > 1 else sub_conditions[0]

    if q_negated:
        result = not_(result)

    return result


# Lookups whose comparison value must match the column's temporal type.
_TEMPORAL_COERCE_LOOKUPS = {"exact", "gt", "gte", "lt", "lte", "in", "range"}


def _coerce_temporal_value(column: Any, transform: str | None, lookup: str, value: Any) -> Any:
    """Coerce ISO-format strings to date/time objects for temporal columns.

    Some async drivers (e.g. asyncpg) reject string binds against
    timestamp/date/time columns, so comparison values are normalized here.
    Transformed columns (``created_at__year``) compare against plain
    ints/strings and are left untouched.
    """
    import datetime as _dt

    from sqlalchemy import Date as _SADate
    from sqlalchemy import DateTime as _SADateTime
    from sqlalchemy import Time as _SATime

    if transform is not None or lookup not in _TEMPORAL_COERCE_LOOKUPS:
        return value

    col_type = getattr(column, "type", None)
    if isinstance(col_type, _SADateTime):

        def _convert(v: Any) -> Any:
            if not isinstance(v, str):
                return v
            try:
                return _dt.datetime.fromisoformat(v)
            except ValueError:
                # Date-only string compares from midnight
                return _dt.datetime.combine(_dt.date.fromisoformat(v), _dt.time.min)
    elif isinstance(col_type, _SADate):

        def _convert(v: Any) -> Any:
            if not isinstance(v, str):
                return v
            try:
                return _dt.date.fromisoformat(v)
            except ValueError:
                return _dt.datetime.fromisoformat(v).date()
    elif isinstance(col_type, _SATime):

        def _convert(v: Any) -> Any:
            return _dt.time.fromisoformat(v) if isinstance(v, str) else v
    else:
        return value

    if isinstance(value, (list, tuple, set, frozenset)):
        return [_convert(v) for v in value]
    return _convert(value)


def lookup_to_condition(
    model: type,
    lookup_string: str,
    value: Any,
    joins: JoinContext | None = None,
    annotations: dict[str, Any] | None = None,
    *,
    scope: Any = None,
    negated: bool = False,
    branch_negated: bool = False,
) -> Any:
    """Convert a Django-style lookup to a SQLAlchemy condition.

    ``scope``, ``negated`` and ``branch_negated`` are described on
    :func:`q_to_condition`.
    """
    from zeeb_orm.exceptions import FieldError
    from zeeb_orm.query.expressions import Expression
    from zeeb_orm.query.joins import is_multi_valued_path
    from zeeb_orm.query.transforms import apply_transform

    relation_parts, field_name, transform, lookup = parse_path(model, lookup_string)

    if (
        relation_parts
        and (branch_negated or negated)
        and is_multi_valued_path(model, relation_parts)
    ):
        return _multi_valued_condition(model, lookup_string, value)

    # SQL forbids window functions in WHERE clauses — referencing a
    # Window annotation in filter()/exclude() must fail loudly.
    if not relation_parts and annotations and field_name in annotations:
        from zeeb_orm.query.expressions import Window

        if isinstance(annotations[field_name], Window):
            raise FieldError(
                f"Window annotation {field_name!r} is disallowed in the "
                "filter clause: window functions cannot be used in a "
                "WHERE clause. Filter on a subquery instead."
            )

    # Handle F expressions in value
    if isinstance(value, Expression):
        value = value.resolve(model)

    # Get the column
    if relation_parts:
        if joins is None:
            raise FieldError(
                f"Related-field traversal ({lookup_string!r}) is not supported in this context."
            )
        column = joins.column(relation_parts, field_name, scope=scope)
    else:
        column = resolve_field_path(model, field_name, annotations, joins)
    if column is None:
        field_names = sorted(f.name for f in model._meta.local_fields)
        raise FieldError(
            f"Cannot resolve keyword {field_name!r} into field on "
            f"{model.__name__}. Choices are: {', '.join(field_names)}"
        )
    base_column = column

    # Apply datetime transform (e.g. created_at__year) before the lookup
    if transform is not None:
        column = apply_transform(column, transform)

    # Coerce model instances to their primary keys
    def _coerce(v: Any) -> Any:
        if hasattr(v, "_state") and hasattr(v, "pk"):
            return v.pk
        return v

    if lookup == "in" and isinstance(value, (list, tuple, set, frozenset)):
        value = [_coerce(v) for v in value]
    else:
        value = _coerce(value)

    # Coerce ISO strings against temporal columns (asyncpg rejects str binds)
    value = _coerce_temporal_value(column, transform, lookup, value)

    condition = _apply_lookup(column, lookup, value)

    if negated and lookup != "isnull" and value is not None:
        # Under NOT, a comparison with NULL would make the whole row vanish
        # (NOT NULL is NULL). Django adds "IS NOT NULL" so the negation
        # keeps such rows: exclude(x=1) keeps x IS NULL.
        guards = []
        if relation_parts or _is_nullable_column(base_column):
            guards.append(base_column.isnot(None))
        if _is_nullable_column(value):
            guards.append(value.isnot(None))
        if guards:
            condition = and_(condition, *guards)

    return condition


def _is_nullable_column(expr: Any) -> bool:
    """True for a table/alias column declared NULL-able."""
    from sqlalchemy import Column

    return isinstance(expr, Column) and bool(expr.nullable)


def _multi_valued_condition(model: type, lookup_string: str, value: Any) -> Any:
    """``pk IN (SELECT pk FROM <model> JOIN <path> WHERE <lookup>)``.

    How a lookup across a multi-valued relation is compiled under a
    negation: ``NOT`` of it then reads "no related row matches" — Django's
    exclude() semantics — instead of "some related row does not match",
    which is what negating a condition on a shared LEFT JOIN yields (and it
    also drops objects without any related row).

    The subquery walks the relation from an alias of the model's own table,
    so it never collides with the outer statement's joins. A value that
    references the outer row (``F("field")``) still resolves against the
    outer table and correlates the subquery.
    """
    from zeeb_orm.query.joins import JoinContext

    table = model._get_table()
    meta = model._meta
    pk_name = meta.pk.db_column or meta.pk_name
    inner = table.alias(f"_mv_{table.name}")
    inner_joins = JoinContext(model, inner, alias_prefix="_mv")
    condition = lookup_to_condition(model, lookup_string, value, inner_joins)
    subquery = select(inner.c[pk_name]).select_from(inner_joins.apply(inner)).where(condition)
    return table.c[pk_name].in_(subquery)


#: Lookups compiled to LIKE / ILIKE.
_LIKE_LOOKUPS = frozenset(
    {"iexact", "contains", "icontains", "startswith", "istartswith", "endswith", "iendswith"}
)

#: Escape character for LIKE patterns built from lookup values (the one
#: SQLAlchemy's ``autoescape=True`` uses): it needs no escaping itself in
#: any dialect's string literals, unlike a backslash on MySQL.
_LIKE_ESCAPE = "/"


def _like_literal(value: Any) -> str:
    """``value`` as a LIKE pattern that matches itself only.

    ``%`` and ``_`` (and the escape character) are escaped, so
    ``email__iexact="%"`` matches the address ``%`` and not every row.
    """
    return (
        str(value)
        .replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


def _like_lookup(column: Any, lookup: str, value: Any) -> Any:
    """``contains``/``startswith``/``endswith`` and their ``i`` variants,
    plus ``iexact``, with the value's wildcards escaped on every dialect."""
    from sqlalchemy import func
    from sqlalchemy.sql.elements import ClauseElement

    if isinstance(value, ClauseElement):
        # Another column (F()): no user-supplied pattern to escape.
        if lookup == "iexact":
            return func.lower(column) == func.lower(value)
        method = getattr(column, lookup)
        return method(value)

    if lookup == "iexact":
        return column.ilike(_like_literal(value), escape=_LIKE_ESCAPE)
    method = getattr(column, lookup)
    return method(str(value), autoescape=True)


def _apply_lookup(column: Any, lookup: str, value: Any) -> Any:
    """The SQL condition for ``column <lookup> value``."""
    if lookup in _LIKE_LOOKUPS:
        if value is None:
            # Django: a None pattern is an IS NULL test for iexact, and
            # matches nothing for the containment lookups.
            if lookup == "iexact":
                return column.is_(None)
            from sqlalchemy import false

            return false()
        return _like_lookup(column, lookup, value)
    if lookup == "exact":
        return column == value
    elif lookup == "in":
        return column.in_(value)
    elif lookup == "gt":
        return column > value
    elif lookup == "gte":
        return column >= value
    elif lookup == "lt":
        return column < value
    elif lookup == "lte":
        return column <= value
    elif lookup == "range":
        value = list(value)
        return column.between(value[0], value[1])
    elif lookup == "isnull":
        if value:
            return column.is_(None)
        else:
            return column.isnot(None)
    elif lookup == "regex":
        # SQLite has no native REGEXP: SQLAlchemy registers Python's ``re``
        # as the function, so a crafted pattern can backtrack for a very
        # long time (ReDoS). Never pass untrusted patterns here.
        return column.regexp_match(value)
    elif lookup == "iregex":
        return column.regexp_match(value, flags="i")
    else:
        raise ValueError(f"Unknown lookup type: {lookup}")
