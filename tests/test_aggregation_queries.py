"""Aggregation queries compile to SQL every backend accepts, with Django's results.

Each class pins one defect that SQLite hid: it accepts a GROUP BY query whose
SELECT list names columns that are neither grouped nor aggregated and answers
with an arbitrary row per group, where PostgreSQL refuses the query. The
real-database tests run on the harness default (SQLite) or on
``ZEEB_TEST_DATABASE_URL``; the ``requires_postgres`` tests prove PostgreSQL
accepts the shapes.
"""

import datetime

import pytest

from zeeb_orm import Model, fields
from zeeb_orm.query.expressions import (
    Case,
    Coalesce,
    Count,
    F,
    Rank,
    Subquery,
    Sum,
    Value,
    When,
    Window,
)
from zeeb_orm.testing import requires_postgres, temporary_database


class AqAuthor(Model):
    id = fields.AutoField(primary_key=True)
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "aq_authors"


class AqEvent(Model):
    id = fields.AutoField(primary_key=True)
    tool = fields.CharField(max_length=50)
    cost = fields.IntegerField(default=0)
    author = fields.ForeignKey(AqAuthor, related_name="events", null=True)
    created_at = fields.DateTimeField()

    class Meta:
        table_name = "aq_events"
        ordering = ["-created_at"]


def by_tool(rows, key):
    return {row["tool"]: row[key] for row in rows}


@pytest.fixture
async def events():
    async with temporary_database(AqAuthor, AqEvent):
        amy = await AqAuthor.objects.create(name="Amy")
        zed = await AqAuthor.objects.create(name="Zed")
        start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        rows = [("x", 1, amy), ("x", 2, amy), ("y", 4, zed), ("z", 8, None)]
        for hour, (tool, cost, author) in enumerate(rows):
            await AqEvent.objects.create(
                tool=tool,
                cost=cost,
                author=author,
                created_at=start + datetime.timedelta(hours=hour),
            )
        yield {"amy": amy, "zed": zed}


class TestWrappedAggregates:
    """An aggregate inside another expression aggregates too.

    Only a bare ``Sum``/``Count`` used to be recognised, so
    ``values("tool").annotate(s=Coalesce(Sum("cost"), 0))`` compiled without a
    GROUP BY: SQLite collapsed every tool into a single row, PostgreSQL
    refused it, and a filter on the annotation went to WHERE ("misuse of
    aggregate function").
    """

    def test_the_tree_is_walked(self):
        assert Coalesce(Sum("cost"), Value(0)).contains_aggregate
        assert (Sum("cost") + 1).contains_aggregate
        assert (2 * Count("id")).contains_aggregate
        assert Case(When(tool="x", then=Sum("cost")), default=Value(0)).contains_aggregate
        assert not (F("cost") + 1).contains_aggregate
        assert not Window(Sum("cost")).contains_aggregate
        assert Window(Rank(), order_by="-cost").contains_over_clause
        assert not Subquery(AqEvent.objects.values("cost")).contains_aggregate

    def test_coalesce_over_an_aggregate_groups(self):
        qs = AqEvent.objects.values("tool").annotate(s=Coalesce(Sum("cost"), Value(0)))
        assert "GROUP BY" in str(qs._build_select())

    async def test_coalesce_over_an_aggregate(self, events):
        rows = await AqEvent.objects.values("tool").annotate(s=Coalesce(Sum("cost"), Value(0)))
        assert by_tool(rows, "s") == {"x": 3, "y": 4, "z": 8}

    async def test_arithmetic_on_an_aggregate(self, events):
        rows = await AqEvent.objects.values("tool").annotate(s=Sum("cost") + 1)
        assert by_tool(rows, "s") == {"x": 4, "y": 5, "z": 9}

    async def test_case_over_an_aggregate(self, events):
        rows = await AqEvent.objects.values("tool").annotate(
            s=Case(When(tool="x", then=Sum("cost")), default=Value(0))
        )
        assert by_tool(rows, "s") == {"x": 3, "y": 0, "z": 0}

    async def test_filtering_a_wrapped_aggregate_goes_to_having(self, events):
        qs = (
            AqEvent.objects.values("tool")
            .annotate(s=Coalesce(Sum("cost"), Value(0)))
            .filter(s__gt=3)
        )
        assert "HAVING" in str(qs._build_select())
        assert by_tool(await qs, "s") == {"y": 4, "z": 8}

    async def test_a_window_over_an_aggregate_does_not_group(self, events):
        rows = await AqEvent.objects.annotate(running=Window(Sum("cost"), order_by="id"))
        assert sorted(r.running for r in rows) == [1, 3, 7, 15]


@requires_postgres()
async def test_postgres_accepts_wrapped_aggregates(events):
    tools = AqEvent.objects.values("tool")
    assert by_tool(await tools.annotate(s=Coalesce(Sum("cost"), Value(0))), "s")["x"] == 3
    assert by_tool(await tools.annotate(s=Sum("cost") * 2), "s")["x"] == 6
    case = Case(When(tool="x", then=Sum("cost")), default=Value(0))
    assert by_tool(await tools.annotate(s=case), "s") == {"x": 3, "y": 0, "z": 0}
    assert len(await tools.annotate(s=Coalesce(Sum("cost"), Value(0))).filter(s__gt=3)) == 2
