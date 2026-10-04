"""Meta.ordering stays out of an aggregation's GROUP BY query.

``values("tool").annotate(n=Count("id"))`` on a model ordered by
``-created_at`` used to compile to ``... GROUP BY tool ORDER BY created_at
DESC``. PostgreSQL refuses that ("column must appear in the GROUP BY clause or
be used in an aggregate function"); SQLite accepts it and sorts the groups by
an arbitrary row's value, so the bug only showed up in production. Django
leaves a model's default ordering out of any GROUP BY query, and so does
zeebpy now; an explicit ``order_by()`` still applies.

The SQL-shape tests need no database. ``TestAgainstARealDatabase`` runs on the
harness default (SQLite) or on ``ZEEB_TEST_DATABASE_URL``; the
``requires_postgres`` test is the one that proves PostgreSQL accepts the
queries, and it first checks that the database really enforces the rule.
"""

import datetime

import pytest
import sqlalchemy.exc
from sqlalchemy.dialects import postgresql

from zeeb_orm import Model, fields
from zeeb_orm.query.expressions import Count, F, Max, Rank, Sum, Window
from zeeb_orm.testing import requires_postgres, temporary_database


class AgOrdAuthor(Model):
    id = fields.AutoField(primary_key=True)
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "agord_authors"


class AgOrdEvent(Model):
    id = fields.AutoField(primary_key=True)
    tool = fields.CharField(max_length=50)
    cost = fields.IntegerField(default=0)
    author = fields.ForeignKey(AgOrdAuthor, related_name="events", null=True)
    created_at = fields.DateTimeField()

    class Meta:
        table_name = "agord_events"
        ordering = ["-created_at"]


class AgOrdByAuthor(Model):
    """Default ordering through a relation: invalid under a GROUP BY pk too."""

    id = fields.AutoField(primary_key=True)
    title = fields.CharField(max_length=50)
    author = fields.ForeignKey(AgOrdAuthor, related_name="by_author")

    class Meta:
        table_name = "agord_by_author"
        ordering = ["author__name"]


def sql(queryset) -> str:
    return str(queryset._build_select().compile(dialect=postgresql.dialect()))


def per_tool():
    return AgOrdEvent.objects.values("tool").annotate(n=Count("id"))


class TestCompiledSql:
    def test_values_annotate_drops_meta_ordering(self):
        compiled = sql(per_tool())
        assert "GROUP BY agord_events.tool" in compiled
        assert "ORDER BY" not in compiled
        assert "created_at" not in compiled

    def test_values_list_annotate_drops_meta_ordering(self):
        compiled = sql(AgOrdEvent.objects.values_list("tool").annotate(n=Count("id")))
        assert "GROUP BY" in compiled
        assert "ORDER BY" not in compiled

    def test_filtered_and_having_aggregation_drops_meta_ordering(self):
        compiled = sql(per_tool().filter(tool__startswith="z", n__gt=1))
        assert "HAVING" in compiled
        assert "ORDER BY" not in compiled

    def test_whole_row_aggregation_drops_meta_ordering(self):
        """Grouped by the primary key; Django drops the default ordering here
        too, and a related-path ``Meta.ordering`` would be invalid SQL."""
        assert "ORDER BY" not in sql(AgOrdEvent.objects.annotate(m=Max("cost")))
        compiled = sql(AgOrdByAuthor.objects.annotate(n=Count("author__events")))
        assert "GROUP BY agord_by_author.id" in compiled
        assert "ORDER BY" not in compiled

    def test_explicit_order_by_an_aggregate_is_kept(self):
        compiled = sql(per_tool().order_by("-n"))
        assert "ORDER BY count(agord_events.id) DESC" in compiled

    def test_explicit_order_by_a_grouped_field_is_kept(self):
        compiled = sql(per_tool().order_by("tool"))
        assert "ORDER BY agord_events.tool ASC" in compiled

    def test_explicit_order_by_is_not_validated_against_the_grouping(self):
        """An explicit ungrouped name is the caller's to get right: it is
        compiled as written (and refused by PostgreSQL), never dropped."""
        assert "ORDER BY agord_events.created_at DESC" in sql(per_tool().order_by("-created_at"))

    def test_non_aggregate_annotations_keep_meta_ordering(self):
        """No GROUP BY, so the default ordering still applies."""
        assert "ORDER BY agord_events.created_at DESC" in sql(
            AgOrdEvent.objects.annotate(double=F("cost") * 2)
        )
        assert "ORDER BY agord_events.created_at DESC" in sql(
            AgOrdEvent.objects.annotate(r=Window(Rank(), order_by="-cost"))
        )

    def test_plain_queries_keep_meta_ordering(self):
        assert "ORDER BY agord_events.created_at DESC" in sql(AgOrdEvent.objects.all())
        assert "ORDER BY agord_events.created_at DESC" in sql(AgOrdEvent.objects.values("tool"))

    def test_sliced_count_of_an_aggregation_has_no_meta_ordering(self):
        """The count keeps the inner ORDER BY when sliced; it must be valid."""
        compiled = str(per_tool()[:2]._build_count_select().compile(dialect=postgresql.dialect()))
        assert "created_at" not in compiled


