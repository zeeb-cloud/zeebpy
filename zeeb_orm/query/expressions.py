"""F expressions and aggregation functions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement


class Expression:
    """Base class for SQL expressions."""

    def __init__(self) -> None:
        self._output_field: Any = None

    def resolve(self, model: Any) -> Any:
        """Resolve expression to SQLAlchemy expression."""
        raise NotImplementedError

    def __add__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(self, "+", other)

    def __radd__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(other, "+", self)

    def __sub__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(self, "-", other)

    def __rsub__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(other, "-", self)

    def __mul__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(self, "*", other)

    def __rmul__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(other, "*", self)

    def __truediv__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(self, "/", other)

    def __rtruediv__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(other, "/", self)

    def __mod__(self, other: Any) -> CombinedExpression:
        return CombinedExpression(self, "%", other)

    def __neg__(self) -> CombinedExpression:
        return CombinedExpression(self, "*", -1)


class F(Expression):
    """
    Reference to a model field for use in queries.

    Allows referencing field values in filters, updates, and annotations.

    Usage:
        # Filter where field1 > field2
        Model.objects.filter(field1__gt=F('field2'))

        # Increment a field
        Model.objects.update(view_count=F('view_count') + 1)

        # Reference related field
        Model.objects.filter(price__lt=F('discount_price'))
    """

    def __init__(self, field_name: str) -> None:
        super().__init__()
        self.field_name = field_name

    def resolve(self, model: Any) -> Any:
        """Resolve to SQLAlchemy column."""
        parts = self.field_name.split("__")

        # Get the table from the model
        if hasattr(model, "_get_table"):
            table = model._get_table()
            col = getattr(table.c, parts[0], None)
            if col is not None:
                return col
            raise ValueError(f"Field '{parts[0]}' not found on {model}")
        elif hasattr(model, "__table__"):
            col = getattr(model.__table__.c, parts[0], None)
            if col is not None:
                return col
            raise ValueError(f"Field '{parts[0]}' not found on {model}")
        else:
            raise ValueError(f"Cannot resolve field path '{self.field_name}'")

    def __repr__(self) -> str:
        return f"F({self.field_name!r})"


class CombinedExpression(Expression):
    """Expression combining two values with an operator."""

    def __init__(self, lhs: Any, operator: str, rhs: Any) -> None:
        super().__init__()
        self.lhs = lhs
        self.operator = operator
        self.rhs = rhs

    def resolve(self, model: Any) -> Any:
        """Resolve to SQLAlchemy expression."""
        from sqlalchemy import literal

        lhs = self.lhs.resolve(model) if isinstance(self.lhs, Expression) else literal(self.lhs)
        rhs = self.rhs.resolve(model) if isinstance(self.rhs, Expression) else literal(self.rhs)

        ops = {
            "+": lambda a, b: a + b,
            "-": lambda a, b: a - b,
            "*": lambda a, b: a * b,
            "/": lambda a, b: a / b,
            "%": lambda a, b: a % b,
        }
        return ops[self.operator](lhs, rhs)

    def __repr__(self) -> str:
        return f"({self.lhs!r} {self.operator} {self.rhs!r})"


class Value(Expression):
    """Wrap a Python value as an expression."""

    def __init__(self, value: Any) -> None:
        super().__init__()
        self.value = value

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import literal

        return literal(self.value)

    def __repr__(self) -> str:
        return f"Value({self.value!r})"


# Aggregate functions


class Aggregate(Expression):
    """Base class for aggregate functions."""

    function: str = ""
    allow_distinct: bool = False

    def __init__(
        self,
        expression: str | Expression,
        *,
        distinct: bool = False,
        filter: Any = None,
        alias: str | None = None,
    ) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
        self.distinct = distinct
        self.filter = filter
        self.alias = alias

    def resolve(self, model: Any) -> Any:
        """Resolve to SQLAlchemy aggregate function."""
        from sqlalchemy import func

        expr = self.expression.resolve(model)
        agg_func = getattr(func, self.function.lower())

        if self.distinct and self.allow_distinct:
            return agg_func(expr.distinct())
        return agg_func(expr)

    def __repr__(self) -> str:
        distinct = "DISTINCT " if self.distinct else ""
        return f"{self.function}({distinct}{self.expression!r})"


class Count(Aggregate):
    """COUNT aggregate function."""

    function = "COUNT"
    allow_distinct = True

    def __init__(
        self,
        expression: str | Expression = "*",
        *,
        distinct: bool = False,
        **kwargs: Any,
    ) -> None:
        if expression == "*":
            # Count all rows
            super().__init__("*", distinct=False, **kwargs)
        else:
            super().__init__(expression, distinct=distinct, **kwargs)

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func, literal_column

        if isinstance(self.expression, F) and self.expression.field_name == "*":
            return func.count(literal_column("*"))
        return super().resolve(model)


class Sum(Aggregate):
    """SUM aggregate function."""

    function = "SUM"
    allow_distinct = True


class Avg(Aggregate):
    """AVG aggregate function."""

    function = "AVG"
    allow_distinct = True


class Min(Aggregate):
    """MIN aggregate function."""

    function = "MIN"


class Max(Aggregate):
    """MAX aggregate function."""

    function = "MAX"


class StdDev(Aggregate):
    """Standard deviation aggregate function."""

    function = "STDDEV"


class Variance(Aggregate):
    """Variance aggregate function."""

    function = "VARIANCE"


# Utility expressions


class Coalesce(Expression):
    """COALESCE SQL function - returns first non-null value."""

    def __init__(self, *expressions: str | Expression | Any) -> None:
        super().__init__()
        self.expressions = [
            e if isinstance(e, Expression) else (F(e) if isinstance(e, str) else Value(e))
            for e in expressions
        ]

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func

        resolved = [e.resolve(model) for e in self.expressions]
        return func.coalesce(*resolved)


class Case(Expression):
    """
    CASE WHEN SQL expression.
    
    Usage:
        Case(
            When(status='active', then=Value(1)),
            When(status='inactive', then=Value(0)),
            default=Value(-1)
        )
        
        # Or with tuples (condition, result):
        Case(
            (Q(price__gte=100), Value('expensive')),
            default=Value('cheap')
        )
    """

    def __init__(
        self,
        *whens: "When | tuple[Any, Any]",
        default: Any = None,
    ) -> None:
        super().__init__()
        self.whens = whens
        self.default = default

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import case, literal

        cases = []
        for when in self.whens:
            if isinstance(when, When):
                # When object - resolve it
                cond, res = when.resolve(model)
                cases.append((cond, res))
            else:
                # Tuple (condition, result)
                condition, result = when
                cond = condition.resolve(model) if isinstance(condition, Expression) else condition
                res = result.resolve(model) if isinstance(result, Expression) else literal(result)
                cases.append((cond, res))

        else_val = None
        if self.default is not None:
            else_val = (
                self.default.resolve(model)
                if isinstance(self.default, Expression)
                else literal(self.default)
            )

        return case(*cases, else_=else_val)


class When(Expression):
    """
    WHEN clause for use with Case.
    
    Usage:
        Case(
            When(status='active', then=Value(1)),
            When(price__gte=500, then=Value('premium')),
            When(status='inactive', then=Value(0)),
            default=Value(-1)
        )
    """
    
    def __init__(self, then: Any = None, **condition: Any) -> None:
        super().__init__()
        self.condition = condition
        self.then = then
    
    def resolve(self, model: Any) -> tuple[Any, Any]:
        """Resolve to (condition, result) tuple for Case."""
        from sqlalchemy import and_, literal
        from zeeb_orm.query.q import parse_lookup
        
        conditions = []
        table = model._get_table() if hasattr(model, '_get_table') else model.__table__
        
        for lookup_string, value in self.condition.items():
            # Parse Django-style lookups (e.g., price__gte -> price, gte)
            field_path, lookup = parse_lookup(lookup_string)
            col = getattr(table.c, field_path, None)
            
            if col is None:
                continue
                
            # Apply lookup
            if lookup == "exact":
                conditions.append(col == value)
            elif lookup == "iexact":
                conditions.append(col.ilike(value))
            elif lookup == "gt":
                conditions.append(col > value)
            elif lookup == "gte":
                conditions.append(col >= value)
            elif lookup == "lt":
                conditions.append(col < value)
            elif lookup == "lte":
                conditions.append(col <= value)
            elif lookup == "in":
                conditions.append(col.in_(value))
            elif lookup == "contains":
                conditions.append(col.contains(value))
            elif lookup == "icontains":
                conditions.append(col.ilike(f"%{value}%"))
            elif lookup == "startswith":
                conditions.append(col.startswith(value))
            elif lookup == "endswith":
                conditions.append(col.endswith(value))
            elif lookup == "isnull":
                if value:
                    conditions.append(col.is_(None))
                else:
                    conditions.append(col.isnot(None))
            else:
                # Default to exact match
                conditions.append(col == value)
        
        if not conditions:
            raise ValueError("When() requires at least one condition")
        
        cond = and_(*conditions) if len(conditions) > 1 else conditions[0]
        
        # Resolve result value
        if isinstance(self.then, Expression):
            result = self.then.resolve(model)
        elif self.then is not None:
            result = literal(self.then)
        else:
            result = literal(None)
            
        return (cond, result)


class Subquery(Expression):
    """
    Subquery expression for use in annotations.
    
    Usage:
        from zeeb_orm.query import Subquery, OuterRef
        
        # Get the latest comment date for each post
        latest_comment = Comment.objects.filter(
            post_id=OuterRef('id')
        ).order_by('-created_at').values('created_at')[:1]
        
        Post.objects.annotate(latest_comment_date=Subquery(latest_comment))
    """
    
    def __init__(self, queryset: Any, output_field: Any = None) -> None:
        super().__init__()
        self.queryset = queryset
        self._output_field = output_field
    
    def resolve(self, model: Any) -> Any:
        """Resolve to SQLAlchemy scalar subquery."""
        from sqlalchemy import select
        
        # Get the inner query
        inner_qs = self.queryset
        
        # Build the subquery statement
        inner_stmt = inner_qs._build_select()
        
        # Resolve any OuterRef in filters
        inner_stmt = self._resolve_outer_refs(inner_stmt, model)
        
        return inner_stmt.scalar_subquery()
    
    def _resolve_outer_refs(self, stmt: Any, outer_model: Any) -> Any:
        """Resolve OuterRef references to outer query columns."""
        # This is handled at query build time
        return stmt


class OuterRef(Expression):
    """
    Reference to a field in the outer query (for correlated subqueries).
    
    Usage:
        Comment.objects.filter(post_id=OuterRef('id'))
    """
    
    def __init__(self, field_name: str) -> None:
        super().__init__()
        self.field_name = field_name
        self._outer_model: Any = None
    
    def resolve(self, model: Any) -> Any:
        """Resolve to the outer model's column."""
        if self._outer_model is not None:
            model = self._outer_model
        
        table = model._get_table() if hasattr(model, '_get_table') else model.__table__
        col = getattr(table.c, self.field_name, None)
        if col is None:
            raise ValueError(f"Field '{self.field_name}' not found on outer query model")
        return col
    
    def set_outer_model(self, model: Any) -> None:
        """Set the outer model for resolution."""
        self._outer_model = model
    
    def __repr__(self) -> str:
        return f"OuterRef({self.field_name!r})"


