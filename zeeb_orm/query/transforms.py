"""Django-style date/time transforms for field lookups.

Supports lookups like ``created_at__year``, ``created_at__year__gte=2024``,
``created_at__date__range=(d1, d2)`` or ``created_at__week_day=1``.

All transforms are resolved at SQL-compile time (no DB handle needed when
building the statement) via :class:`~sqlalchemy.sql.functions.FunctionElement`
subclasses with per-dialect ``@compiles`` implementations for SQLite,
PostgreSQL and MySQL plus a generic default.

Django semantics are preserved:

- ``week_day``: Sunday=1 .. Saturday=7
- ``iso_week_day``: Monday=1 .. Sunday=7
- ``week``: ISO-8601 week number (1-53)
- ``iso_year``: ISO-8601 week-numbering year
- ``quarter``: 1-4
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import Date, Integer, Time, extract
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

#: Transform names usable between a field name and an optional lookup,
#: e.g. ``created_at__year__gte``.
DATETIME_TRANSFORMS = {
    "year",
    "iso_year",
    "month",
    "day",
    "week",
    "week_day",
    "iso_week_day",
    "quarter",
    "hour",
    "minute",
    "second",
    "date",
    "time",
}

# Transforms handled directly by sqlalchemy.extract() (each supported by
# SQLAlchemy's SQLite strftime-based EXTRACT emulation as well).
_EXTRACT_TRANSFORMS = {"year", "month", "day", "hour", "minute", "second"}


class _TransformTime(Time):
    """TIME comparison type whose SQLite binds match ``strftime('%H:%M:%S')``.

    SQLAlchemy's stock SQLite Time type renders microseconds, which would
    never compare equal to the second-precision strings produced by the
    SQLite ``time`` transform SQL.
    """

    def dialect_impl(self, dialect: Any) -> Any:
        # Prevent dialect adaptation (e.g. to sqlite.TIME), which would
        # replace our bind_processor below.
        return self

    def _gen_dialect_impl(self, dialect: Any) -> Any:
        # Internal hook used by _cached_bind_processor; same purpose as
        # dialect_impl above.
        return self

    def bind_processor(self, dialect: Any) -> Any:
        if dialect.name == "sqlite":
            def process(value: Any) -> Any:
                if value is None or isinstance(value, str):
                    return value
                if isinstance(value, datetime.time):
                    return value.strftime("%H:%M:%S")
                return value

            return process
        return super().bind_processor(dialect)


class _DatetimeTransform(FunctionElement):  # type: ignore[type-arg]
    """Base for custom-compiled datetime transforms (integer result)."""

    inherit_cache = True
    type = Integer()


class _Quarter(_DatetimeTransform):
    name = "zeeb_quarter"
    inherit_cache = True


class _Week(_DatetimeTransform):
    name = "zeeb_week"
    inherit_cache = True


class _WeekDay(_DatetimeTransform):
    name = "zeeb_week_day"
    inherit_cache = True


class _IsoWeekDay(_DatetimeTransform):
    name = "zeeb_iso_week_day"
    inherit_cache = True


class _IsoYear(_DatetimeTransform):
    name = "zeeb_iso_year"
    inherit_cache = True


class _DateTransform(FunctionElement):  # type: ignore[type-arg]
    name = "zeeb_date"
    inherit_cache = True
    type = Date()


class _TimeTransform(FunctionElement):  # type: ignore[type-arg]
    name = "zeeb_time"
    inherit_cache = True
    type = _TransformTime()


def _arg(element: FunctionElement, compiler: Any, **kw: Any) -> str:  # type: ignore[type-arg]
    """Compile the single column argument of a transform element."""
    return compiler.process(list(element.clauses)[0], **kw)


# --- quarter -----------------------------------------------------------------


@compiles(_Quarter)
def _quarter_default(element: Any, compiler: Any, **kw: Any) -> str:
    return f"EXTRACT(QUARTER FROM {_arg(element, compiler, **kw)})"


@compiles(_Quarter, "sqlite")
def _quarter_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    col = _arg(element, compiler, **kw)
    return f"(CAST(STRFTIME('%m', {col}) AS INTEGER) + 2) / 3"


@compiles(_Quarter, "mysql")
def _quarter_mysql(element: Any, compiler: Any, **kw: Any) -> str:
    return f"QUARTER({_arg(element, compiler, **kw)})"


# --- week (ISO week number) --------------------------------------------------


@compiles(_Week)
def _week_default(element: Any, compiler: Any, **kw: Any) -> str:
    return f"EXTRACT(WEEK FROM {_arg(element, compiler, **kw)})"


@compiles(_Week, "sqlite")
def _week_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    # ISO week: shift to the Thursday of the current ISO week, then
    # day-of-year // 7 + 1 (same formula Django uses for SQLite).
    col = _arg(element, compiler, **kw)
    return (
        f"(CAST(STRFTIME('%j', DATE({col}, '-3 days', 'weekday 4')) AS INTEGER)"
        " - 1) / 7 + 1"
    )


@compiles(_Week, "mysql")
def _week_mysql(element: Any, compiler: Any, **kw: Any) -> str:
    # Mode 3: Monday-first, ISO-8601 week 1-53.
    return f"WEEK({_arg(element, compiler, **kw)}, 3)"


# --- week_day (Django: Sunday=1 .. Saturday=7) -------------------------------


@compiles(_WeekDay)
def _week_day_default(element: Any, compiler: Any, **kw: Any) -> str:
    return f"(EXTRACT(DOW FROM {_arg(element, compiler, **kw)}) + 1)"


@compiles(_WeekDay, "sqlite")
def _week_day_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    # strftime('%w'): Sunday=0 .. Saturday=6
    col = _arg(element, compiler, **kw)
    return f"(CAST(STRFTIME('%w', {col}) AS INTEGER) + 1)"


@compiles(_WeekDay, "mysql")
def _week_day_mysql(element: Any, compiler: Any, **kw: Any) -> str:
    # DAYOFWEEK(): Sunday=1 .. Saturday=7 (matches Django semantics).
    return f"DAYOFWEEK({_arg(element, compiler, **kw)})"


# --- iso_week_day (Monday=1 .. Sunday=7) --------------------------------------


@compiles(_IsoWeekDay)
def _iso_week_day_default(element: Any, compiler: Any, **kw: Any) -> str:
    return f"EXTRACT(ISODOW FROM {_arg(element, compiler, **kw)})"


@compiles(_IsoWeekDay, "sqlite")
def _iso_week_day_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    # Map Sunday=0..Saturday=6 onto Monday=1..Sunday=7.
    col = _arg(element, compiler, **kw)
    return f"((CAST(STRFTIME('%w', {col}) AS INTEGER) + 6) % 7 + 1)"


@compiles(_IsoWeekDay, "mysql")
def _iso_week_day_mysql(element: Any, compiler: Any, **kw: Any) -> str:
    # WEEKDAY(): Monday=0 .. Sunday=6
    return f"(WEEKDAY({_arg(element, compiler, **kw)}) + 1)"


# --- iso_year (ISO week-numbering year) ---------------------------------------


@compiles(_IsoYear)
def _iso_year_default(element: Any, compiler: Any, **kw: Any) -> str:
    return f"EXTRACT(ISOYEAR FROM {_arg(element, compiler, **kw)})"


@compiles(_IsoYear, "sqlite")
def _iso_year_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    # Year of the Thursday of the current ISO week.
    col = _arg(element, compiler, **kw)
    return f"CAST(STRFTIME('%Y', DATE({col}, '-3 days', 'weekday 4')) AS INTEGER)"


@compiles(_IsoYear, "mysql")
def _iso_year_mysql(element: Any, compiler: Any, **kw: Any) -> str:
    # YEARWEEK(col, 3) returns e.g. 202401; the year part is the ISO year.
    return f"(YEARWEEK({_arg(element, compiler, **kw)}, 3) DIV 100)"


# --- date / time casts ---------------------------------------------------------


@compiles(_DateTransform)
def _date_default(element: Any, compiler: Any, **kw: Any) -> str:
    return f"CAST({_arg(element, compiler, **kw)} AS DATE)"


@compiles(_DateTransform, "sqlite")
def _date_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    # SQLite CAST(x AS DATE) applies numeric affinity; use the date()
    # function which yields 'YYYY-MM-DD' (matching DATE binds).
    return f"DATE({_arg(element, compiler, **kw)})"


@compiles(_DateTransform, "mysql")
def _date_mysql(element: Any, compiler: Any, **kw: Any) -> str:
    return f"DATE({_arg(element, compiler, **kw)})"


@compiles(_TimeTransform)
def _time_default(element: Any, compiler: Any, **kw: Any) -> str:
    return f"CAST({_arg(element, compiler, **kw)} AS TIME)"


@compiles(_TimeTransform, "sqlite")
def _time_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    return f"STRFTIME('%H:%M:%S', {_arg(element, compiler, **kw)})"


@compiles(_TimeTransform, "mysql")
def _time_mysql(element: Any, compiler: Any, **kw: Any) -> str:
    return f"TIME({_arg(element, compiler, **kw)})"


_CUSTOM_TRANSFORMS = {
    "quarter": _Quarter,
    "week": _Week,
    "week_day": _WeekDay,
    "iso_week_day": _IsoWeekDay,
    "iso_year": _IsoYear,
    "date": _DateTransform,
    "time": _TimeTransform,
}


def apply_transform(column: Any, transform: str) -> Any:
    """Apply a datetime ``transform`` to a SQLAlchemy column expression.

    Returns a new SQL expression; raises :class:`~zeeb_orm.exceptions.FieldError`
    for unknown transform names.
    """
    if transform in _EXTRACT_TRANSFORMS:
        return extract(transform, column)
    element_cls = _CUSTOM_TRANSFORMS.get(transform)
    if element_cls is not None:
        return element_cls(column)

    from zeeb_orm.exceptions import FieldError

    raise FieldError(
        f"Unknown datetime transform {transform!r}. "
        f"Choices are: {', '.join(sorted(DATETIME_TRANSFORMS))}"
    )


__all__ = ["DATETIME_TRANSFORMS", "apply_transform"]
