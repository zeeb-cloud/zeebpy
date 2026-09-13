"""Tests for AutoField / BigAutoField DDL across dialects.

``autoincrement`` used to live only in ``Field.to_mapped_column()``, which the
DDL path never calls, and ``BigAutoField`` rendered as ``BIGINT PRIMARY KEY``
on SQLite — not a rowid alias, so every INSERT that omitted the id failed with
``NOT NULL constraint failed``.
"""

import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.schema import CreateTable

from zeeb_orm import (
    Model,
    close_all_connections,
    configure,
    fields,
    setup_database,
)
from zeeb_orm.migrations.operations import _repr_column, _repr_sa_type


class ApBig(Model):
    id = fields.BigAutoField()
    name = fields.CharField(max_length=20)

    class Meta:
        table_name = "ap_big"


class ApAuto(Model):
    id = fields.AutoField()
    name = fields.CharField(max_length=20)

    class Meta:
        table_name = "ap_auto"


class ApChild(Model):
    """UUID PK of its own, FK pointing at a BigAutoField PK."""

    parent = fields.ForeignKey(ApBig, related_name="children")

    class Meta:
        table_name = "ap_child"


MODELS = (ApBig, ApAuto, ApChild)


def _ddl(model, dialect):
    return str(CreateTable(model._get_table()).compile(dialect=dialect))


@pytest.fixture
def tables():
    """Rebuild all three tables in a clean metadata.

    ``ap_child``'s ForeignKey can only resolve while ``ap_big`` is registered
    in the same metadata, and sibling test modules clear it in their own
    fixtures — so the tables must be built together, per test.
    """
    from zeeb_orm.models.base import metadata

    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()
    for model in MODELS:
        model._get_table()
    yield
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None


@pytest.fixture
async def db(tables):
    from zeeb_orm.conf.settings import Settings

    Settings.reset()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    Settings.reset()


class TestAutoPkDDL:
    def test_big_auto_field_is_integer_on_sqlite(self, tables):
        # Only INTEGER PRIMARY KEY aliases SQLite's rowid and auto-assigns.
        ddl = _ddl(ApBig, sqlite.dialect())
        assert "id INTEGER NOT NULL" in ddl
        assert "BIGINT" not in ddl

    def test_big_auto_field_stays_64_bit_on_postgres_and_mysql(self, tables):
        assert "id BIGSERIAL NOT NULL" in _ddl(ApBig, postgresql.dialect())
        assert "id BIGINT NOT NULL AUTO_INCREMENT" in _ddl(ApBig, mysql.dialect())

    def test_auto_field_unchanged(self, tables):
        assert "id INTEGER NOT NULL" in _ddl(ApAuto, sqlite.dialect())
        assert "id SERIAL NOT NULL" in _ddl(ApAuto, postgresql.dialect())
        assert "id INTEGER NOT NULL AUTO_INCREMENT" in _ddl(ApAuto, mysql.dialect())

    def test_autoincrement_is_requested_explicitly(self, tables):
        # SQLAlchemy's "auto" default declines for composite / FK-bearing /
        # server-defaulted primary keys, so it must be explicit.
        assert ApBig._get_table().c.id.autoincrement is True
        assert ApAuto._get_table().c.id.autoincrement is True

    def test_fk_inherits_the_dialect_variant_from_the_target_pk(self, tables):
        assert "parent_id INTEGER NOT NULL" in _ddl(ApChild, sqlite.dialect())
        assert "parent_id BIGINT NOT NULL" in _ddl(ApChild, postgresql.dialect())


class TestAutoPkInserts:
    @pytest.mark.asyncio
    async def test_big_auto_field_assigns_ids(self, db):
        first = await ApBig.objects.create(name="first")
        second = await ApBig.objects.create(name="second")
        assert first.pk is not None
        assert second.pk == first.pk + 1

    @pytest.mark.asyncio
    async def test_auto_field_assigns_ids(self, db):
        assert (await ApAuto.objects.create(name="first")).pk is not None

    @pytest.mark.asyncio
    async def test_fk_to_big_auto_field_round_trips(self, db):
        parent = await ApBig.objects.create(name="parent")
        child = await ApChild.objects.create(parent=parent)
        assert child.parent_id == parent.pk
        assert await ApChild.objects.filter(parent=parent).count() == 1


class TestMigrationWriterRoundTrip:
    """A generated migration must not recreate the broken BIGINT PK."""

    def test_variant_survives_into_the_migration_file(self, tables):
        rendered = _repr_column(ApBig._get_table().c.id)
        assert "sa.BigInteger().with_variant(sa.Integer(), 'sqlite')" in rendered
        assert "autoincrement=True" in rendered

    def test_fk_column_variant_survives(self, tables):
        rendered = _repr_column(ApChild._get_table().c.parent_id)
        assert "sa.BigInteger().with_variant(sa.Integer(), 'sqlite')" in rendered
        assert "sa.ForeignKey('ap_big.id'" in rendered

    def test_plain_types_are_unchanged(self):
        from sqlalchemy import Integer, Numeric, String

        assert _repr_sa_type(Integer()) == "sa.Integer()"
        assert _repr_sa_type(String(20)) == "sa.String(20)"
        assert _repr_sa_type(Numeric(10, 2)) == "sa.Numeric(10, 2)"

    def test_rendered_column_is_executable_python(self, tables):
        import sqlalchemy as sa

        col = eval(_repr_column(ApBig._get_table().c.id), {"sa": sa})  # noqa: S307
        assert col.autoincrement is True
        assert (
            col.type.compile(dialect=sqlite.dialect()) == "INTEGER"
        ), "variant lost on round-trip"
