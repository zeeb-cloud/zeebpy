"""count() counts what the queryset yields.

It used to be ``count(*) FROM <table> WHERE ...`` no matter what: slices were
ignored, ``distinct()`` over-counted, and a filter on an aggregate annotation
landed in WHERE and made the database reject the statement.
"""

import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite

from zeeb_orm import Count, Model, close_all_connections, configure, fields, setup_database


class CtAuthor(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "ct_authors"


class CtTag(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "ct_tags"


class CtPost(Model):
    title = fields.CharField(max_length=50)
    author = fields.ForeignKey(CtAuthor, related_name="posts")
    tags = fields.ManyToMany(CtTag, related_name="posts")

    class Meta:
        table_name = "ct_posts"
        ordering = ["title"]


MODELS = (CtAuthor, CtTag, CtPost)


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    for model in MODELS:
        model._get_table()
    CtPost.tags.get_through_table()
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    for name in ("ct_authors", "ct_tags", "ct_posts", "ct_posts_tags"):
        table = metadata.tables.get(name)
        if table is not None:
            metadata.remove(table)
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
async def world(db):
    ann = await CtAuthor.objects.create(name="Ann")
    bob = await CtAuthor.objects.create(name="Bob")
    await CtAuthor.objects.create(name="Cy")
    py = await CtTag.objects.create(name="python")
    db_ = await CtTag.objects.create(name="db")
    p1 = await CtPost.objects.create(title="p1", author=ann)
    p2 = await CtPost.objects.create(title="p2", author=ann)
    p3 = await CtPost.objects.create(title="p3", author=bob)
    await p1.tags.add(py, db_)
    await p2.tags.add(py)
    await p3.tags.add(db_)


async def test_sliced_count(world):
    assert await CtPost.objects.all()[:2].count() == 2
    assert await CtPost.objects.all()[1:].count() == 2
    assert await CtPost.objects.all()[1:2].count() == 1
    assert await CtPost.objects.all()[5:].count() == 0


async def test_distinct_count_over_a_multi_valued_join(world):
    joined = CtPost.objects.filter(tags__name__in=["python", "db"])
    assert await joined.count() == 4  # one row per matching link, like len()
    assert await joined.distinct().count() == 2 + 1
    assert await joined.distinct().count() == len(await joined.distinct())


async def test_count_with_a_filter_on_an_aggregate_annotation(world):
    qs = CtAuthor.objects.annotate(n=Count("posts")).filter(n__gt=1)
    assert await qs.count() == 1
    assert await CtAuthor.objects.annotate(n=Count("posts")).filter(n=0).count() == 1
    assert await CtAuthor.objects.annotate(n=Count("posts")).count() == 3


async def test_count_of_grouped_values(world):
    groups = CtPost.objects.values("author").annotate(n=Count("id"))
    assert await groups.count() == 2
    assert await groups.count() == len(await groups)


async def test_distinct_values_count(world):
    assert await CtPost.objects.values("author").distinct().count() == 2


async def test_evaluated_queryset_counts_its_cache(world):
    qs = CtPost.objects.all()
    rows = await qs
    await CtPost.objects.filter(title="p1").delete()
    assert await qs.count() == len(rows) == 3
    assert await qs.exists()


async def test_plain_count_keeps_the_fast_form(world):
    stmt = CtPost.objects.filter(title="p1")._build_count_select()
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "FROM ct_posts" in sql and "_count" not in sql
    assert await CtPost.objects.filter(title="p1").count() == 1


@pytest.mark.parametrize(
    "dialect", [sqlite.dialect(), postgresql.dialect(), mysql.dialect()], ids=str
)
def test_count_subquery_compiles_on_every_dialect(dialect):
    CtPost.tags.get_through_table()
    for qs in (
        CtPost.objects.all()[:5],
        CtPost.objects.filter(tags__name="x").distinct(),
        CtAuthor.objects.annotate(n=Count("posts")).filter(n__gt=1),
    ):
        sql = str(qs._build_count_select().compile(dialect=dialect))
        assert sql.startswith("SELECT count(*)")
        assert "AS _count" in sql
    having = str(
        CtAuthor.objects.annotate(n=Count("posts"))
        .filter(n__gt=1)
        ._build_count_select()
        .compile(dialect=dialect)
    )
    assert "HAVING" in having
    unordered = str(CtPost.objects.distinct()._build_count_select().compile(dialect=dialect))
    assert "ORDER BY" not in unordered
