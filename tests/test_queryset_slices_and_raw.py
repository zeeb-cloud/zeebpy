"""Slicing semantics, writes on sliced/raw querysets, and FOR UPDATE joins.

Regressions:

* ``update()`` and ``delete()`` ignored a slice and wrote every matching row
  (``qs[:2].delete()`` emptied the table).
* A ``raw()`` queryset kept only its SQL for evaluation: ``update()``,
  ``delete()``, ``count()`` and ``filter()`` ran against the whole table.
* ``qs[5:10][2:4]`` returned rows 2-3 instead of 7-8, ``qs[5:3]`` sent
  ``LIMIT -2`` (unlimited on SQLite) and negative slices returned everything.
* ``last()`` on a sliced queryset reversed the order but kept the offset;
  ``qs[:1].get()`` overwrote the slice's limit.
* ``select_for_update(of=...)`` named the base table instead of the JOIN's
  alias, ``iterator()`` took no locks, and ``select_related()`` always
  outer-joined (PostgreSQL refuses FOR UPDATE on the nullable side).
* ``annotate(n=Count(...)).filter(n__gte=...).update()`` put the aggregate
  in WHERE.
"""

import pytest
from sqlalchemy.dialects import postgresql

from zeeb_orm import (
    Model,
    NotSupportedError,
    atomic,
    close_all_connections,
    configure,
    fields,
    setup_database,
)
from zeeb_orm.exceptions import FieldError
from zeeb_orm.query.expressions import Count


class SrItem(Model):
    n = fields.IntegerField()
    flag = fields.BooleanField(default=False)

    class Meta:
        table_name = "sr_items"


class SrPublisher(Model):
    name = fields.CharField(max_length=20)

    class Meta:
        table_name = "sr_publishers"


class SrAuthor(Model):
    name = fields.CharField(max_length=20)
    age = fields.IntegerField(null=True)
    publisher = fields.ForeignKey(SrPublisher, related_name="authors", null=True)

    class Meta:
        table_name = "sr_authors"


class SrBook(Model):
    title = fields.CharField(max_length=20)
    author = fields.ForeignKey(SrAuthor, related_name="books", on_delete="DO_NOTHING")
    editor = fields.ForeignKey(
        SrAuthor, related_name="edited", null=True, on_delete="DO_NOTHING"
    )

    class Meta:
        table_name = "sr_books"


MODELS = (SrItem, SrPublisher, SrAuthor, SrBook)


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


@pytest.fixture
async def items(db):
    for i in range(10):
        await SrItem.objects.create(n=i)
    return SrItem.objects.order_by("n")


def _ns(rows):
    return [row.n for row in rows]