class Exists(Expression):
    """
    EXISTS subquery expression.
    
    Usage:
        # Get posts that have at least one comment
        Post.objects.annotate(
            has_comments=Exists(Comment.objects.filter(post_id=OuterRef('id')))
        ).filter(has_comments=True)
    """
    
    def __init__(self, queryset: Any) -> None:
        super().__init__()
        self.queryset = queryset
    
    def resolve(self, model: Any) -> Any:
        """Resolve to SQLAlchemy EXISTS expression."""
        from sqlalchemy import exists
        
        inner_stmt = self.queryset._build_select()
        return exists(inner_stmt)
    
    def __repr__(self) -> str:
        return f"Exists({self.queryset!r})"


# String functions

class Concat(Expression):
    """
    Concatenate strings.
    
    Usage:
        User.objects.annotate(full_name=Concat('first_name', Value(' '), 'last_name'))
    """
    
    def __init__(self, *expressions: str | Expression) -> None:
        super().__init__()
        self.expressions = [
            e if isinstance(e, Expression) else F(e) if isinstance(e, str) else Value(e)
            for e in expressions
        ]
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        resolved = [e.resolve(model) for e in self.expressions]
        return func.concat(*resolved)


class Lower(Expression):
    """Convert to lowercase."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.lower(self.expression.resolve(model))


class Upper(Expression):
    """Convert to uppercase."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.upper(self.expression.resolve(model))


