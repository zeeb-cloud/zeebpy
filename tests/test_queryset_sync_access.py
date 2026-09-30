"""Sync access to a QuerySet: never a hidden event loop, always a clear error.

``__iter__`` guarded against a running loop but caught its own RuntimeError,
so the guard never fired and ``asyncio.run()`` failed instead; ``__len__``
called ``asyncio.run(count())``, which fails inside a running loop and, outside
one, starts a second loop the engine's connections are not bound to. Both now
raise ``TypeError`` pointing at the async API unless the queryset has already
been evaluated, in which case they read the result cache.
"""

import pytest

from zeeb_orm import Model, close_all_connections, configure, fields, setup_database


class SyItem(Model):
    name = fields.CharField(max_length=20)

    class Meta:
        table_name = "sy_items"


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()
    SyItem._sa_table = None
    SyItem._sa_model = None
    metadata.clear()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    SyItem._get_table()
    await database.create_all()
    await SyItem.objects.create(name="a")
    await SyItem.objects.create(name="b")
    yield database
    await database.drop_all()
    await close_all_connections()
    table = metadata.tables.get("sy_items")
    if table is not None:
        metadata.remove(table)
    SyItem._sa_table = None
    SyItem._sa_model = None
    Settings.reset()


@pytest.mark.parametrize(
    "operation", [list, len, bool, lambda qs: [x for x in qs]], ids=["list", "len", "bool", "for"]
)
async def test_unevaluated_queryset_raises_type_error_inside_a_loop(db, operation):
    with pytest.raises(TypeError, match="await qs"):
        operation(SyItem.objects.all())


@pytest.mark.parametrize("operation", [list, len, bool], ids=["list", "len", "bool"])
def test_unevaluated_queryset_raises_type_error_without_a_loop(operation):
    with pytest.raises(TypeError, match="async for obj in qs"):
        operation(SyItem.objects.filter(name="a"))


async def test_evaluated_queryset_reads_its_cache(db):
    qs = SyItem.objects.order_by("name")
    rows = await qs
    assert [i.name for i in qs] == ["a", "b"]
    assert len(qs) == 2 and bool(qs)
    assert list(qs) == rows

    empty = SyItem.objects.filter(name="zzz")
    await empty
    assert not empty and len(empty) == 0


async def test_async_iteration_still_works(db):
    names = [item.name async for item in SyItem.objects.order_by("name")]
    assert names == ["a", "b"]
