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
    OuterRef,
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


def per_event(rows):
    return sorted((r.tool, r.cost, r.n) for r in rows)


class TestGroupByCoversTheSelectList:
    """Everything an aggregation selects outside an aggregate is grouped.

    Whole rows were grouped by the primary key alone, so ``select_related()``
    columns (and a non-aggregate annotation reading a joined table) were
    neither grouped nor aggregated; in a ``values()`` aggregation a
    non-aggregate annotation was left out of the GROUP BY the same way.
    PostgreSQL refused all of them.
    """

    def test_select_related_groups_by_each_joined_primary_key(self):
        qs = AqEvent.objects.select_related("author").annotate(n=Count("author__events"))
        group_by = str(qs._build_select()).split("GROUP BY")[1]
        assert "aq_events.id" in group_by
        assert "_sr_author" in group_by and ".id" in group_by.split("aq_events.id")[1]

    def test_constants_stay_out_of_the_group_by(self):
        qs = AqEvent.objects.values("tool").annotate(one=Value(1), n=Count("id"))
        assert str(qs._build_select()).split("GROUP BY")[1].strip() == "aq_events.tool"

    async def test_select_related_with_an_aggregate(self, events):
        rows = await AqEvent.objects.select_related("author").annotate(n=Count("author__events"))
        assert per_event(rows) == [("x", 1, 2), ("x", 2, 2), ("y", 4, 1), ("z", 8, 0)]
        names = {r.cost: getattr(r, "_cache_author", None) for r in rows}
        assert names[1].name == "Amy" and names[4].name == "Zed"

    async def test_a_joined_annotation_beside_an_aggregate(self, events):
        rows = await AqEvent.objects.annotate(
            author_name=F("author__name"), n=Count("author__events")
        )
        assert sorted((r.cost, r.author_name, r.n) for r in rows) == [
            (1, "Amy", 2),
            (2, "Amy", 2),
            (4, "Zed", 1),
            (8, None, 0),
        ]

    async def test_a_non_aggregate_annotation_is_a_grouping_key(self, events):
        """As in SQL: the output row is (tool, double, n), so all three group."""
        rows = await AqEvent.objects.values("tool").annotate(double=F("cost") * 2, n=Count("id"))
        assert sorted((r["tool"], r["double"], r["n"]) for r in rows) == [
            ("x", 2, 1),
            ("x", 4, 1),
            ("y", 8, 1),
            ("z", 16, 1),
        ]

    async def test_a_constant_annotation_beside_an_aggregate(self, events):
        rows = await AqEvent.objects.values("tool").annotate(one=Value(1), n=Count("id"))
        assert sorted((r["tool"], r["one"], r["n"]) for r in rows) == [
            ("x", 1, 2),
            ("y", 1, 1),
            ("z", 1, 1),
        ]


@requires_postgres()
async def test_postgres_accepts_grouped_selections(events):
    rows = await AqEvent.objects.select_related("author").annotate(n=Count("author__events"))
    assert per_event(rows) == [("x", 1, 2), ("x", 2, 2), ("y", 4, 1), ("z", 8, 0)]
    rows = await AqEvent.objects.annotate(author_name=F("author__name"), n=Count("author__events"))
    assert len(rows) == 4
    assert len(await AqEvent.objects.values("tool").annotate(d=F("cost") * 2, n=Count("id"))) == 4
    assert len(await AqEvent.objects.values("tool").annotate(one=Value(1), n=Count("id"))) == 3
    flag = Case(When(tool="x", then=Value(1)), default=Value(0))
    rows = await AqEvent.objects.values("author_id").annotate(f=flag, n=Count("id"))
    assert sorted((r["f"], r["n"]) for r in rows) == [(0, 1), (0, 1), (1, 2)]


def author_totals():
    """Each author's total cost, from a grouped subquery selecting an annotation."""
    per_author = (
        AqEvent.objects.filter(author_id=OuterRef("id"))
        .values("author")
        .annotate(total=Sum("cost"))
        .values("total")
    )
    return AqAuthor.objects.annotate(total=Subquery(per_author)).order_by("name")


