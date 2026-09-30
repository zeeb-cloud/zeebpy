"""TruncMonth / TruncYear / TruncDate compile per dialect.

TruncMonth and TruncYear rendered PostgreSQL's ``date_trunc`` everywhere, so
they failed on SQLite and MySQL; TruncDate used ``CAST(x AS DATE)``, which
SQLite evaluates with numeric affinity (``2024``).
"""

import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mysql, postgresql, sqlite

from zeeb_orm import Model, close_all_connections, configure, fields, setup_database
from zeeb_orm.query import TruncDate, TruncMonth, TruncYear


class TrEvent(Model):
    name = fields.CharField(max_length=20)
    at = fields.DateTimeField(null=True)
    day = fields.DateField(null=True)

    class Meta:
        table_name = "tr_events"


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()
    TrEvent._sa_table = None
    TrEvent._sa_model = None
    metadata.clear()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    TrEvent._get_table()
    await database.create_all()
    for name, at in (
        ("a", datetime.datetime(2024, 5, 15, 10, 30, 5, 123)),
        ("b", datetime.datetime(2024, 5, 2, 23, 59)),
        ("c", datetime.datetime(2023, 12, 31, 8, 0)),
    ):
        await TrEvent.objects.create(name=name, at=at, day=at.date())
    yield database
    await database.drop_all()
    await close_all_connections()
    table = metadata.tables.get("tr_events")
    if table is not None:
        metadata.remove(table)
    TrEvent._sa_table = None
    TrEvent._sa_model = None
    Settings.reset()


async def test_trunc_month_of_a_datetime(db):
    rows = await TrEvent.objects.annotate(m=TruncMonth("at")).order_by("name")
    assert [r.m for r in rows] == [
        datetime.datetime(2024, 5, 1),
        datetime.datetime(2024, 5, 1),
        datetime.datetime(2023, 12, 1),
    ]


async def test_trunc_year_and_month_of_a_date(db):
    rows = await TrEvent.objects.annotate(y=TruncYear("day"), m=TruncMonth("day")).order_by("name")
    assert [(r.y, r.m) for r in rows] == [
        (datetime.date(2024, 1, 1), datetime.date(2024, 5, 1)),
        (datetime.date(2024, 1, 1), datetime.date(2024, 5, 1)),
        (datetime.date(2023, 1, 1), datetime.date(2023, 12, 1)),
    ]


async def test_distinct_months(db):
    months = await TrEvent.objects.annotate(m=TruncMonth("at")).values_list("m", flat=True)
    assert sorted(set(months)) == [datetime.datetime(2023, 12, 1), datetime.datetime(2024, 5, 1)]


async def test_filter_on_a_truncated_value(db):
    rows = await TrEvent.objects.annotate(m=TruncMonth("at")).filter(
        m=datetime.datetime(2024, 5, 1)
    )
    assert sorted(r.name for r in rows) == ["a", "b"]


async def test_trunc_date(db):
    rows = await TrEvent.objects.annotate(d=TruncDate("at")).order_by("name")
    assert [r.d for r in rows] == [
        datetime.date(2024, 5, 15),
        datetime.date(2024, 5, 2),
        datetime.date(2023, 12, 31),
    ]


@pytest.mark.parametrize(
    "dialect, datetime_sql, date_sql",
    [
        (
            postgresql.dialect(),
            "DATE_TRUNC('month', tr_events.at)",
            "CAST(DATE_TRUNC('year', tr_events.day) AS DATE)",
        ),
        (
            mysql.dialect(),
            "CAST(DATE_FORMAT(tr_events.at, '%%Y-%%m-01 00:00:00') AS DATETIME)",
            "CAST(DATE_FORMAT(tr_events.day, '%%Y-01-01') AS DATE)",
        ),
        (
            sqlite.dialect(),
            "STRFTIME('%Y-%m-01 00:00:00.000000', tr_events.at)",
            "STRFTIME('%Y-01-01', tr_events.day)",
        ),
    ],
    ids=["postgresql", "mysql", "sqlite"],
)
def test_compiles_per_dialect(dialect, datetime_sql, date_sql):
    TrEvent._get_table()
    sql = str(
        select(
            TruncMonth("at").resolve(TrEvent), TruncYear("day").resolve(TrEvent)
        ).compile(dialect=dialect)
    )
    assert datetime_sql in sql
    assert date_sql in sql
    assert "date_trunc" not in sql or dialect.name == "postgresql"
