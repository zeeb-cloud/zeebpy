"""Tests for Django-style datetime transforms in lookups.

Executes against SQLite and asserts compiled SQL strings for the
PostgreSQL and MySQL dialects.
"""

import datetime

import pytest
from sqlalchemy.dialects import mysql, postgresql

from zeeb_orm import (
    FieldError,
    Model,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# Test Models


class DtAuthor(Model):
    name = fields.CharField(max_length=100)
    joined_at = fields.DateTimeField(null=True)

    class Meta:
        table_name = "dt_authors"


class DtEvent(Model):
    title = fields.CharField(max_length=200)
    author = fields.ForeignKey(DtAuthor, related_name="events")
    created_at = fields.DateTimeField(null=True)

    class Meta:
        table_name = "dt_events"


MODELS = (DtAuthor, DtEvent)


@pytest.fixture
async def db():
    """Set up test database (pattern from tests/test_orm.py)."""
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()

    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")

    for model in MODELS:
        model._get_table()

    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    # Remove our tables from the global metadata so later-collected tests
    # (e.g. migration autodetection) don't see them as pending models.
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
async def seeded(db):
    author = await DtAuthor.objects.create(
        name="Alice", joined_at=datetime.datetime(2023, 12, 31, 8, 0, 0)
    )
    # Wednesday, ISO week 20, Q2
    e1 = await DtEvent.objects.create(
        title="spring",
        author=author,
        created_at=datetime.datetime(2024, 5, 15, 10, 30, 45),
    )
    # Friday 2021-01-01: ISO year 2020, ISO week 53, Q1
    e2 = await DtEvent.objects.create(
        title="newyear",
        author=author,
        created_at=datetime.datetime(2021, 1, 1, 23, 59, 58),
    )
    # Sunday: week_day == 1
    e3 = await DtEvent.objects.create(
        title="sunday",
        author=author,
        created_at=datetime.datetime(2024, 6, 2, 0, 5, 0),
    )
    return {"author": author, "events": [e1, e2, e3]}


async def titles(**filters):
    return {e.title for e in await DtEvent.objects.filter(**filters)}


class TestExtractTransforms:
    """year/month/day/hour/minute/second via EXTRACT/strftime."""

    @pytest.mark.asyncio
    async def test_year_exact(self, seeded):
        assert await titles(created_at__year=2024) == {"spring", "sunday"}

    @pytest.mark.asyncio
    async def test_year_gte(self, seeded):
        assert await titles(created_at__year__gte=2024) == {"spring", "sunday"}
        assert await titles(created_at__year__lt=2024) == {"newyear"}

    @pytest.mark.asyncio
    async def test_year_in(self, seeded):
        assert await titles(created_at__year__in=[2021, 2024]) == {
            "spring",
            "newyear",
            "sunday",
        }

    @pytest.mark.asyncio
    async def test_month(self, seeded):
        assert await titles(created_at__month=5) == {"spring"}
        assert await titles(created_at__month__gte=5) == {"spring", "sunday"}

    @pytest.mark.asyncio
    async def test_day(self, seeded):
        assert await titles(created_at__day=15) == {"spring"}
        assert await titles(created_at__day__lte=2) == {"newyear", "sunday"}

    @pytest.mark.asyncio
    async def test_hour_minute_second(self, seeded):
        assert await titles(created_at__hour=10) == {"spring"}
        assert await titles(created_at__minute=59) == {"newyear"}
        assert await titles(created_at__second=45) == {"spring"}
        assert await titles(created_at__hour__gte=10) == {"spring", "newyear"}


class TestCalendarTransforms:
    """quarter/week/week_day/iso_week_day/iso_year."""

    @pytest.mark.asyncio
    async def test_quarter(self, seeded):
        assert await titles(created_at__quarter=2) == {"spring", "sunday"}
        assert await titles(created_at__quarter=1) == {"newyear"}

    @pytest.mark.asyncio
    async def test_week(self, seeded):
        # 2024-05-15 is ISO week 20; 2021-01-01 is ISO week 53 (of 2020)
        assert await titles(created_at__week=20) == {"spring"}
        assert await titles(created_at__week=53) == {"newyear"}

    @pytest.mark.asyncio
    async def test_week_day_django_semantics(self, seeded):
        # Django: Sunday=1 .. Saturday=7
        assert await titles(created_at__week_day=1) == {"sunday"}  # Sunday
        assert await titles(created_at__week_day=4) == {"spring"}  # Wednesday
        assert await titles(created_at__week_day=6) == {"newyear"}  # Friday

    @pytest.mark.asyncio
    async def test_iso_week_day(self, seeded):
        # ISO: Monday=1 .. Sunday=7
        assert await titles(created_at__iso_week_day=7) == {"sunday"}
        assert await titles(created_at__iso_week_day=3) == {"spring"}
        assert await titles(created_at__iso_week_day=5) == {"newyear"}

    @pytest.mark.asyncio
    async def test_iso_year(self, seeded):
        # 2021-01-01 belongs to ISO year 2020
        assert await titles(created_at__iso_year=2020) == {"newyear"}
        assert await titles(created_at__iso_year=2024) == {"spring", "sunday"}


class TestDateTimeCasts:
    """date / time transforms."""

    @pytest.mark.asyncio
    async def test_date_exact(self, seeded):
        assert await titles(created_at__date=datetime.date(2024, 5, 15)) == {
            "spring"
        }

    @pytest.mark.asyncio
    async def test_date_range(self, seeded):
        assert await titles(
            created_at__date__range=(
                datetime.date(2024, 1, 1),
                datetime.date(2024, 12, 31),
            )
        ) == {"spring", "sunday"}

    @pytest.mark.asyncio
    async def test_date_gte(self, seeded):
        assert await titles(
            created_at__date__gte=datetime.date(2024, 6, 1)
        ) == {"sunday"}

    @pytest.mark.asyncio
    async def test_time_exact(self, seeded):
        assert await titles(created_at__time=datetime.time(10, 30, 45)) == {
            "spring"
        }

    @pytest.mark.asyncio
    async def test_time_gte(self, seeded):
        assert await titles(
            created_at__time__gte=datetime.time(10, 0, 0)
        ) == {"spring", "newyear"}


class TestTransformWithTraversal:
    """Transforms combined with relation traversal."""

    @pytest.mark.asyncio
    async def test_relation_then_transform(self, seeded):
        events = await DtEvent.objects.filter(author__joined_at__year=2023)
        assert len(events) == 3
        events = await DtEvent.objects.filter(author__joined_at__year__gt=2023)
        assert events == []

    @pytest.mark.asyncio
    async def test_reverse_relation_then_transform(self, seeded):
        authors = await DtAuthor.objects.filter(
            events__created_at__year=2021
        ).distinct()
        assert [a.name for a in authors] == ["Alice"]


class TestTransformErrors:
    @pytest.mark.asyncio
    async def test_unknown_transform(self, db):
        with pytest.raises(FieldError):
            DtEvent.objects.filter(created_at__century__gte=20)._build_select()

    @pytest.mark.asyncio
    async def test_transform_with_unknown_lookup(self, db):
        with pytest.raises(FieldError):
            DtEvent.objects.filter(created_at__year__bogus=2024)._build_select()


class TestDialectCompilation:
    """Compile-string assertions for PostgreSQL and MySQL dialects."""

    def _sql(self, qs, dialect):
        stmt = qs._build_select()
        return str(stmt.compile(dialect=dialect))

    def test_year_postgresql(self, db):
        sql = self._sql(
            DtEvent.objects.filter(created_at__year=2024), postgresql.dialect()
        )
        assert "EXTRACT(year FROM dt_events.created_at)" in sql

    def test_year_mysql(self, db):
        sql = self._sql(
            DtEvent.objects.filter(created_at__year=2024), mysql.dialect()
        )
        assert "EXTRACT(year FROM dt_events.created_at)" in sql

    def test_year_sqlite(self, db):
        from sqlalchemy.dialects import sqlite

        sql = self._sql(
            DtEvent.objects.filter(created_at__year=2024), sqlite.dialect()
        )
        assert "STRFTIME('%Y', dt_events.created_at)" in sql

    def test_quarter_dialects(self, db):
        qs = DtEvent.objects.filter(created_at__quarter=2)
        assert "EXTRACT(QUARTER FROM dt_events.created_at)" in self._sql(
            qs, postgresql.dialect()
        )
        assert "QUARTER(dt_events.created_at)" in self._sql(qs, mysql.dialect())
        from sqlalchemy.dialects import sqlite

        assert (
            "(CAST(STRFTIME('%m', dt_events.created_at) AS INTEGER) + 2) / 3"
            in self._sql(qs, sqlite.dialect())
        )

    def test_week_day_dialects(self, db):
        qs = DtEvent.objects.filter(created_at__week_day=1)
        assert "(EXTRACT(DOW FROM dt_events.created_at) + 1)" in self._sql(
            qs, postgresql.dialect()
        )
        assert "DAYOFWEEK(dt_events.created_at)" in self._sql(
            qs, mysql.dialect()
        )

    def test_iso_week_day_dialects(self, db):
        qs = DtEvent.objects.filter(created_at__iso_week_day=1)
        assert "EXTRACT(ISODOW FROM dt_events.created_at)" in self._sql(
            qs, postgresql.dialect()
        )
        assert "(WEEKDAY(dt_events.created_at) + 1)" in self._sql(
            qs, mysql.dialect()
        )

    def test_week_dialects(self, db):
        qs = DtEvent.objects.filter(created_at__week=20)
        assert "EXTRACT(WEEK FROM dt_events.created_at)" in self._sql(
            qs, postgresql.dialect()
        )
        assert "WEEK(dt_events.created_at, 3)" in self._sql(qs, mysql.dialect())

    def test_iso_year_dialects(self, db):
        qs = DtEvent.objects.filter(created_at__iso_year=2024)
        assert "EXTRACT(ISOYEAR FROM dt_events.created_at)" in self._sql(
            qs, postgresql.dialect()
        )
        assert "(YEARWEEK(dt_events.created_at, 3) DIV 100)" in self._sql(
            qs, mysql.dialect()
        )

    def test_date_dialects(self, db):
        qs = DtEvent.objects.filter(created_at__date=datetime.date(2024, 5, 15))
        assert "CAST(dt_events.created_at AS DATE)" in self._sql(
            qs, postgresql.dialect()
        )
        assert "DATE(dt_events.created_at)" in self._sql(qs, mysql.dialect())
        from sqlalchemy.dialects import sqlite

        assert "DATE(dt_events.created_at)" in self._sql(qs, sqlite.dialect())

    def test_time_dialects(self, db):
        qs = DtEvent.objects.filter(created_at__time=datetime.time(10, 30))
        assert "CAST(dt_events.created_at AS TIME)" in self._sql(
            qs, postgresql.dialect()
        )
        assert "TIME(dt_events.created_at)" in self._sql(qs, mysql.dialect())
        from sqlalchemy.dialects import sqlite

        assert "STRFTIME('%H:%M:%S', dt_events.created_at)" in self._sql(
            qs, sqlite.dialect()
        )