@pytest.fixture
async def events():
    async with temporary_database(AgOrdAuthor, AgOrdEvent, AgOrdByAuthor):
        amy = await AgOrdAuthor.objects.create(name="Amy")
        zed = await AgOrdAuthor.objects.create(name="Zed")
        start = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        rows = [("x", 1, amy), ("x", 2, amy), ("y", 4, zed), ("z", 8, None)]
        for hour, (tool, cost, author) in enumerate(rows):
            await AgOrdEvent.objects.create(
                tool=tool,
                cost=cost,
                author=author,
                created_at=start + datetime.timedelta(hours=hour),
            )
        await AgOrdByAuthor.objects.create(title="by zed", author=zed)
        await AgOrdByAuthor.objects.create(title="by amy", author=amy)
        yield {"amy": amy, "zed": zed}


class TestAgainstARealDatabase:
    async def test_groups_come_back_one_per_value(self, events):
        rows = await per_tool()
        assert sorted((r["tool"], r["n"]) for r in rows) == [("x", 2), ("y", 1), ("z", 1)]

    async def test_explicit_ordering_orders_the_groups(self, events):
        rows = (
            await AgOrdEvent.objects.values("tool").annotate(spend=Sum("cost")).order_by("-spend")
        )
        assert [r["tool"] for r in rows] == ["z", "y", "x"]
        rows = await per_tool().order_by("-n", "tool")
        assert [r["tool"] for r in rows] == ["x", "y", "z"]

    async def test_having_slicing_count_and_exists(self, events):
        assert await per_tool().filter(n__gt=1) == [{"tool": "x", "n": 2}]
        assert await per_tool().count() == 3
        assert await per_tool()[:2].count() == 2
        assert len(await per_tool()[:2]) == 2
        assert await per_tool().exists() is True

    async def test_first_and_last_need_an_ordering_unless_grouped_by_pk(self, events):
        with pytest.raises(TypeError, match=r"first\(\) on an unordered queryset"):
            await per_tool().first()
        with pytest.raises(TypeError, match=r"last\(\) on an unordered queryset"):
            await per_tool().last()
        # A bare order_by() clears the ordering; still nothing to fall back on.
        with pytest.raises(TypeError, match="Add an ordering"):
            await per_tool().order_by().first()

        assert await per_tool().order_by("tool").first() == {"tool": "x", "n": 2}
        assert await per_tool().order_by("tool").last() == {"tool": "z", "n": 1}
        # Grouped by the primary key, the primary key is a valid fallback.
        by_pk = AgOrdEvent.objects.values("id", "tool").annotate(n=Count("id"))
        assert (await by_pk.first())["tool"] == "x"
        assert (await by_pk.last())["tool"] == "z"
        assert (await AgOrdEvent.objects.annotate(m=Max("cost")).first()).tool == "x"

    async def test_first_without_aggregation_still_uses_meta_ordering(self, events):
        assert (await AgOrdEvent.objects.first()).tool == "z"
        assert (await AgOrdEvent.objects.annotate(double=F("cost") * 2).first()).tool == "z"

    async def test_whole_row_aggregation_with_a_related_meta_ordering(self, events):
        rows = await AgOrdByAuthor.objects.annotate(n=Count("author__events")).order_by("title")
        assert [(r.title, r.n) for r in rows] == [("by amy", 2), ("by zed", 1)]
        assert len(await AgOrdByAuthor.objects.annotate(n=Count("author__events"))) == 2


@requires_postgres()
async def test_postgres_accepts_every_aggregation_shape(events):
    """The queries PostgreSQL refused while Meta.ordering leaked into them."""
    # The database under test must enforce the rule, or the rest proves nothing.
    with pytest.raises(sqlalchemy.exc.ProgrammingError, match="GROUP BY"):
        await per_tool().order_by("-created_at")

    assert len(await per_tool()) == 3
    assert len(await AgOrdEvent.objects.values_list("tool").annotate(n=Count("id"))) == 3
    assert await per_tool().filter(n__gt=1) == [{"tool": "x", "n": 2}]
    assert len(await per_tool()[:2]) == 2
    assert await per_tool()[:2].count() == 2
    assert await per_tool().exists() is True
    assert await per_tool().order_by("tool").first() == {"tool": "x", "n": 2}
    assert len(await AgOrdByAuthor.objects.annotate(n=Count("author__events"))) == 2