class Length(Expression):
    """Get string length."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.length(self.expression.resolve(model))


class Trim(Expression):
    """Trim whitespace from string."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.trim(self.expression.resolve(model))


class Substr(Expression):
    """
    Substring extraction.
    
    Usage:
        Model.objects.annotate(first_char=Substr('name', 1, 1))
    """
    
    def __init__(self, expression: str | Expression, pos: int, length: int | None = None) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
        self.pos = pos
        self.length = length
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        expr = self.expression.resolve(model)
        if self.length is not None:
            return func.substr(expr, self.pos, self.length)
        return func.substr(expr, self.pos)


# Date/Time functions

class Now(Expression):
    """Current timestamp."""
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.now()


class Extract(Expression):
    """
    Extract part of a date/datetime.
    
    Usage:
        Model.objects.annotate(year=Extract('created_at', 'year'))
    """
    
    def __init__(self, expression: str | Expression, lookup_name: str) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
        self.lookup_name = lookup_name
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import extract
        
        return extract(self.lookup_name, self.expression.resolve(model))


class TruncDate(Expression):
    """Truncate datetime to date."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func, cast
        from sqlalchemy.types import Date
        
        return cast(self.expression.resolve(model), Date)


class TruncMonth(Expression):
    """Truncate date to first of month."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        # Database-specific, but most support date_trunc
        return func.date_trunc('month', self.expression.resolve(model))