def _pg(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


# --- slicing -------------------------------------------------------------


class TestSlicing:
    async def test_slice_of_slice_composes(self, items):
        assert _ns(await items[5:10][2:4]) == [7, 8]
        assert _ns(await items[5:10][3:]) == [8, 9]
        assert _ns(await items[5:][:2]) == [5, 6]
        assert _ns(await items[2:][3:5]) == [5, 6]
        assert _ns(await items[5:10][2:100]) == [7, 8, 9]
        assert _ns(await items[5:10][1]) == [6]

    async def test_empty_range_yields_nothing(self, items):
        assert await items[5:3] == []
        assert await items[5:3].exists() is False
        assert await items[5:3].count() == 0
        assert await items[5:7][4:] == []
        assert await items[5:3].first() is None

    async def test_negative_indexes_raise(self, items):
        with pytest.raises(ValueError, match="Negative indexing is not supported."):
            items[-2:]
        with pytest.raises(ValueError, match="Negative indexing is not supported."):
            items[-1]
        with pytest.raises(ValueError, match="Negative indexing is not supported."):
            items[:-1]

    async def test_bad_index_type_raises(self, items):
        with pytest.raises(TypeError):
            items["a"]
        with pytest.raises(TypeError):
            items[1.5:]

    async def test_step_is_applied_to_the_rows(self, items):
        assert _ns(await items[::2]) == [0, 2, 4, 6, 8]
        assert _ns(await items[1:8:3]) == [1, 4, 7]
        assert _ns(await items[:3:-1]) == [2, 1, 0]
        assert await items[1:8:3].count() == 3
        assert await items[::2].exists() is True
        assert _ns([obj async for obj in items[1:8:3].iterator()]) == [1, 4, 7]
        with pytest.raises(ValueError):
            items[::0]
        with pytest.raises(TypeError):
            items[::2].filter(n=1)


# --- writes on sliced querysets ---------------------------------------------


class TestSlicedWrites:
    async def test_update_after_slice_raises(self, items):
        with pytest.raises(TypeError, match="Cannot update a query once a slice"):
            await items[:3].update(flag=True)
        assert await SrItem.objects.filter(flag=True).count() == 0

    async def test_delete_after_slice_raises_on_the_fast_path(self, items):
        with pytest.raises(TypeError, match="Cannot use 'limit' or 'offset' with delete"):
            await items[:2].delete()
        assert await SrItem.objects.count() == 10

    async def test_delete_after_slice_raises_on_the_collector_path(self, items):
        from zeeb_orm.signals import pre_delete

        def receiver(sender, **kwargs):
            pass

        pre_delete.connect(receiver, sender=SrItem, weak=False)
        try:
            with pytest.raises(TypeError, match="Cannot use 'limit' or 'offset'"):
                await items[2:4].delete()
        finally:
            pre_delete.disconnect(receiver, sender=SrItem)
        assert await SrItem.objects.count() == 10


class TestFirstLastGetOnSlices:
    async def test_last_after_slice_raises(self, items):
        with pytest.raises(TypeError, match="Cannot reverse a query once a slice"):
            await items[0:2].last()

    async def test_first_stays_within_the_slice(self, items):
        assert (await items[3:6].first()).n == 3

    async def test_first_on_unordered_slice_raises(self, items):
        with pytest.raises(TypeError, match="Cannot reorder"):
            await SrItem.objects.all()[3:6].first()

    async def test_get_respects_the_slice(self, items):
        assert (await items[:1].get()).n == 0
        assert (await items[4:5].get()).n == 4
        with pytest.raises(SrItem.MultipleObjectsReturned):
            await items[:3].get()
        with pytest.raises(SrItem.DoesNotExist):
            await items[5:3].get()
        with pytest.raises(TypeError, match="Cannot filter a query once a slice"):
            await items[:3].get(n=1)


# --- raw() ----------------------------------------------------------------


class TestRawQuerysets:
    @pytest.fixture
    async def raw(self, db):
        for i in range(5):
            await SrItem.objects.create(n=i)
        return SrItem.objects.raw("SELECT * FROM sr_items WHERE n < ? ORDER BY n", [2])

    async def test_evaluation_count_exists_and_slicing_use_the_raw_rows(self, raw):
        assert await raw.count() == 2
        assert await raw.exists() is True
        assert _ns(await raw) == [0, 1]
        assert _ns(await raw[1:]) == [1]
        assert _ns(await raw[0]) == [0]
        assert _ns([obj async for obj in raw]) == [0, 1]

    async def test_writes_raise_and_leave_the_table_alone(self, raw):
        with pytest.raises(NotSupportedError):
            await raw.update(n=42)
        with pytest.raises(NotSupportedError):
            await raw.delete()
        assert sorted(_ns(await SrItem.objects.all())) == [0, 1, 2, 3, 4]

    @pytest.mark.parametrize(
        "call",
        [
            lambda qs: qs.filter(n=1),
            lambda qs: qs.exclude(n=1),
            lambda qs: qs.order_by("n"),
            lambda qs: qs.distinct(),
            lambda qs: qs.only("n"),
            lambda qs: qs.defer("n"),
            lambda qs: qs.values("n"),
            lambda qs: qs.values_list("n"),
            lambda qs: qs.select_related("x"),
            lambda qs: qs.annotate(c=Count("id")),
            lambda qs: qs.none(),
            lambda qs: qs.select_for_update(),
            lambda qs: qs.iterator(),
            lambda qs: qs.union(SrItem.objects.all()),
            lambda qs: SrItem.objects.all().union(qs),
        ],
    )
    async def test_query_building_methods_raise(self, raw, call):
        with pytest.raises(NotSupportedError):
            call(raw)

    @pytest.mark.parametrize(
        "call",
        [
            lambda qs: qs.aggregate(c=Count("id")),
            lambda qs: qs.get(),
            lambda qs: qs.first(),
            lambda qs: qs.last(),
            lambda qs: qs.in_bulk(),
            lambda qs: qs.explain(),
            lambda qs: qs.create(n=9),
            lambda qs: qs.get_or_create(n=9),
            lambda qs: qs.update_or_create(n=9),
            lambda qs: qs.bulk_create([SrItem(n=9)]),
            lambda qs: qs.bulk_update([], ["n"]),
        ],
    )
    async def test_executing_methods_raise(self, raw, call):
        with pytest.raises(NotSupportedError):
            await call(raw)
        assert await SrItem.objects.count() == 5


# --- update/delete filtered on an aggregate ----------------------------------


class TestWritesFilteredOnAggregates:
    @pytest.fixture
    async def authors(self, db):
        a = await SrAuthor.objects.create(name="a", age=1)
        b = await SrAuthor.objects.create(name="b", age=2)
        c = await SrAuthor.objects.create(name="c", age=3)
        await SrBook.objects.create(title="x", author=a)
        await SrBook.objects.create(title="y", author=a)
        await SrBook.objects.create(title="z", author=c)
        return a, b, c

    async def test_update(self, authors):
        qs = SrAuthor.objects.annotate(n=Count("books")).filter(n__gte=1, age__lt=3)
        assert await qs.update(age=99) == 1
        ages = {o.name: o.age for o in await SrAuthor.objects.all()}
        assert ages == {"a": 99, "b": 2, "c": 3}

    async def test_delete(self, authors):
        await SrBook.objects.all().delete()
        await SrBook.objects.create(title="w", author=authors[2])
        deleted = await SrAuthor.objects.annotate(n=Count("books")).filter(n=0).delete()
        assert deleted == 2
        assert [o.name for o in await SrAuthor.objects.all()] == ["c"]


# --- select_for_update and join types ----------------------------------------


class TestJoinTypes:
    async def test_required_fk_is_an_inner_join(self, db):
        sql = str(SrBook.objects.select_related("author")._build_select())
        assert "LEFT OUTER JOIN" not in sql
        assert "JOIN sr_authors AS _sr_author_author" in sql

    async def test_nullable_fk_stays_outer_and_so_does_the_rest_of_its_chain(self, db):
        sql = str(SrBook.objects.select_related("editor__publisher")._build_select())
        assert sql.count("LEFT OUTER JOIN") == 2
        # author is required, but its publisher is nullable: inner, then outer
        sql = str(SrBook.objects.select_related("author__publisher")._build_select())
        assert "JOIN sr_authors AS _sr_author_author" in sql
        assert "LEFT OUTER JOIN sr_publishers" in sql
        assert sql.count("LEFT OUTER JOIN") == 1

    async def test_required_fk_behind_a_reverse_hop_stays_outer(self, db):
        sql = str(SrPublisher.objects.filter(authors__books__title="x")._build_select())
        assert sql.count("LEFT OUTER JOIN") == 2

    async def test_inner_joins_return_the_same_rows(self, db):
        pub = await SrPublisher.objects.create(name="p")
        a = await SrAuthor.objects.create(name="a", publisher=pub)
        b = await SrAuthor.objects.create(name="b")
        await SrBook.objects.create(title="x", author=a)
        await SrBook.objects.create(title="y", author=b, editor=a)
        books = await SrBook.objects.select_related("author", "editor").order_by("title")
        assert [bk.title for bk in books] == ["x", "y"]
        assert books[0].editor is None and books[1].editor is not None
        authors = await SrAuthor.objects.select_related("publisher").order_by("name")
        assert [au.name for au in authors] == ["a", "b"]
        titles = await SrBook.objects.filter(author__publisher__name="p").values_list(
            "title", flat=True
        )
        assert list(titles) == ["x"]
        titles = await SrBook.objects.exclude(author__publisher__name="p").values_list(
            "title", flat=True
        )
        assert list(titles) == ["y"]


class TestSelectForUpdate:
    async def test_of_names_the_join_alias(self, db):
        qs = SrBook.objects.select_related("author").select_for_update(of=("self", "author"))
        sql = _pg(qs._apply_for_update(qs._build_select()))
        assert "FOR UPDATE OF sr_books, _sr_author_author" in sql
        assert "LEFT OUTER JOIN" not in sql

    async def test_of_a_nested_relation(self, db):
        qs = SrBook.objects.select_related("author__publisher").select_for_update(
            of=("author__publisher",)
        )
        stmt, joins = qs._build_select_with_joins()
        assert "FOR UPDATE OF _sr_author_publisher_publisher" in _pg(
            qs._apply_for_update(stmt, joins)
        )

    async def test_of_a_relation_the_query_does_not_join_raises(self, db):
        qs = SrBook.objects.select_for_update(of=("author",))
        with pytest.raises(FieldError, match="Only relational fields followed"):
            qs._apply_for_update(qs._build_select())

    async def test_iterator_locks_too(self, db):
        await SrItem.objects.create(n=1)
        async with atomic():
            with pytest.raises(NotSupportedError):
                [obj async for obj in SrItem.objects.select_for_update().iterator()]
