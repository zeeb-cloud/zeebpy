"""update(), bulk_update() and bulk_create() write what they are given.

* ``update(author=obj)`` / ``bulk_update(objs, ["author"])`` used the field
  name as the column name (the column is ``author_id``) and passed model
  instances through, so both failed.
* ``bulk_create`` issued one INSERT per row and ignored ``batch_size``.
"""

import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import mysql, postgresql, sqlite

from zeeb_orm import F, FieldError, Model, close_all_connections, configure, fields, setup_database


class BwAuthor(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "bw_authors"


class BwTag(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "bw_tags"


class BwPost(Model):
    title = fields.CharField(max_length=50)
    author = fields.ForeignKey(BwAuthor, null=True, on_delete="SET_NULL", related_name="posts")
    views = fields.IntegerField(default=0)
    note = fields.CharField(max_length=50, null=True)
    tags = fields.ManyToMany(BwTag, related_name="posts")

    class Meta:
        table_name = "bw_posts"


class BwCounter(Model):
    id = fields.AutoField(primary_key=True)
    slug = fields.CharField(max_length=50, unique=True)
    hits = fields.IntegerField(default=0)

    class Meta:
        table_name = "bw_counters"


class BwCode(Model):
    code = fields.CharField(max_length=20, unique=True)

    class Meta:
        table_name = "bw_codes"


MODELS = (BwAuthor, BwTag, BwPost, BwCounter, BwCode)


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
    BwPost.tags.get_through_table()
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    for name in ("bw_authors", "bw_tags", "bw_posts", "bw_posts_tags", "bw_counters", "bw_codes"):
        table = metadata.tables.get(name)
        if table is not None:
            metadata.remove(table)
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
def statements(db):
    """Every SQL statement sent to the driver (executemany counts once)."""
    seen: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    engine = db.get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine, "before_cursor_execute", record)


def _inserts(seen: list[str]) -> list[str]:
    return [s for s in seen if s.lstrip().upper().startswith("INSERT")]


class TestUpdate:
    async def test_update_fk_with_an_instance(self, db):
        ann = await BwAuthor.objects.create(name="Ann")
        post = await BwPost.objects.create(title="t")
        assert await BwPost.objects.filter(pk=post.pk).update(author=ann) == 1
        assert (await BwPost.objects.get(pk=post.pk)).author_id == ann.pk

    async def test_update_fk_with_a_pk_by_name_or_column(self, db):
        ann = await BwAuthor.objects.create(name="Ann")
        post = await BwPost.objects.create(title="t")
        await BwPost.objects.all().update(author=ann.pk)
        assert (await BwPost.objects.get(pk=post.pk)).author_id == ann.pk
        await BwPost.objects.all().update(author_id=None)
        assert (await BwPost.objects.get(pk=post.pk)).author_id is None

    async def test_update_with_expressions_and_pk(self, db):
        post = await BwPost.objects.create(title="t", views=1)
        await BwPost.objects.filter(pk=post.pk).update(views=F("views") + 4)
        assert (await BwPost.objects.get(pk=post.pk)).views == 5

    async def test_unknown_and_m2m_fields_raise(self, db):
        with pytest.raises(FieldError, match="no field named 'bogus'"):
            await BwPost.objects.all().update(bogus=1)
        with pytest.raises(FieldError, match="many-to-many"):
            await BwPost.objects.all().update(tags=[])

    async def test_related_manager_clear_still_works(self, db):
        ann = await BwAuthor.objects.create(name="Ann")
        await BwPost.objects.create(title="t", author=ann)
        await ann.posts.clear()
        assert await BwPost.objects.filter(author__isnull=True).count() == 1


class TestBulkUpdate:
    async def test_fk_field_by_name(self, db):
        ann = await BwAuthor.objects.create(name="Ann")
        bob = await BwAuthor.objects.create(name="Bob")
        p1 = await BwPost.objects.create(title="a")
        p2 = await BwPost.objects.create(title="b", author=ann)
        p1.author = ann
        p2.author = bob
        assert await BwPost.objects.bulk_update([p1, p2], ["author"]) == 2
        rows = {p.title: p.author_id for p in await BwPost.objects.all()}
        assert rows == {"a": ann.pk, "b": bob.pk}

    async def test_fk_by_column_name_and_several_fields(self, db):
        ann = await BwAuthor.objects.create(name="Ann")
        posts = [await BwPost.objects.create(title=f"p{i}") for i in range(3)]
        for i, post in enumerate(posts):
            post.author_id = ann.pk
            post.views = i * 10
            post.note = None if i == 1 else f"n{i}"
        assert await BwPost.objects.bulk_update(posts, ["author_id", "views", "note"]) == 3
        rows = {p.title: (p.author_id, p.views, p.note) for p in await BwPost.objects.all()}
        assert rows == {
            "p0": (ann.pk, 0, "n0"),
            "p1": (ann.pk, 10, None),
            "p2": (ann.pk, 20, "n2"),
        }

    async def test_batches_statements(self, db, statements):
        posts = [await BwPost.objects.create(title=f"p{i}") for i in range(7)]
        for post in posts:
            post.views = 99
        statements.clear()
        assert await BwPost.objects.bulk_update(posts, ["views"], batch_size=3) == 7
        updates = [s for s in statements if s.lstrip().upper().startswith("UPDATE")]
        assert len(updates) == 3
        assert await BwPost.objects.filter(views=99).count() == 7

    async def test_rejects_pk_m2m_unsaved_and_bad_batch_size(self, db):
        post = await BwPost.objects.create(title="p")
        with pytest.raises(ValueError, match="primary key"):
            await BwPost.objects.bulk_update([post], ["id"])
        with pytest.raises(FieldError):
            await BwPost.objects.bulk_update([post], ["tags"])
        with pytest.raises(ValueError, match="positive"):
            await BwPost.objects.bulk_update([post], ["views"], batch_size=0)
        with pytest.raises(ValueError, match="primary key set"):
            await BwCounter.objects.bulk_update([BwCounter(slug="x")], ["hits"])


class TestBulkCreate:
    async def test_batch_size_batches_the_inserts(self, db, statements):
        objs = [BwPost(title=f"p{i}") for i in range(25)]
        statements.clear()
        created = await BwPost.objects.bulk_create(objs, batch_size=10)
        assert len(_inserts(statements)) == 3
        assert created == objs
        assert await BwPost.objects.count() == 25
        assert all(o._state.persisted and o.pk is not None for o in created)
        stored = {p.pk for p in await BwPost.objects.all()}
        assert stored == {o.pk for o in created}

    async def test_default_is_one_statement(self, db, statements):
        statements.clear()
        await BwPost.objects.bulk_create([BwPost(title=f"p{i}") for i in range(40)])
        assert len(_inserts(statements)) == 1

    async def test_rows_with_different_columns_keep_their_defaults(self, db):
        objs = [BwPost(title="a", note="x"), BwPost(title="b"), BwPost(title="c", views=7)]
        await BwPost.objects.bulk_create(objs)
        rows = {p.title: (p.note, p.views) for p in await BwPost.objects.all()}
        assert rows == {"a": ("x", 0), "b": (None, 0), "c": (None, 7)}

    async def test_database_generated_pks_come_back_in_order(self, db, statements):
        objs = [BwCounter(slug=f"s{i}", hits=i) for i in range(12)]
        statements.clear()
        await BwCounter.objects.bulk_create(objs, batch_size=5)
        # Batched where RETURNING rows can be matched to their parameters
        # (PostgreSQL); SQLite and MySQL fall back to one row per statement.
        from sqlalchemy.sql.compiler import InsertmanyvaluesSentinelOpts

        dialect = db.get_engine().dialect
        sentinel = InsertmanyvaluesSentinelOpts.ANY_AUTOINCREMENT
        batched = bool(dialect.insertmanyvalues_implicit_sentinel & sentinel)
        assert len(_inserts(statements)) == (3 if batched else 12)
        assert all("RETURNING" in s for s in _inserts(statements))
        for obj in objs:
            stored = await BwCounter.objects.get(pk=obj.pk)
            assert stored.slug == obj.slug and stored.hits == obj.hits
        assert len({o.pk for o in objs}) == 12

    async def test_ignore_conflicts_batches_and_marks_what_was_inserted(self, db, statements):
        await BwCode.objects.create(code="taken")
        objs = [BwCode(code="new1"), BwCode(code="taken"), BwCode(code="new2")]
        statements.clear()
        await BwCode.objects.bulk_create(objs, ignore_conflicts=True)
        assert len(_inserts(statements)) == 1
        assert [o._state.persisted for o in objs] == [True, False, True]
        assert sorted(await BwCode.objects.values_list("code", flat=True)) == [
            "new1",
            "new2",
            "taken",
        ]

    async def test_ignore_conflicts_with_database_generated_pks(self, db):
        await BwCounter.objects.create(slug="taken")
        objs = [BwCounter(slug="a"), BwCounter(slug="taken"), BwCounter(slug="b")]
        await BwCounter.objects.bulk_create(objs, ignore_conflicts=True)
        assert [o._state.persisted for o in objs] == [True, False, True]
        assert objs[0].pk is not None and objs[2].pk is not None
        assert await BwCounter.objects.count() == 3

    async def test_bad_batch_size(self, db):
        with pytest.raises(ValueError, match="positive"):
            await BwPost.objects.bulk_create([BwPost(title="x")], batch_size=0)


@pytest.mark.parametrize(
    "dialect, expected",
    [
        (sqlite.dialect(), "ON CONFLICT DO NOTHING"),
        (postgresql.dialect(), "ON CONFLICT DO NOTHING"),
        (mysql.dialect(), "INSERT IGNORE"),
    ],
    ids=["sqlite", "postgresql", "mysql"],
)
def test_ignore_conflicts_statement_per_dialect(dialect, expected):
    from zeeb_orm.query.queryset import _insert_statement

    BwCode._get_table()
    stmt = _insert_statement(BwCode._get_table(), dialect.name, ignore_conflicts=True)
    sql = str(stmt.values({"id": uuid.uuid4(), "code": "x"}).compile(dialect=dialect))
    assert expected in sql


@pytest.mark.parametrize(
    "dialect", [sqlite.dialect(), postgresql.dialect(), mysql.dialect()], ids=str
)
def test_bulk_update_case_statement_compiles(dialect):
    from sqlalchemy import case, literal, update

    table = BwPost._get_table()
    stmt = (
        update(table)
        .where(table.c.id.in_(["a"]))
        .values(
            {
                "views": case(
                    (table.c.id == "a", literal(1, type_=table.c.views.type)), else_=table.c.views
                )
            }
        )
    )
    sql = str(stmt.compile(dialect=dialect))
    assert "CASE WHEN" in sql and "ELSE bw_posts.views END" in sql