class TestValuesListAnnotations:
    """values_list() tuples carry the annotations added after it.

    They used to hold only the named fields: ``values_list("tool").annotate(
    n=Count("id"))`` yielded ``("x",)`` and the count was lost.
    """

    async def test_annotations_follow_the_fields(self, events):
        rows = await AqEvent.objects.values_list("tool").annotate(n=Count("id"))
        assert sorted(rows) == [("x", 2), ("y", 1), ("z", 1)]

    async def test_several_fields_then_annotations_in_order(self, events):
        rows = await AqEvent.objects.values_list("tool", "author__name").annotate(
            n=Count("id"), spend=Sum("cost")
        )
        assert sorted(rows, key=lambda r: r[0]) == [
            ("x", "Amy", 2, 3),
            ("y", "Zed", 1, 4),
            ("z", None, 1, 8),
        ]

    async def test_a_named_annotation_keeps_its_position(self, events):
        rows = await AqAuthor.objects.annotate(n=Count("events")).values_list("n", "name")
        assert sorted(rows) == [(1, "Zed"), (2, "Amy")]

    async def test_an_earlier_unnamed_annotation_is_not_returned(self, events):
        rows = await AqAuthor.objects.annotate(n=Count("events")).values_list("name")
        assert sorted(rows) == [("Amy",), ("Zed",)]

    async def test_flat_yields_the_first_value(self, events):
        rows = await AqEvent.objects.values_list("tool", flat=True).annotate(n=Count("id"))
        assert sorted(rows) == ["x", "y", "z"]


class TestValuesAfterAnnotate:
    """The grouping is fixed when the aggregate is annotated.

    ``values()`` after ``annotate()`` changes what is returned, not what is
    grouped. The GROUP BY used to be recomputed from the last ``values()``:
    ``values("tool").annotate(n=...).values("n")`` lost the grouping
    altogether (one row per event, every n = 1), and an annotation made
    before ``values()`` was regrouped by the named fields instead of per
    object.
    """

    async def test_a_second_values_keeps_the_grouping(self, events):
        rows = await AqEvent.objects.values("tool").annotate(n=Count("id")).values("n")
        assert sorted(r["n"] for r in rows) == [1, 1, 2]
        rows = await (
            AqEvent.objects.values("tool").annotate(n=Count("id")).values_list("n", flat=True)
        )
        assert sorted(rows) == [1, 1, 2]

    async def test_values_after_a_whole_row_aggregate_stays_per_object(self, events):
        rows = await AqEvent.objects.annotate(n=Count("id")).values("tool", "n")
        assert sorted((r["tool"], r["n"]) for r in rows) == [
            ("x", 1),
            ("x", 1),
            ("y", 1),
            ("z", 1),
        ]

    async def test_an_earlier_unnamed_annotation_is_not_returned(self, events):
        rows = await AqEvent.objects.annotate(double=F("cost") * 2).values("tool")
        assert all(set(row) == {"tool"} for row in rows)
        rows = await AqEvent.objects.annotate(double=F("cost") * 2).values("tool", "double")
        assert sorted((r["tool"], r["double"]) for r in rows)[0] == ("x", 2)

    async def test_values_naming_only_a_constant_annotation_reads_every_row(self, events):
        rows = await AqEvent.objects.annotate(one=Value(1)).values("one")
        assert rows == [{"one": 1}] * 4
        assert await AqEvent.objects.annotate(one=Value(1)).values_list("one", flat=True) == [1] * 4

    async def test_the_last_of_values_and_values_list_wins(self, events):
        costs = await AqEvent.objects.values("tool").values_list("cost", flat=True)
        assert sorted(costs) == [1, 2, 4, 8]
        rows = await AqEvent.objects.values_list("cost").values("tool")
        assert sorted(r["tool"] for r in rows) == ["x", "x", "y", "z"]

    async def test_a_subquery_can_select_an_annotation(self, events):
        rows = await author_totals()
        assert [(a.name, a.total) for a in rows] == [("Amy", 3), ("Zed", 4)]


@requires_postgres()
async def test_postgres_accepts_values_after_annotate(events):
    rows = await AqEvent.objects.values("tool").annotate(n=Count("id")).values("n")
    assert sorted(r["n"] for r in rows) == [1, 1, 2]
    rows = await AqEvent.objects.values_list("tool").annotate(n=Count("id"))
    assert sorted(rows) == [("x", 2), ("y", 1), ("z", 1)]
    rows = await AqEvent.objects.annotate(n=Count("id")).values("tool", "n")
    assert len(rows) == 4
    assert [(a.name, a.total) for a in await author_totals()] == [("Amy", 3), ("Zed", 4)]
