"""The ORM behaves the same on every supported database.

Runs on in-memory SQLite by default. CI runs this file again with
``ZEEB_TEST_DATABASE_URL`` pointing at PostgreSQL and at MySQL, because most of
the ORM's dialect branches (datetime transforms, bulk updates, LIKE escaping,
``SELECT ... FOR UPDATE``, the transaction an ``IntegrityError`` aborts) only
execute against the database they are written for::

    ZEEB_TEST_DATABASE_URL=postgresql+asyncpg://postgres:zeeb@localhost:5432/zeeb \\
        pytest tests/test_dialect_conformance.py

The migration test also needs the matching synchronous driver (psycopg2 /
pymysql), which the migration executor runs on.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from zeeb_orm import F, Model, Q, Window, atomic, fields
from zeeb_orm.query.expressions import Count, Rank, Sum
from zeeb_orm.testing import resolve_database_url, temporary_database


class DcAuthor(Model):
    id = fields.AutoField()
    name = fields.CharField(max_length=50, unique=True)

    class Meta:
        table_name = "dc_authors"


class DcTag(Model):
    id = fields.AutoField()
    label = fields.CharField(max_length=30)

    class Meta:
        table_name = "dc_tags"


class DcPost(Model):
    id = fields.AutoField()
    title = fields.CharField(max_length=100)
    views = fields.IntegerField(default=0)
    price = fields.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    published = fields.DateTimeField(null=True)
    meta = fields.JSONField(null=True)
    author = fields.ForeignKey(DcAuthor, on_delete="CASCADE", related_name="posts")
    tags = fields.ManyToManyField(DcTag, related_name="posts")

    class Meta:
        table_name = "dc_posts"


MODELS = (DcAuthor, DcTag, DcPost)


def _dialect() -> str:
    return resolve_database_url().split(":", 1)[0].split("+", 1)[0]


@pytest.fixture
async def db():
    async with temporary_database(*MODELS) as database:
        yield database


async def _seed() -> tuple[DcAuthor, DcAuthor]:
    ada = await DcAuthor.objects.create(name="ada")
    bob = await DcAuthor.objects.create(name="bob")
    when = datetime.datetime(2024, 3, 5, 12, 0, tzinfo=datetime.timezone.utc)
    await DcPost.objects.create(
        title="50% off_sale", views=10, price=Decimal("9.99"), published=when,
        meta={"lang": "en"}, author_id=ada.id,
    )
    await DcPost.objects.create(
        title="Plain", views=3, price=Decimal("1.50"),
        published=when.replace(year=2023), author_id=ada.id,
    )
    await DcPost.objects.create(title="Other", views=7, author_id=bob.id)
    return ada, bob


async def test_lookups_and_transforms(db):
    await _seed()
    # LIKE metacharacters in the value are literals, on every dialect.
    assert await DcPost.objects.filter(title__contains="50%").count() == 1
    assert await DcPost.objects.filter(title__contains="f_s").count() == 1
    assert await DcPost.objects.filter(title__contains="%").count() == 1
    assert await DcPost.objects.filter(title__icontains="PLAIN").count() == 1
    assert await DcPost.objects.filter(published__year=2024).count() == 1
    assert await DcPost.objects.filter(published__month=3).count() == 2
    assert await DcPost.objects.filter(views__range=(4, 10)).count() == 2
    assert await DcPost.objects.filter(published__isnull=True).count() == 1
    assert await DcPost.objects.filter(author__name__in=["bob"]).count() == 1
    assert await DcPost.objects.filter(Q(views__gt=5) & ~Q(author__name="bob")).count() == 1
    # Decimal round-trips exactly.
    post = await DcPost.objects.get(title="Plain")
    assert post.price == Decimal("1.50")


async def test_expressions_aggregates_and_windows(db):
    await _seed()
    assert await DcPost.objects.filter(title="Plain").update(views=F("views") + 5) == 1
    assert (await DcPost.objects.get(title="Plain")).views == 8

    totals = await DcPost.objects.aggregate(total=Sum("views"), n=Count("id"))
    assert totals == {"total": 25, "n": 3}

    per_author = {
        row["name"]: row["n"]
        for row in await DcAuthor.objects.annotate(n=Count("posts")).values("name", "n")
    }
    assert per_author == {"ada": 2, "bob": 1}

    ranked = await DcPost.objects.annotate(
        rank=Window(Rank(), order_by="-views")
    ).order_by("rank").values_list("title", flat=True)
    assert list(ranked) == ["50% off_sale", "Plain", "Other"]


async def test_bulk_writes(db):
    ada, _ = await _seed()
    created = await DcPost.objects.bulk_create(
        [DcPost(title=f"bulk-{i}", views=i, author_id=ada.id) for i in range(5)]
    )
    assert len(created) == 5
    rows = await DcPost.objects.filter(title__startswith="bulk-").order_by("views")
    for row in rows:
        row.views = row.views * 10
    assert await DcPost.objects.bulk_update(rows, ["views"]) == 5
    views = await DcPost.objects.filter(title__startswith="bulk-").order_by("views").values_list(
        "views", flat=True
    )
    assert list(views) == [0, 10, 20, 30, 40]


async def test_many_to_many_and_prefetch(db):
    ada, _ = await _seed()
    red = await DcTag.objects.create(label="red")
    blue = await DcTag.objects.create(label="blue")
    post = await DcPost.objects.get(title="Plain")
    await post.tags.set([red.id, blue.id])
    assert await post.tags.count() == 2
    assert await DcPost.objects.filter(tags__label="red").count() == 1

    posts = await DcPost.objects.filter(author_id=ada.id).prefetch_related("tags").order_by("id")
    labels = [sorted(t.label for t in await p.tags.all()) for p in posts]
    assert labels == [[], ["blue", "red"]]


async def test_atomic_rolls_back_and_savepoints_recover(db):
    await _seed()
    with pytest.raises(RuntimeError):
        async with atomic():
            await DcAuthor.objects.create(name="ghost")
            raise RuntimeError("undo")
    assert not await DcAuthor.objects.filter(name="ghost").exists()

    async with atomic():
        await DcAuthor.objects.create(name="kept")
        with pytest.raises(RuntimeError):
            async with atomic():
                await DcAuthor.objects.create(name="dropped")
                raise RuntimeError("undo the savepoint only")
    assert await DcAuthor.objects.filter(name="kept").exists()
    assert not await DcAuthor.objects.filter(name="dropped").exists()


async def test_get_or_create_survives_a_conflict_inside_a_transaction(db):
    await _seed()
    # PostgreSQL aborts the whole transaction on an IntegrityError unless the
    # insert ran in a savepoint; the outer block must still be usable.
    async with atomic():
        author, created = await DcAuthor.objects.get_or_create(name="ada")
        assert created is False
        await DcAuthor.objects.create(name="after")
    assert await DcAuthor.objects.filter(name="after").exists()


async def test_select_for_update_inside_a_transaction(db):
    if _dialect() == "sqlite":
        pytest.skip("SQLite has no row locks; select_for_update is refused there")
    await _seed()
    async with atomic():
        locked = await DcPost.objects.select_for_update().filter(views__gte=7).order_by("id")
        assert [p.title for p in locked] == ["50% off_sale", "Other"]
        await DcPost.objects.filter(pk=locked[0].pk).update(views=0)
    assert (await DcPost.objects.get(title="50% off_sale")).views == 0


async def test_json_round_trips(db):
    await _seed()
    post = await DcPost.objects.get(title="50% off_sale")
    assert post.meta == {"lang": "en"}
    post.meta = {"lang": "de", "tags": [1, 2]}
    await post.save()
    assert (await DcPost.objects.get(pk=post.pk)).meta == {"lang": "de", "tags": [1, 2]}


async def test_a_migrated_schema_has_no_changes_left_to_detect(tmp_path):
    """makemigrations right after migrate finds nothing, on the real database.

    The autodetector diffs the models against the schema the migration files
    declare (replayed on SQLite); a type or default that reads back differently
    there would show up here as a phantom change on the target dialect.
    """
    import sys

    from sqlalchemy import MetaData, create_engine

    import zeeb_agents as agents
    from zeeb_orm.db.urls import sync_database_url
    from zeeb_orm.models.base import _model_registry, metadata

    url = resolve_database_url()
    if url.startswith("sqlite") and ":memory:" in url:
        url = f"sqlite+aiosqlite:///{tmp_path / 'conformance.sqlite3'}"
    registry_before = dict(_model_registry)
    tables_before = set(metadata.tables)
    modules_before = set(sys.modules)
    path_before = list(sys.path)

    res = await agents.create_project("conform", directory=str(tmp_path))
    assert res.success, res.message
    root = tmp_path / "conform"
    with (root / ".env").open("a", encoding="utf-8") as env:
        env.write(f"\nDATABASE_URL={url}\n")
    spec = {
        "name": "shop",
        "app": "shop",
        "entities": [
            {"name": "Category", "fields": [{"name": "name", "type": "string", "unique": True}]},
            {
                "name": "Product",
                "fields": [
                    {"name": "title", "type": "string", "max_length": 120},
                    {"name": "body", "type": "text"},
                    {"name": "price", "type": "decimal"},
                    {"name": "stock", "type": "integer"},
                    {"name": "active", "type": "boolean"},
                    {"name": "released", "type": "datetime"},
                    {"name": "attrs", "type": "json"},
                    {"name": "category", "type": "fk", "to": "Category"},
                ],
            },
        ],
    }
    try:
        res = await agents.build_feature(spec, migrate=True, verify=False, project_id=root)
        assert res.success, res.message
        # The tables are in the target database (the project's own .env said
        # where), not in a SQLite file next to it.
        from sqlalchemy import inspect

        probe = create_engine(sync_database_url(url))
        try:
            assert {"shop_category", "shop_product"} <= set(inspect(probe).get_table_names())
        finally:
            probe.dispose()
        res = await agents.make_migrations(project_id=root)
        assert res.success, res.message
        assert res.data["created"] is None, res.data
    finally:
        engine = create_engine(sync_database_url(url))
        try:
            reflected = MetaData()
            reflected.reflect(engine)
            reflected.drop_all(engine)
        finally:
            engine.dispose()
        _model_registry.clear()
        _model_registry.update(registry_before)
        for name in set(metadata.tables) - tables_before:
            metadata.remove(metadata.tables[name])
        for name in set(sys.modules) - modules_before:
            if name == "apps" or name.startswith("apps.") or name.startswith("conform"):
                del sys.modules[name]
        sys.path[:] = path_before
