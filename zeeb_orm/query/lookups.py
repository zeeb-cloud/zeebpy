"""Lookup compilation: ``Q`` objects and ``field__lookup`` strings to SQL conditions.

Split out of :mod:`zeeb_orm.query.queryset` (which re-exports every name here):
turning ``author__name__icontains="x"`` into a SQLAlchemy condition — path
resolution through joins, value coercion to the column's type, ``LIKE``
escaping, multi-valued relations — is independent of building and running a
queryset, and is shared by expressions (``Case``/``When``, ``FilteredRelation``)
and the many-to-many managers.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
)

from sqlalchemy import (
    and_,
    func,
    not_,
    or_,
    select,
)

from zeeb_orm.query.q import Q, QOperator, parse_path

if TYPE_CHECKING:
    from zeeb_orm.models.base import Model
    from zeeb_orm.query.joins import JoinContext

ModelT = TypeVar("ModelT", bound="Model")


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


_TRUE_STRINGS = frozenset({"true", "1", "yes"})
_FALSE_STRINGS = frozenset({"false", "0", "no"})

#: How a fractional bound becomes an integer without changing which integers
#: it admits: ``views > 3.5`` is ``views > 3``, ``views >= 3.5`` is
#: ``views >= 4``. (asyncpg would truncate 3.5 to 3, matching wrong rows.)
_INTEGER_ROUNDING = {
    "gt": "ROUND_FLOOR",
    "lte": "ROUND_FLOOR",
    "gte": "ROUND_CEILING",
    "lt": "ROUND_CEILING",
}

#: No integer column holds more than 64 bits; a larger value is refused before
#: ``int()`` turns a short ``"1e999999999"`` into a billion-digit integer.
_INTEGER_LIMIT = 2**63


def _coerce_typed_value(column: Any, lookup: str, value: Any, lookup_string: str) -> Any:
    """Convert a comparison value to the Python type of ``column``.

    Drivers disagree on a mistyped bind: SQLite compares ``'3'`` with an
    integer column, asyncpg refuses it at execute time, and SQLAlchemy's
    ``Uuid`` bind processor rejects a UUID *string* on SQLite. Filter values
    from a client (``POST /query``, query-string filters) are JSON or text, so
    strings are converted to the column's type here — the same query works on
    every database — and a value that cannot be converted raises
    :class:`~zeeb_orm.exceptions.ValidationError` (a 400 in a view) instead of
    a driver error. Values that are already of a compatible type, expressions
    and unknown types pass through unchanged.

    Against an integer column a non-integral number is never sent as is
    (asyncpg truncates it): a bound of ``gt``/``gte``/``lt``/``lte``/``range``
    is rounded to the integer that admits the same rows (see
    :data:`_INTEGER_ROUNDING`), and an ``exact``/``in`` value, NaN or infinity
    is refused.
    """
    import decimal
    import uuid

    from sqlalchemy import Boolean, Float, Integer, Numeric, String, Uuid
    from sqlalchemy.sql.elements import ClauseElement

    from zeeb_orm.exceptions import ValidationError

    if lookup not in _TEMPORAL_COERCE_LOOKUPS:
        return value

    col_type = getattr(column, "type", None)

    def _invalid(v: Any, kind: str) -> ValidationError:
        return ValidationError({lookup_string: [f"{v!r} is not a valid {kind}."]})

    def _integer(v: Any, rounding: str | None) -> Any:
        if isinstance(v, int) or not isinstance(v, (str, float, decimal.Decimal)):
            return v
        try:
            d = decimal.Decimal(v.strip() if isinstance(v, str) else v)
            # abs() applies the context: a huge exponent overflows here
            out_of_range = not d.is_finite() or abs(d) >= _INTEGER_LIMIT
        except decimal.DecimalException:
            raise _invalid(v, "integer") from None
        if out_of_range:
            raise _invalid(v, "integer")
        if d == d.to_integral_value():
            return int(d)
        if rounding is None:
            raise _invalid(v, "integer")
        return int(d.to_integral_value(rounding=rounding))

    def _convert(v: Any, rounding: str | None = None) -> Any:
        if v is None or isinstance(v, ClauseElement):
            return v
        if isinstance(col_type, Boolean):
            if isinstance(v, bool):
                return v
            if isinstance(v, int) and v in (0, 1):
                return bool(v)
            if isinstance(v, str) and v.strip().lower() in _TRUE_STRINGS | _FALSE_STRINGS:
                return v.strip().lower() in _TRUE_STRINGS
            raise _invalid(v, "boolean")
        if isinstance(col_type, Integer):
            return _integer(v, rounding)
        # Float is not a Numeric subclass from SQLAlchemy 2.1 on
        if isinstance(col_type, (Numeric, Float)):
            if not isinstance(v, str):
                return v
            try:
                if isinstance(col_type, Float) or not getattr(col_type, "asdecimal", True):
                    return float(v.strip())
                return decimal.Decimal(v.strip())
            except (ValueError, decimal.InvalidOperation):
                raise _invalid(v, "number") from None
        if isinstance(col_type, Uuid):
            if isinstance(v, str):
                try:
                    return uuid.UUID(v.strip())
                except ValueError:
                    raise _invalid(v, "UUID") from None
            if isinstance(v, (int, float, bool)):
                raise _invalid(v, "UUID")
            return v
        if isinstance(col_type, String):
            if isinstance(v, bool):
                raise _invalid(v, "string")
            if isinstance(v, (int, float, decimal.Decimal, uuid.UUID)):
                return str(v)
            return v
        return v

    if lookup == "range" and isinstance(value, (list, tuple)) and len(value) == 2:
        # Narrow inwards: range(1.5, 9.5) on integers is range(2, 9)
        return [_convert(value[0], "ROUND_CEILING"), _convert(value[1], "ROUND_FLOOR")]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_convert(v) for v in value]
    return _convert(value, _INTEGER_ROUNDING.get(lookup))


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

    # Coerce ISO strings against temporal columns (asyncpg rejects str binds).
    # Only strings are parsed and fromisoformat raises ValueError alone, so a
    # TypeError here is a bug, not bad input — let it surface.
    try:
        value = _coerce_temporal_value(column, transform, lookup, value)
    except ValueError:
        from zeeb_orm.exceptions import ValidationError

        raise ValidationError(
            {lookup_string: [f"{value!r} is not a valid date, time or datetime."]}
        ) from None
    # ...and every other value against its column's type (see the helper)
    value = _coerce_typed_value(column, lookup, value, lookup_string)

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
