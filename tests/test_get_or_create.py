"""get_or_create / update_or_create: atomic, race-safe, lookups not assigned.

Both used to run a plain get() followed by a plain create(): a concurrent
caller creating the same row in between surfaced as an IntegrityError, the
failed insert was not isolated in a savepoint, and lookups such as
``name__iexact`` were passed to the model constructor.
"""

import pytest
from sqlalchemy import event

from zeeb_orm import IntegrityError, Model, close_all_connections, configure, fields, setup_database
from zeeb_orm.db.connection import atomic
from zeeb_orm.query.queryset import QuerySet


class GcAccount(Model):
    email = fields.CharField(max_length=80, unique=True)
    name = fields.CharField(max_length=80, null=True)
    note = fields.CharField(max_length=80, null=True)
    hits = fields.IntegerField(default=0)
    handle = fields.CharField(max_length=20, unique=True, null=True)

    class Meta:
        table_name = "gc_accounts"


MODELS = (GcAccount,)


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
def race(monkeypatch):
    """Make the first get() miss while a 'concurrent' caller inserts the row."""
    real_get = QuerySet.get
    state = {"calls": 0, "row": None}

    async def racing_get(self, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1 and state["row"] is not None:
            await GcAccount.objects.create(**state["row"])
            raise self.model.DoesNotExist("not there yet")
        return await real_get(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "get", racing_get)
    return state


class TestGetOrCreate:
    async def test_lookups_with_double_underscore_are_not_assigned(self, db):
        obj, created = await GcAccount.objects.get_or_create(
            email__iexact="ANN@x.io", defaults={"email": "ann@x.io", "name": "Ann"}
        )
        assert created and obj.email == "ann@x.io"
        again, created = await GcAccount.objects.get_or_create(
            email__iexact="ANN@x.io", defaults={"email": "ann@x.io"}
        )
        assert not created and again.pk == obj.pk

    async def test_callable_defaults_are_called(self, db):
        obj, created = await GcAccount.objects.get_or_create(
            email="c@x.io", defaults={"name": lambda: "computed"}
        )
        assert created and obj.name == "computed"

    async def test_concurrent_create_returns_the_existing_row(self, db, race):
        race["row"] = {"email": "race@x.io", "name": "winner"}
        obj, created = await GcAccount.objects.get_or_create(
            email="race@x.io", defaults={"name": "loser"}
        )
        assert created is False
        assert obj.name == "winner"
        assert await GcAccount.objects.count() == 1

    async def test_failed_create_does_not_doom_the_enclosing_transaction(self, db, race):
        race["row"] = {"email": "race@x.io", "name": "winner"}
        async with atomic():
            obj, created = await GcAccount.objects.get_or_create(email="race@x.io")
            assert not created
            await GcAccount.objects.create(email="after@x.io")
        emails = sorted(await GcAccount.objects.values_list("email", flat=True))
        assert emails == ["after@x.io", "race@x.io"]

    async def test_integrity_error_without_a_matching_row_is_raised(self, db):
        await GcAccount.objects.create(email="a@x.io", handle="taken")
        with pytest.raises(IntegrityError):
            await GcAccount.objects.get_or_create(email="b@x.io", defaults={"handle": "taken"})
        assert await GcAccount.objects.count() == 1


class TestUpdateOrCreate:
    async def test_updates_only_the_defaults_fields(self, db):
        acct = await GcAccount.objects.create(email="u@x.io", name="old", note="keep")
        # Another writer changes a column the update does not mention.
        await GcAccount.objects.filter(pk=acct.pk).update(note="changed elsewhere")

        seen: list[str] = []

        def record(conn, cursor, statement, parameters, context, executemany):
            seen.append(statement)

        engine = db.get_engine().sync_engine
        event.listen(engine, "before_cursor_execute", record)
        try:
            obj, created = await GcAccount.objects.update_or_create(
                email="u@x.io", defaults={"name": "new"}
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert not created and obj.name == "new"
        update = next(s for s in seen if s.lstrip().upper().startswith("UPDATE"))
        assert "name" in update and "note" not in update
        stored = await GcAccount.objects.get(pk=acct.pk)
        assert (stored.name, stored.note) == ("new", "changed elsewhere")

    async def test_creates_with_create_defaults(self, db):
        obj, created = await GcAccount.objects.update_or_create(
            email="n@x.io", defaults={"hits": 1}, create_defaults={"hits": 100}
        )
        assert created and obj.hits == 100
        obj, created = await GcAccount.objects.update_or_create(
            email="n@x.io", defaults={"hits": 1}, create_defaults={"hits": 100}
        )
        assert not created and obj.hits == 1

    async def test_concurrent_create_is_updated_instead(self, db, race):
        race["row"] = {"email": "race@x.io", "hits": 1}
        obj, created = await GcAccount.objects.update_or_create(
            email="race@x.io", defaults={"hits": 7}
        )
        assert not created
        assert (await GcAccount.objects.get(email="race@x.io")).hits == 7
        assert await GcAccount.objects.count() == 1

    async def test_manager_forwards_create_defaults(self, db):
        obj, created = await GcAccount.objects.update_or_create(
            defaults={"name": "upd"}, create_defaults={"name": "made"}, email="m@x.io"
        )
        assert created and obj.name == "made"


class TestEnclosingTransaction:
    """A rolled-back enclosing atomic() undoes what these methods wrote.

    On SQLite a savepoint opened as the first statement of the enclosing
    transaction commits on RELEASE (pysqlite's transaction handling), so the
    methods join the enclosing transaction there instead.
    """

    async def test_get_or_create_is_rolled_back_with_the_enclosing_block(self, db):
        with pytest.raises(RuntimeError):
            async with atomic():
                _obj, created = await GcAccount.objects.get_or_create(email="t@x.io")
                assert created
                raise RuntimeError("roll back")
        assert await GcAccount.objects.count() == 0

    async def test_update_or_create_is_rolled_back_with_the_enclosing_block(self, db):
        await GcAccount.objects.create(email="u@x.io", hits=1)
        with pytest.raises(RuntimeError):
            async with atomic():
                await GcAccount.objects.update_or_create(email="u@x.io", defaults={"hits": 9})
                await GcAccount.objects.update_or_create(email="new@x.io")
                raise RuntimeError("roll back")
        assert await GcAccount.objects.count() == 1
        assert (await GcAccount.objects.get(email="u@x.io")).hits == 1
