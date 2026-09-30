"""QuerySet transactions and row loading follow the queryset's database alias.

* ``delete()`` collected the objects to cascade *before* opening its
  transaction, and treated a transaction open on any database as its own.
* ``select_for_update()`` accepted a transaction on another database as the
  one holding its locks.
* Rows load through ``Model._from_db`` when the model layer provides it.
"""

from types import SimpleNamespace

import pytest

from zeeb_orm import (
    Database,
    Model,
    TransactionManagementError,
    close_all_connections,
    configure,
    fields,
    register_database,
    setup_database,
)
from zeeb_orm.db import connection
from zeeb_orm.db.connection import atomic


class SsParent(Model):
    name = fields.CharField(max_length=20)

    class Meta:
        table_name = "ss_parents"


class SsChild(Model):
    name = fields.CharField(max_length=20)
    parent = fields.ForeignKey(SsParent, related_name="children")

    class Meta:
        table_name = "ss_children"


MODELS = (SsParent, SsChild)


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
    other = Database("sqlite+aiosqlite:///:memory:")
    await other.connect()
    await other.create_all()
    register_database(other, "other")
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


class TestDelete:
    async def test_collection_runs_inside_the_transaction(self, db, monkeypatch):
        from zeeb_orm.models.deletion import Collector

        parent = await SsParent.objects.create(name="p")
        await SsChild.objects.create(name="c", parent=parent)

        seen = []
        real_collect = Collector.collect

        async def spy(self, objs, *args, **kwargs):
            seen.append(connection._active_session.get())
            return await real_collect(self, objs, *args, **kwargs)

        monkeypatch.setattr(Collector, "collect", spy)
        assert await SsParent.objects.filter(name="p").delete() == 2
        assert seen and seen[0] is not None
        assert await SsChild.objects.count() == 0

    async def test_transaction_on_another_database_is_not_joined(self, db):
        parent = await SsParent.objects.create(name="p")
        await SsChild.objects.create(name="c", parent=parent)
        with pytest.raises(RuntimeError):
            async with atomic("other"):
                assert await SsParent.objects.filter(name="p").delete() == 2
                raise RuntimeError("roll back 'other' only")
        # The default database committed its own delete.
        assert await SsParent.objects.count() == 0
        assert await SsChild.objects.count() == 0

    async def test_inside_an_enclosing_transaction_on_the_same_database(self, db):
        with pytest.raises(RuntimeError):
            async with atomic():
                parent = await SsParent.objects.create(name="p")
                await SsChild.objects.create(name="c", parent=parent)
                assert await SsParent.objects.all().delete() == 2
                raise RuntimeError("undo everything")
        assert await SsParent.objects.count() == 0

        parent = await SsParent.objects.create(name="kept")
        with pytest.raises(RuntimeError):
            async with atomic():
                await SsParent.objects.filter(name="kept").delete()
                raise RuntimeError("undo the delete")
        assert await SsParent.objects.filter(pk=parent.pk).exists()


class TestSelectForUpdateAlias:
    async def test_transaction_on_another_database_does_not_count(self, db):
        async with atomic():
            with pytest.raises(TransactionManagementError):
                SsParent.objects.using("other").select_for_update()._validate_for_update(
                    "postgresql"
                )
        async with atomic("other"):
            with pytest.raises(TransactionManagementError):
                SsParent.objects.select_for_update()._validate_for_update("postgresql")
            SsParent.objects.using("other").select_for_update()._validate_for_update("postgresql")

    async def test_transaction_on_its_database_is_accepted(self, db):
        async with atomic():
            SsParent.objects.select_for_update()._validate_for_update("postgresql")


class TestFromDb:
    """Rows go through Model._from_db(values, alias) when it exists."""

    @pytest.fixture
    def from_db_calls(self, monkeypatch):
        calls = []

        # The model layer's own loader, looked up before the per-model patch
        # below shadows it; _from_row delegates to _from_db, so calling
        # _from_row from the fake would recurse into the fake.
        from zeeb_orm import Model

        real_from_db = getattr(Model, "_from_db", None)

        def fake_from_db(cls, values, alias=None):
            calls.append((cls, dict(values), alias))
            if real_from_db is not None:
                return real_from_db.__func__(cls, values, alias)
            instance = cls._from_row(SimpleNamespace(_mapping=dict(values)))
            instance._state.persisted = True
            instance._state.db_alias = alias
            return instance

        for model in MODELS:
            monkeypatch.setattr(model, "_from_db", classmethod(fake_from_db), raising=False)
        return calls

    async def test_plain_rows(self, db, from_db_calls):
        await SsParent.objects.create(name="p")
        rows = await SsParent.objects.all()
        assert [r.name for r in rows] == ["p"]
        assert [(cls, alias) for cls, _values, alias in from_db_calls] == [(SsParent, None)]

    async def test_alias_is_passed(self, db, from_db_calls):
        await SsParent.objects.using("other").create(name="o")
        rows = await SsParent.objects.using("other").all()
        assert rows[0]._state.db_alias == "other"
        assert from_db_calls[-1][2] == "other"

    async def test_select_related_objects(self, db, from_db_calls):
        parent = await SsParent.objects.create(name="p")
        await SsChild.objects.create(name="c", parent=parent)
        from_db_calls.clear()
        child = (await SsChild.objects.select_related("parent"))[0]
        assert child.parent.name == "p"
        loaded = {cls for cls, _values, _alias in from_db_calls}
        assert loaded == {SsChild, SsParent}
        parent_values = next(v for cls, v, _a in from_db_calls if cls is SsParent)
        assert parent_values["name"] == "p"

    async def test_raw_rows(self, db, from_db_calls):
        await SsParent.objects.create(name="p")
        from_db_calls.clear()
        rows = await SsParent.objects.raw("SELECT * FROM ss_parents WHERE name = ?", ["p"])
        assert [r.name for r in rows] == ["p"]
        assert [cls for cls, _v, _a in from_db_calls] == [SsParent]
