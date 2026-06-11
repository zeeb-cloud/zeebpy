"""QuerySet implementation with lazy evaluation and chaining."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    TypeVar,
)

from sqlalchemy import (
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
        self._distinct_fields: list[str] = []
        self._select_related: list[str] = []
        self._prefetch_related: list[Any] = []
        self._annotations: dict[str, Any] = {}
        self._limit: int | None = None
        self._offset: int | None = None
        self._only_fields: list[str] | None = None
        self._defer_fields: list[str] | None = None
        self._values_fields: list[str] | None = None
        self._values_list_fields: list[str] | None = None
        self._flat: bool = False
        self._db_alias: str | None = None
        self._raw_sql: str | None = None
        self._raw_params: list[Any] | None = None
        self._result_cache: list[ModelT] | None = None

    def _clone(self) -> QuerySet[ModelT]:
        """Create a copy of this QuerySet."""
        clone = QuerySet(self.model)
        clone._filters = self._filters.copy()
        clone._excludes = self._excludes.copy()
        clone._order_by = self._order_by.copy()
        clone._distinct_fields = self._distinct_fields.copy()
        clone._select_related = self._select_related.copy()
        clone._prefetch_related = self._prefetch_related.copy()
        clone._annotations = self._annotations.copy()
        clone._limit = self._limit
        clone._offset = self._offset
        clone._only_fields = self._only_fields.copy() if self._only_fields else None
        clone._defer_fields = self._defer_fields.copy() if self._defer_fields else None
        clone._values_fields = self._values_fields.copy() if self._values_fields else None
        clone._values_list_fields = (
            self._values_list_fields.copy() if self._values_list_fields else None
        )
        clone._flat = self._flat
        clone._db_alias = self._db_alias
        clone._raw_sql = self._raw_sql
        clone._raw_params = self._raw_params.copy() if self._raw_params else None
        return clone

    def _invalidate_cache(self) -> None:
        """Invalidate the result cache."""
        self._result_cache = None

    # Filtering methods

    def filter(self, *args: Q, **kwargs: Any) -> QuerySet[ModelT]:
        """
        Return a new QuerySet with the given filters applied.

        Accepts Q objects and/or keyword arguments.

        Usage:
            .filter(name='John')
            .filter(age__gte=18)
            .filter(Q(active=True) | Q(role='admin'))
        """
        clone = self._clone()
        if args:
            for arg in args:
                if isinstance(arg, Q):
                    clone._filters.append(arg)
        if kwargs:
            clone._filters.append(Q(**kwargs))
        return clone

    def exclude(self, *args: Q, **kwargs: Any) -> QuerySet[ModelT]:
        """
        Return a new QuerySet excluding objects matching the given filters.

        Usage:
            .exclude(deleted=True)
            .exclude(Q(status='inactive'))
        """
        clone = self._clone()
        if args:
            for arg in args:
                if isinstance(arg, Q):
                    clone._excludes.append(arg)
        if kwargs:
            clone._excludes.append(Q(**kwargs))
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
        return clone

    # Distinct

    def distinct(self, *fields: str) -> QuerySet[ModelT]:
        """Return a new QuerySet with distinct results."""
        clone = self._clone()
        clone._distinct_fields = list(fields) if fields else ["*"]
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
        clone = self._clone()
        clone._only_fields = list(fields)
        return clone

    def defer(self, *fields: str) -> QuerySet[ModelT]:
        """Defer loading of the specified fields."""
        clone = self._clone()
        clone._defer_fields = list(fields)
        return clone

    def values(self, *fields: str) -> QuerySet[ModelT]:
        """Return dictionaries instead of model instances."""
        clone = self._clone()
        clone._values_fields = list(fields) if fields else None
        return clone

    def values_list(self, *fields: str, flat: bool = False) -> QuerySet[ModelT]:
        """Return tuples instead of model instances."""
        clone = self._clone()
        clone._values_list_fields = list(fields)
        clone._flat = flat
        return clone

    # Related object loading

    def select_related(self, *fields: str) -> QuerySet[ModelT]:
        """
        Load related objects in the same query using JOINs.

        Usage:
            Post.objects.select_related('author')
            Post.objects.select_related('author', 'category')
        """
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
        clone = self._clone()
        clone._prefetch_related = list(lookups)
        return clone

    # Annotations and Aggregations

    def annotate(self, **kwargs: Any) -> QuerySet[ModelT]:
        """
        Add computed fields to each result.

        Usage:
            Author.objects.annotate(post_count=Count('posts'))
        """
        clone = self._clone()
        clone._annotations.update(kwargs)
        return clone

    async def aggregate(self, **kwargs: Any) -> dict[str, Any]:
        """
        Compute aggregate values over the entire QuerySet.

        Usage:
            await Author.objects.aggregate(avg_age=Avg('age'))
        """
        from zeeb_orm.db.connection import get_connection

        db = await get_connection(self._db_alias)
        table = self.model._get_table()
        joins = self._make_join_context()

        select_exprs = []
        for alias, agg in kwargs.items():
            if hasattr(agg, "resolve"):
                select_exprs.append(agg.resolve(self.model).label(alias))

        # Apply filters (may register traversal JOINs)
        where_clause = self._build_where_clause(joins)

        stmt = select(*select_exprs).select_from(
            joins.apply(table) if joins.has_joins else table
        )
        if where_clause is not None:
            stmt = stmt.where(where_clause)

        async with db.session() as session:
            result = await session.execute(stmt)
            row = result.fetchone()
            if row:
                return dict(row._mapping)
            return {alias: None for alias in kwargs}

    # Database selection

    def using(self, alias: str) -> QuerySet[ModelT]:
        """Use a specific database connection."""
        clone = self._clone()
        clone._db_alias = alias
        return clone

    # Raw SQL

    def raw(self, sql: str, params: list[Any] | None = None) -> QuerySet[ModelT]:
        """Execute a raw SQL query."""
        clone = self._clone()
        clone._raw_sql = sql
        clone._raw_params = params
        return clone

    # Query building

    def _make_join_context(self) -> JoinContext:
        """Create a fresh per-statement JoinContext (never stored on self)."""
        from zeeb_orm.query.joins import JoinContext

        return JoinContext(self.model, self.model._get_table())

    def _pk_in_join_subquery(
        self, table: Any, joins: JoinContext, where_clause: Any
    ) -> Any:
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
        conditions = []

        for q in self._filters:
            condition = self._q_to_condition(q, joins)
            if condition is not None:
                conditions.append(condition)

        for q in self._excludes:
            condition = self._q_to_condition(q, joins)
            if condition is not None:
                conditions.append(not_(condition))

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return and_(*conditions)

    def _q_to_condition(self, q: Q, joins: JoinContext | None = None) -> Any:
        """Convert a Q object to SQLAlchemy condition."""
        connector, negated, children = q.resolve()

        sub_conditions = []
        for child in children:
            if isinstance(child, Q):
                cond = self._q_to_condition(child, joins)
                if cond is not None:
                    sub_conditions.append(cond)
            else:
                # It's a (field_lookup, value) tuple
                field_lookup, value = child
                cond = self._lookup_to_condition(field_lookup, value, joins)
                if cond is not None:
                    sub_conditions.append(cond)

        if not sub_conditions:
            return None

        if connector == QOperator.AND:
            result = and_(*sub_conditions) if len(sub_conditions) > 1 else sub_conditions[0]
        else:  # OR
            result = or_(*sub_conditions) if len(sub_conditions) > 1 else sub_conditions[0]

        if negated:
            result = not_(result)

        return result

    def _lookup_to_condition(
        self, lookup_string: str, value: Any, joins: JoinContext | None = None
    ) -> Any:
        """Convert a Django-style lookup to SQLAlchemy condition."""
        from zeeb_orm.exceptions import FieldError
        from zeeb_orm.query.expressions import Expression
        from zeeb_orm.query.transforms import apply_transform

        relation_parts, field_name, transform, lookup = parse_path(
            self.model, lookup_string
        )

        # Handle F expressions in value
        if isinstance(value, Expression):
            value = value.resolve(self.model)

        # Get the column
        if relation_parts:
            if joins is None:
                raise FieldError(
                    f"Related-field traversal ({lookup_string!r}) is not "
                    "supported in this context."
                )
            column = joins.column(relation_parts, field_name)
        else:
            column = self._resolve_field_path(field_name)
        if column is None:
            raise ValueError(f"Unknown field: {field_name}")

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

        # Apply lookup
        if lookup == "exact":
            return column == value
        elif lookup == "iexact":
            return column.ilike(value)
        elif lookup == "contains":
            return column.contains(value)
        elif lookup == "icontains":
            return column.ilike(f"%{value}%")
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
        elif lookup == "startswith":
            return column.startswith(value)
        elif lookup == "istartswith":
            return column.ilike(f"{value}%")
        elif lookup == "endswith":
            return column.endswith(value)
        elif lookup == "iendswith":
            return column.ilike(f"%{value}")
        elif lookup == "range":
            return column.between(value[0], value[1])
        elif lookup == "isnull":
            if value:
                return column.is_(None)
            else:
                return column.isnot(None)
        elif lookup == "regex":
            return column.regexp_match(value)
        elif lookup == "iregex":
            return column.regexp_match(value, flags="i")
        else:
            raise ValueError(f"Unknown lookup type: {lookup}")

    def _resolve_field_path(self, field_path: str) -> Any:
        """Resolve a field path (potentially with __ for relations) to a column."""
        parts = field_path.split("__")
        table = self.model._get_table()

        # Handle 'pk' as alias for primary key
        field_name = parts[0]
        if field_name == "pk":
            # Use the pk_name from meta or default to 'id'
            field_name = self.model._meta.pk_name or "id"

        # Check if it's an annotation first
        if field_name in self._annotations:
            expr = self._annotations[field_name]
            if hasattr(expr, 'resolve'):
                return expr.resolve(self.model)
            return literal_column(field_name)

        column = getattr(table.c, field_name, None)

        # NOTE: Related field traversal (e.g., 'author__name') is handled by
        # parse_path() + JoinContext (zeeb_orm.query.joins); this method only
        # resolves plain columns and annotations on the base table.

        return column

    def _build_order_by(self, joins: JoinContext | None = None) -> list[Any]:
        """Build SQLAlchemy ORDER BY clause."""
        from sqlalchemy import asc, desc

        order_clauses = []
        table = self.model._get_table()

        for field in self._order_by:
            descending = field.startswith("-")
            field_name = field[1:] if descending else field

            # Check if it's an annotation
            if field_name in self._annotations:
                expr = self._annotations[field_name]
                if hasattr(expr, 'resolve'):
                    column = expr.resolve(self.model)
                else:
                    column = literal_column(field_name)
            elif "__" in field_name:
                column = self._resolve_path_expression(field_name, joins)
            else:
                column = getattr(table.c, field_name, None)

            if column is not None:
                if descending:
                    order_clauses.append(desc(column))
                else:
                    order_clauses.append(asc(column))

        return order_clauses

    def _resolve_path_expression(
        self, path: str, joins: JoinContext | None
    ) -> Any:
        """Resolve a ``__`` path (relations and/or datetime transform) to a
        SQLAlchemy expression, registering JOINs on ``joins`` as needed."""
        from zeeb_orm.query.transforms import apply_transform

        relation_parts, field_name, transform, _lookup = parse_path(
            self.model, path
        )
        if relation_parts:
            if joins is None:
                return None
            column = joins.column(relation_parts, field_name)
        else:
            column = self._resolve_field_path(field_name)
        if column is not None and transform is not None:
            column = apply_transform(column, transform)
        return column

    def _select_related_columns(self, joins: JoinContext) -> list[Any]:
        """Register select_related JOINs and return their labeled columns.

        Every cumulative prefix of each select_related path gets its own
        JOIN (shared with filter/order traversal via ``joins``) and its
        columns labeled ``_sr_{prefix}_{col}`` for hydration.
        """
        prefixes: list[tuple[str, ...]] = []
        for field_path in self._select_related:
            parts = field_path.split("__")
            for i in range(1, len(parts) + 1):
                prefix = tuple(parts[:i])
                if prefix not in prefixes:
                    prefixes.append(prefix)

        columns: list[Any] = []
        for prefix in prefixes:
            info = joins.ensure_join(list(prefix))
            if info.relation.kind not in ("fk", "o2o"):
                raise ValueError(
                    f"select_related: '{prefix[-1]}' is not a ForeignKey "
                    f"field on {info.relation.source_model.__name__}"
                )
            label_prefix = "_".join(prefix)
            for col in info.alias.c:
                columns.append(col.label(f"_sr_{label_prefix}_{col.name}"))
        return columns

    def _values_path_columns(self, joins: JoinContext) -> list[Any]:
        """Labeled columns for ``__`` paths in values()/values_list()."""
        path_fields: list[str] = []
        for f in (self._values_fields or []) + (self._values_list_fields or []):
            if "__" in f and f not in self._annotations and f not in path_fields:
                path_fields.append(f)

        columns = []
        for f in path_fields:
            column = self._resolve_path_expression(f, joins)
            if column is None:
                raise ValueError(f"Unknown field: {f}")
            columns.append(column.label(f))
        return columns

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

    def _build_select(self) -> Select[Any]:
        """Build the complete SELECT statement."""
        table = self.model._get_table()
        joins = self._make_join_context()

        # Build select columns
        columns = list(self._get_select_columns(table))

        # select_related paths register their JOINs FIRST so filter/order
        # traversal of the same path reuses the same aliases (single JOIN).
        if self._select_related:
            columns.extend(self._select_related_columns(joins))

        # Labeled columns for values()/values_list() "__" paths
        columns.extend(self._values_path_columns(joins))

        if self._annotations:
            for alias, expr in self._annotations.items():
                if hasattr(expr, 'resolve'):
                    columns.append(expr.resolve(self.model).label(alias))
                else:
                    columns.append(literal_column(str(expr)).label(alias))

        # Apply filters (registers traversal JOINs on the shared context)
        where_clause = self._build_where_clause(joins)

        stmt = select(*columns)
        if where_clause is not None:
            stmt = stmt.where(where_clause)

        # Apply ordering - can also order by annotations and "__" paths
        order_clauses = self._build_order_by(joins)
        if order_clauses:
            stmt = stmt.order_by(*order_clauses)
        elif self.model._meta.ordering:
            # Apply default ordering from Meta
            for field in self.model._meta.ordering:
                if field.startswith("-"):
                    col = getattr(table.c, field[1:], None)
                    if col is not None:
                        stmt = stmt.order_by(col.desc())
                else:
                    col = getattr(table.c, field, None)
                    if col is not None:
                        stmt = stmt.order_by(col.asc())

        # One FROM clause containing all registered JOINs
        if joins.has_joins:
            stmt = stmt.select_from(joins.apply(table))

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

    async def _fetch_all(self) -> list[Any]:
        """Execute the query and return all results."""
        if self._result_cache is not None:
            return self._result_cache

        from zeeb_orm.db.connection import get_connection

        db = await get_connection(self._db_alias)

        # Handle raw SQL mode
        if self._raw_sql is not None:
            return await self._fetch_raw(db)

        stmt = self._build_select()

        async with db.session() as session:
            result = await session.execute(stmt)
            rows = result.fetchall()

            # Handle values() mode - return dicts
            if self._values_fields is not None:
                instances = []
                fields = self._values_fields or [f.name for f in self.model._meta.local_fields]
                # Include annotations if requested
                all_fields = list(fields)
                for alias in self._annotations:
                    if alias not in all_fields:
                        all_fields.append(alias)
                for row in rows:
                    if hasattr(row, "_mapping"):
                        d = {f: row._mapping.get(f) for f in all_fields}
                    else:
                        d = {f: row[i] for i, f in enumerate(all_fields) if i < len(row)}
                    instances.append(d)
                self._result_cache = instances
                return instances

            # Handle values_list() mode - return tuples
            if self._values_list_fields is not None:
                instances = []
                fields = self._values_list_fields
                for row in rows:
                    if hasattr(row, "_mapping"):
                        values = tuple(row._mapping.get(f) for f in fields)
                    else:
                        values = tuple(row[i] for i in range(len(fields)) if i < len(row))

                    if self._flat and len(fields) == 1:
                        instances.append(values[0])
                    else:
                        instances.append(values)
                self._result_cache = instances
                return instances

            # Default: convert rows to model instances
            instances = []
            for row in rows:
                instance = self.model._from_row(row)
                # Attach annotation values as attributes
                if self._annotations and hasattr(row, "_mapping"):
                    for alias in self._annotations:
                        if alias in row._mapping:
                            setattr(instance, alias, row._mapping[alias])
                # Hydrate select_related objects from joined columns
                if self._select_related and hasattr(row, "_mapping"):
                    self._hydrate_select_related(instance, row._mapping)
                instances.append(instance)

            # Handle prefetch_related after main fetch
            if self._prefetch_related and instances:
                await self._do_prefetch_related(instances, db)

            self._result_cache = instances
            return instances

    async def _fetch_raw(self, db: Any) -> list[Any]:
        """Execute a raw SQL query and return model instances."""
        from sqlalchemy import text

        async with db.session() as session:
            stmt = text(self._raw_sql)
            params = {}
            if self._raw_params:
                # Support both positional (list) and named (dict) params
                if isinstance(self._raw_params, dict):
                    params = self._raw_params
                else:
                    # Convert positional list to dict with :param_0, :param_1, etc.
                    params = {f"param_{i}": v for i, v in enumerate(self._raw_params)}
                    # Replace ? placeholders with :param_N
                    sql = self._raw_sql
                    for i in range(len(self._raw_params)):
                        sql = sql.replace("?", f":param_{i}", 1)
                    stmt = text(sql)

            result = await session.execute(stmt, params)
            rows = result.fetchall()

            instances = []
            for row in rows:
                instance = self.model._from_row(row)
                instances.append(instance)

            self._result_cache = instances
            return instances

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
                related_kwargs = {}
                has_data = False
                for field in target_model._meta.local_fields:
                    col_name = field.db_column or field.name
                    key = f"_sr_{prefix}_{col_name}"
                    value = mapping.get(key)
                    if value is not None:
                        has_data = True
                    if isinstance(field, ForeignKeyField):
                        related_kwargs[f"{field.name}_id"] = value
                    else:
                        related_kwargs[field.name] = value

                if has_data:
                    related_obj = target_model(**related_kwargs)
                    related_obj._state.persisted = True
                    # Cache on the instance so FK access returns it directly
                    setattr(current_instance, f"_cache_{part}", related_obj)
                    current_model = target_model
                    current_instance = related_obj
                else:
                    break

    async def _do_prefetch_related(self, instances: list[Any], db: Any) -> None:
        """Execute separate queries for prefetch_related lookups."""

        for lookup in self._prefetch_related:
            if isinstance(lookup, Prefetch):
                field_name = lookup.lookup
                custom_qs = lookup.queryset
                to_attr = lookup.to_attr
            else:
                field_name = str(lookup)
                custom_qs = None
                to_attr = None

            attr_name = to_attr or field_name

            # Determine if this is a FK relation (forward) or reverse relation
            fk_field = None
            for f in self.model._fk_fields:
                if f.name == field_name:
                    fk_field = f
                    break

            if fk_field is not None:
                # Forward FK prefetch: collect FK IDs and fetch target objects
                target_model = fk_field.get_target_model()
                fk_ids = set()
                for inst in instances:
                    fk_id = getattr(inst, f"_field_{field_name}_id", None)
                    if fk_id is not None:
                        fk_ids.add(fk_id)

                if not fk_ids:
                    continue

                if custom_qs is not None:
                    related_qs = custom_qs.filter(pk__in=list(fk_ids))
                else:
                    related_qs = QuerySet(target_model).filter(pk__in=list(fk_ids))
                related_qs._db_alias = self._db_alias

                related_objects = await related_qs._fetch_all()
                pk_name = target_model._meta.pk_name
                related_map = {getattr(obj, pk_name): obj for obj in related_objects}

                for inst in instances:
                    fk_id = getattr(inst, f"_field_{field_name}_id", None)
                    if fk_id is not None and fk_id in related_map:
                        setattr(inst, f"_cache_{field_name}", related_map[fk_id])
            else:
                # Reverse relation prefetch: find which model has FK pointing to us
                from zeeb_orm.models.relations import resolve_relation

                relation = resolve_relation(self.model, field_name)
                if relation is None or relation.kind not in ("reverse_fk", "reverse_o2o"):
                    continue

                related_model = relation.target_model
                reverse_fk_name = relation.fk_column

                pk_name = self.model._meta.pk_name
                parent_pks = [getattr(inst, pk_name) for inst in instances]

                if custom_qs is not None:
                    related_qs = custom_qs.filter(**{f"{reverse_fk_name}__in": parent_pks})
                else:
                    related_qs = QuerySet(related_model).filter(
                        **{f"{reverse_fk_name}__in": parent_pks}
                    )
                related_qs._db_alias = self._db_alias

                related_objects = await related_qs._fetch_all()

                # Group by FK value
                grouped: dict[Any, list[Any]] = {}
                for obj in related_objects:
                    fk_val = getattr(obj, f"_field_{reverse_fk_name}", None)
                    if fk_val is None:
                        # Try direct attribute
                        fk_val = getattr(obj, reverse_fk_name, None)
                    if fk_val is not None:
                        grouped.setdefault(fk_val, []).append(obj)

                for inst in instances:
                    pk_val = getattr(inst, pk_name)
                    setattr(inst, attr_name, grouped.get(pk_val, []))

    async def __aiter__(self) -> AsyncIterator[Any]:
        """Async iteration support."""
        results = await self._fetch_all()
        for item in results:
            yield item

    def __iter__(self) -> Iterator[Any]:
        """Sync iteration (runs event loop)."""
        import asyncio

        try:
            asyncio.get_running_loop()
            raise RuntimeError(
                "Cannot use sync iteration inside an async context. Use 'async for' instead."
            )
        except RuntimeError:
            pass

        results = asyncio.run(self._fetch_all())
        return iter(results)

    def __await__(self) -> Any:
        """Allow awaiting QuerySet directly to get list of results."""
        return self._fetch_all().__await__()

    async def __aenter__(self) -> QuerySet[ModelT]:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

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
            raise self.model.DoesNotExist(
                f"{self.model.__name__} matching query does not exist."
            )
        if len(results) > 1:
            raise self.model.MultipleObjectsReturned(
                f"get() returned more than one {self.model.__name__}"
            )
        return results[0]

    async def first(self) -> ModelT | None:
        """Get the first object or None."""
        clone = self._clone()
        clone._limit = 1
        results = await clone._fetch_all()
        return results[0] if results else None

    async def last(self) -> ModelT | None:
        """Get the last object or None."""
        clone = self._clone()
        # Reverse ordering
        if clone._order_by:
            clone._order_by = [
                f"-{f}" if not f.startswith("-") else f[1:] for f in clone._order_by
            ]
        clone._limit = 1
        results = await clone._fetch_all()
        return results[0] if results else None

    async def count(self) -> int:
        """Count objects matching the query."""
        from zeeb_orm.db.connection import get_connection

        db = await get_connection(self._db_alias)
        table = self.model._get_table()
        joins = self._make_join_context()

        where_clause = self._build_where_clause(joins)

        stmt = select(func.count()).select_from(
            joins.apply(table) if joins.has_joins else table
        )
        if where_clause is not None:
            stmt = stmt.where(where_clause)

        async with db.session() as session:
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def exists(self) -> bool:
        """Check if any objects match the query."""
        clone = self._clone()
        clone._limit = 1
        results = await clone._fetch_all()
        return len(results) > 0

    # CRUD operations

    async def create(self, **kwargs: Any) -> ModelT:
        """Create and save a new object."""
        from datetime import datetime as dt
        from datetime import timezone

        from sqlalchemy import insert

        from zeeb_orm.db.connection import get_session
        from zeeb_orm.models.fields import ForeignKeyField

        # Pre-process kwargs: convert model instances to IDs for FK fields
        processed_kwargs = {}
        for key, value in kwargs.items():
            if hasattr(value, "pk"):
                # It's a model instance - extract the pk
                processed_kwargs[f"{key}_id"] = value.pk
            else:
                processed_kwargs[key] = value

        instance = self.model(**processed_kwargs)
        table = self.model._get_table()

        # Prepare values for insert
        values = {}
        for field in self.model._meta.local_fields:
            value = None

            # For FK fields, get the _id value
            if isinstance(field, ForeignKeyField):
                value = getattr(instance, f"{field.name}_id", None)
            else:
                value = getattr(instance, field.name, None)

            # Handle primary key with callable default (e.g., UUIDAutoField)
            if field.primary_key and value is None:
                if hasattr(field, 'default') and field.default is not None:
                    if callable(field.default):
                        value = field.default()
                    else:
                        value = field.default
                    setattr(instance, field.name, value)
                else:
                    # Let DB generate auto PK (for integer auto-increment)
                    continue

            # Handle auto timestamps - both auto_now and auto_now_add should set on create
            if hasattr(field, 'auto_now') and field.auto_now and value is None:
                if hasattr(field, 'timezone'):
                    value = dt.now(timezone.utc)
                else:
                    from datetime import date
                    value = date.today()
                setattr(instance, field.name, value)
            elif hasattr(field, 'auto_now_add') and field.auto_now_add and value is None:
                if hasattr(field, 'timezone'):
                    value = dt.now(timezone.utc)
                else:
                    from datetime import date
                    value = date.today()
                setattr(instance, field.name, value)

            # Handle non-PK callable defaults
            if value is None and hasattr(field, 'default') and field.default is not None:
                if callable(field.default):
                    value = field.default()
                else:
                    value = field.default
                setattr(instance, field.name, value)

            if value is not None:
                values[field.db_column or field.name] = value

        async with get_session(self._db_alias) as (session, should_commit):
            stmt = insert(table).values(**values)
            result = await session.execute(stmt)
            if should_commit:
                await session.commit()

            # Get the inserted PK
            pk_name = self.model._meta.pk_name
            if result.inserted_primary_key:
                setattr(instance, pk_name, result.inserted_primary_key[0])

            instance._state.persisted = True
            return instance

    async def get_or_create(
        self, defaults: dict[str, Any] | None = None, **kwargs: Any
    ) -> tuple[ModelT, bool]:
        """
        Get an object or create it if it doesn't exist.

        Returns (instance, created) tuple.
        """
        defaults = defaults or {}
        try:
            instance = await self.get(**kwargs)
            return instance, False
        except self.model.DoesNotExist:
            create_kwargs = {**kwargs, **defaults}
            instance = await self.create(**create_kwargs)
            return instance, True

    async def update_or_create(
        self, defaults: dict[str, Any] | None = None, **kwargs: Any
    ) -> tuple[ModelT, bool]:
        """
        Update an object or create it if it doesn't exist.

        Returns (instance, created) tuple.
        """
        defaults = defaults or {}
        try:
            instance = await self.get(**kwargs)
            for key, value in defaults.items():
                setattr(instance, key, value)
            await instance.save()
            return instance, False
        except self.model.DoesNotExist:
            create_kwargs = {**kwargs, **defaults}
            instance = await self.create(**create_kwargs)
            return instance, True

    async def update(self, **kwargs: Any) -> int:
        """
        Update all objects matching the query.

        Returns the number of rows updated.
        """
        from zeeb_orm.db.connection import get_session
        from zeeb_orm.query.expressions import Expression

        table = self.model._get_table()

        # Resolve F expressions and other Expression objects
        resolved_kwargs = {}
        for key, value in kwargs.items():
            if isinstance(value, Expression):
                resolved_kwargs[key] = value.resolve(self.model)
            else:
                resolved_kwargs[key] = value

        stmt = update(table).values(**resolved_kwargs)

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

        Returns the number of rows deleted.
        """
        from zeeb_orm.db.connection import get_session

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
    ) -> list[ModelT]:
        """Insert multiple objects efficiently."""
        from sqlalchemy import insert as _sa_insert

        from zeeb_orm.db.connection import get_session

        if not objs:
            return []

        batch_size = batch_size or 1000
        table = self.model._get_table()
        pk_name = self.model._meta.pk_name

        created: list[ModelT] = []
        async with get_session(self._db_alias) as (session, should_commit):
            for i in range(0, len(objs), batch_size):
                batch = objs[i : i + batch_size]
                for obj in batch:
                    values = obj._to_insert_values()
                    stmt = _sa_insert(table).values(**values)
                    result = await session.execute(stmt)

                    # Read back DB-generated PK for auto-increment fields
                    if getattr(obj, pk_name) is None:
                        pk_value = result.inserted_primary_key[0]
                        setattr(obj, pk_name, pk_value)

                    obj._state.persisted = True
                    created.append(obj)

            if should_commit:
                await session.commit()

        return created

    async def bulk_update(
        self,
        objs: list[ModelT],
        fields: list[str],
        *,
        batch_size: int | None = None,
    ) -> int:
        """Update multiple objects efficiently."""
        from zeeb_orm.db.connection import get_session

        if not objs or not fields:
            return 0

        batch_size = batch_size or 1000
        pk_name = self.model._meta.pk_name

        count = 0
        async with get_session(self._db_alias) as (session, should_commit):
            for obj in objs:
                pk_value = getattr(obj, pk_name)
                values = {f: getattr(obj, f) for f in fields}

                table = self.model._get_table()
                pk_col = getattr(table.c, pk_name)
                stmt = update(table).where(pk_col == pk_value).values(**values)
                result = await session.execute(stmt)
                count += result.rowcount

            if should_commit:
                await session.commit()

        return count

    # Representation

    def __repr__(self) -> str:
        return f"<QuerySet [{self.model.__name__}]>"

    def __len__(self) -> int:
        """Sync length (runs event loop)."""
        import asyncio

        return asyncio.run(self.count())


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