class TruncYear(Expression):
    """Truncate date to first of year."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.date_trunc('year', self.expression.resolve(model))


# Math functions

class Abs(Expression):
    """Absolute value."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.abs(self.expression.resolve(model))


class Ceil(Expression):
    """Ceiling (round up)."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.ceil(self.expression.resolve(model))


class Floor(Expression):
    """Floor (round down)."""
    
    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.floor(self.expression.resolve(model))


class Round(Expression):
    """
    Round to specified precision.
    
    Usage:
        Model.objects.annotate(rounded_price=Round('price', 2))
    """
    
    def __init__(self, expression: str | Expression, precision: int = 0) -> None:
        super().__init__()
        self.expression = expression if isinstance(expression, Expression) else F(expression)
        self.precision = precision
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        return func.round(self.expression.resolve(model), self.precision)


class Greatest(Expression):
    """Return the greatest value among arguments."""
    
    def __init__(self, *expressions: str | Expression) -> None:
        super().__init__()
        self.expressions = [
            e if isinstance(e, Expression) else F(e) if isinstance(e, str) else Value(e)
            for e in expressions
        ]
    
    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func
        
        resolved = [e.resolve(model) for e in self.expressions]
        return func.greatest(*resolved)


class Least(Expression):
    """Return the least value among arguments."""

    def __init__(self, *expressions: str | Expression) -> None:
        super().__init__()
        self.expressions = [
            e if isinstance(e, Expression) else F(e) if isinstance(e, str) else Value(e)
            for e in expressions
        ]

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func

        resolved = [e.resolve(model) for e in self.expressions]
        return func.least(*resolved)


# Window functions
#
# Requires database support for SQL window functions:
# SQLite >= 3.25, MySQL >= 8.0, any supported PostgreSQL.
# No runtime version check is performed.


def _as_expression(value: str | Expression) -> Expression:
    """Coerce a field name or Expression into an Expression."""
    return value if isinstance(value, Expression) else F(value)


class Window(Expression):
    """
    Wrap an expression in an SQL ``OVER (...)`` window clause.

    ``partition_by`` and ``order_by`` accept a field name, an Expression,
    or a list of either.  Order fields support the ``"-"`` prefix for
    descending order.

    Usage:
        Post.objects.annotate(
            rank=Window(Rank(), partition_by="author_id", order_by="-score")
        )

    Note: window annotations may not be referenced in ``filter()`` /
    ``exclude()`` — SQL forbids window functions in WHERE clauses.

    Requires SQLite >= 3.25 or MySQL >= 8.0 (PostgreSQL: any version).
    """

    def __init__(
        self,
        expression: str | Expression,
        partition_by: Any = None,
        order_by: Any = None,
    ) -> None:
        super().__init__()
        self.expression = _as_expression(expression)
        self.partition_by = self._as_list(partition_by)
        self.order_by = self._as_list(order_by)

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    def _resolve_order_item(self, item: Any, model: Any) -> Any:
        from sqlalchemy import asc, desc

        if isinstance(item, str):
            if item.startswith("-"):
                return desc(F(item[1:]).resolve(model))
            return asc(F(item).resolve(model))
        if isinstance(item, Expression):
            return item.resolve(model)
        return item

    def resolve(self, model: Any) -> Any:
        inner = self.expression.resolve(model)
        over_kwargs: dict[str, Any] = {}
        if self.partition_by:
            over_kwargs["partition_by"] = [
                _as_expression(p).resolve(model) if isinstance(p, (str, Expression)) else p
                for p in self.partition_by
            ]
        if self.order_by:
            over_kwargs["order_by"] = [
                self._resolve_order_item(o, model) for o in self.order_by
            ]
        return inner.over(**over_kwargs)

    def __repr__(self) -> str:
        return (
            f"Window({self.expression!r}, partition_by={self.partition_by!r}, "
            f"order_by={self.order_by!r})"
        )


class _SimpleWindowFunction(Expression):
    """Base for argument-less window functions (ROW_NUMBER, RANK, ...)."""

    function: str = ""

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func

        return getattr(func, self.function)()

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class RowNumber(_SimpleWindowFunction):
    """``ROW_NUMBER()`` window function."""

    function = "row_number"


class Rank(_SimpleWindowFunction):
    """``RANK()`` window function."""

    function = "rank"


class DenseRank(_SimpleWindowFunction):
    """``DENSE_RANK()`` window function."""

    function = "dense_rank"


class PercentRank(_SimpleWindowFunction):
    """``PERCENT_RANK()`` window function."""

    function = "percent_rank"


class CumeDist(_SimpleWindowFunction):
    """``CUME_DIST()`` window function."""

    function = "cume_dist"


class Lag(Expression):
    """``LAG(expr, offset, default)`` window function."""

    function = "lag"

    def __init__(
        self,
        expression: str | Expression,
        offset: int = 1,
        default: Any = None,
    ) -> None:
        super().__init__()
        self.expression = _as_expression(expression)
        self.offset = offset
        self.default = default

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func, literal

        args = [self.expression.resolve(model), self.offset]
        if self.default is not None:
            args.append(literal(self.default))
        return getattr(func, self.function)(*args)


class Lead(Lag):
    """``LEAD(expr, offset, default)`` window function."""

    function = "lead"


class FirstValue(Expression):
    """``FIRST_VALUE(expr)`` window function."""

    function = "first_value"

    def __init__(self, expression: str | Expression) -> None:
        super().__init__()
        self.expression = _as_expression(expression)

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func

        return getattr(func, self.function)(self.expression.resolve(model))


class LastValue(FirstValue):
    """``LAST_VALUE(expr)`` window function."""

    function = "last_value"


class Ntile(Expression):
    """``NTILE(num_buckets)`` window function."""

    def __init__(self, num_buckets: int) -> None:
        super().__init__()
        self.num_buckets = num_buckets

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import func

        return func.ntile(self.num_buckets)


class Cast(Expression):
    """
    ``CAST(expr AS type)`` — convert an expression to another type.

    ``output_field`` is a zeeb_orm Field instance or class, e.g.
    ``Cast("price", fields.IntegerField)`` or
    ``Cast("score", fields.CharField(max_length=20))``.
    """

    def __init__(self, expression: str | Expression, output_field: Any) -> None:
        super().__init__()
        self.expression = _as_expression(expression)
        if isinstance(output_field, type):
            output_field = output_field()
        self.output_field = output_field

    def resolve(self, model: Any) -> Any:
        from sqlalchemy import cast as sa_cast

        return sa_cast(
            self.expression.resolve(model), self.output_field.get_column_type()
        )


# String aggregation


class _StringAggFunction(FunctionElement):  # type: ignore[type-arg]
    """Portable string-aggregation SQL element (see :class:`StringAgg`)."""

    name = "string_agg"
    inherit_cache = False

    def __init__(self, expr: Any, delimiter: str, distinct: bool = False) -> None:
        self.delimiter = delimiter
        self.distinct_agg = distinct
        super().__init__(expr)


def _quoted_delimiter(delimiter: str) -> str:
    """Render the delimiter as an SQL string literal.

    MySQL's ``GROUP_CONCAT ... SEPARATOR`` does not accept bind parameters,
    so the delimiter is inlined (with single quotes escaped) on all backends.
    """
    return "'" + delimiter.replace("'", "''") + "'"


@compiles(_StringAggFunction)
def _compile_string_agg_default(element: Any, compiler: Any, **kw: Any) -> str:
    # SQLite (and other backends): group_concat(expr, 'delimiter').
    # SQLite forbids a second argument with DISTINCT, so the delimiter
    # falls back to the default "," in that case.
    expr = compiler.process(list(element.clauses)[0], **kw)
    if element.distinct_agg:
        return f"group_concat(DISTINCT {expr})"
    return f"group_concat({expr}, {_quoted_delimiter(element.delimiter)})"


@compiles(_StringAggFunction, "postgresql")
def _compile_string_agg_postgresql(element: Any, compiler: Any, **kw: Any) -> str:
    expr = compiler.process(list(element.clauses)[0], **kw)
    distinct = "DISTINCT " if element.distinct_agg else ""
    return f"string_agg({distinct}{expr}, {_quoted_delimiter(element.delimiter)})"


@compiles(_StringAggFunction, "mysql")
def _compile_string_agg_mysql(element: Any, compiler: Any, **kw: Any) -> str:
    expr = compiler.process(list(element.clauses)[0], **kw)
    distinct = "DISTINCT " if element.distinct_agg else ""
    return (
        f"GROUP_CONCAT({distinct}{expr} "
        f"SEPARATOR {_quoted_delimiter(element.delimiter)})"
    )


class StringAgg(Aggregate):
    """
    Concatenate values into a single delimited string (aggregate).

    Compiles to ``string_agg`` on PostgreSQL, ``GROUP_CONCAT`` on MySQL and
    ``group_concat`` on SQLite/other backends.  With ``distinct=True`` on
    SQLite the delimiter falls back to ``","`` (SQLite limitation).

    Usage:
        await Post.objects.aggregate(titles=StringAgg("title", delimiter=", "))
    """

    function = "STRING_AGG"
    allow_distinct = True

    def __init__(
        self,
        expression: str | Expression,
        delimiter: str = ", ",
        *,
        distinct: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(expression, distinct=distinct, **kwargs)
        self.delimiter = delimiter

    def resolve(self, model: Any) -> Any:
        expr = self.expression.resolve(model)
        return _StringAggFunction(expr, self.delimiter, distinct=self.distinct)


# Django parity alias (MySQL naming)
GroupConcat = StringAgg
