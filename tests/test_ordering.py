"""order_by() / Meta.ordering: every name resolves or raises FieldError.

Unknown names and ForeignKey names used to be dropped from the ORDER BY
without a word, so a typo silently returned rows in arbitrary order.
"""

import pytest
from sqlalchemy.dialects import postgresql

from zeeb_orm import FieldError, Model, close_all_connections, configure, fields, setup_database


class OrdAuthor(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "ord_authors"


class OrdPost(Model):
    id = fields.AutoField(primary_key=True)
    title = fields.CharField(max_length=50)
    author = fields.ForeignKey(OrdAuthor, related_name="posts")
    views = fields.IntegerField(default=0)

    class Meta:
        table_name = "ord_posts"


class OrdSorted(Model):
    title = fields.CharField(max_length=50)

    class Meta:
        table_name = "ord_sorted"
        ordering = ["-title"]


class OrdTypo(Model):
    title = fields.CharField(max_length=50)

    class Meta:
        table_name = "ord_typos"
        ordering = ["-titel"]


MODELS = (OrdAuthor, OrdPost, OrdSorted, OrdTypo)


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
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
async def posts(db):
    zed = await OrdAuthor.objects.create(name="Zed")
    amy = await OrdAuthor.objects.create(name="Amy")
    await OrdPost.objects.create(title="b", author=zed, views=3)
    await OrdPost.objects.create(title="a", author=amy, views=1)
    await OrdPost.objects.create(title="c", author=zed, views=2)
    return {"zed": zed, "amy": amy}


async def test_unknown_order_by_name_raises(posts):
    with pytest.raises(FieldError, match="tittle"):
        await OrdPost.objects.order_by("tittle")


async def test_unknown_name_in_meta_ordering_raises(db):
    with pytest.raises(FieldError, match="titel"):
        await OrdTypo.objects.all()


async def test_unknown_path_raises(posts):
    with pytest.raises(FieldError):
        await OrdPost.objects.order_by("author__nme")


async def test_fk_name_orders_by_the_fk_column(posts):
    by_name = await OrdPost.objects.order_by("author", "title")
    by_column = await OrdPost.objects.order_by("author_id", "title")
    assert [p.title for p in by_name] == [p.title for p in by_column]
    sql = str(
        OrdPost.objects.order_by("-author")._build_select().compile(dialect=postgresql.dialect())
    )
    assert "ORDER BY ord_posts.author_id DESC" in sql


async def test_pk_and_plain_fields(posts):
    assert [p.title for p in await OrdPost.objects.order_by("pk")] == ["b", "a", "c"]
    assert [p.title for p in await OrdPost.objects.order_by("-views")] == ["b", "c", "a"]


async def test_related_path_and_annotation_still_work(posts):
    from zeeb_orm import F

    rows = await OrdPost.objects.order_by("author__name", "title")
    assert [p.title for p in rows] == ["a", "b", "c"]
    rows = await OrdPost.objects.annotate(double=F("views") * 2).order_by("-double")
    assert [p.title for p in rows] == ["b", "c", "a"]


async def test_first_and_last_resolve_the_same_way(posts):
    assert (await OrdPost.objects.order_by("author__name", "views").first()).title == "a"
    assert (await OrdPost.objects.order_by("author__name", "views").last()).title == "b"
    with pytest.raises(FieldError):
        await OrdPost.objects.order_by("nope").first()


async def test_bare_order_by_clears_meta_ordering(db):
    for title in ("a", "c", "b"):
        await OrdSorted.objects.create(title=title)
    assert [o.title for o in await OrdSorted.objects.all()] == ["c", "b", "a"]
    sql = str(OrdSorted.objects.order_by()._build_select().compile(dialect=postgresql.dialect()))
    assert "ORDER BY" not in sql
    # An explicit order_by() replaces Meta.ordering, it does not extend it.
    sql = str(
        OrdSorted.objects.order_by("id")._build_select().compile(dialect=postgresql.dialect())
    )
    assert "ORDER BY ord_sorted.id ASC" in sql and "title" not in sql.split("ORDER BY")[1]
