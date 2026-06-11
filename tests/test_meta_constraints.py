"""Tests that Meta.unique_together / index_together / indexes / constraints
are emitted into the SQLAlchemy table (DDL), not just parsed.
"""

import pytest
from sqlalchemy import CheckConstraint as SACheckConstraint
from sqlalchemy import UniqueConstraint as SAUniqueConstraint
from sqlalchemy.schema import CreateTable

from zeeb_orm import (
    Model,
    close_all_connections,
    configure,
    fields,
    setup_database,
)
from zeeb_orm.models.options import CheckConstraint, Index, UniqueConstraint


class McTenant(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "mc_tenants"


class McMember(Model):
    email = fields.EmailField()
    tenant = fields.ForeignKey(McTenant, related_name="members")
    age = fields.IntegerField(null=True)
    status = fields.CharField(max_length=20, default="active")

    class Meta:
        table_name = "mc_members"
        unique_together = [("email", "tenant")]
        index_together = [("status", "age")]
        indexes = [Index(fields=["status"], name="mc_status_idx")]
        constraints = [
            UniqueConstraint(fields=["email", "status"], name="uq_mc_email_status"),
            CheckConstraint(check="age >= 0", name="ck_mc_age_positive"),
        ]


MODELS = (McTenant, McMember)


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
    db = await setup_database("sqlite+aiosqlite:///:memory:")
    for model in MODELS:
        model._get_table()
    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


class TestMetaConstraintEmission:
    def test_unique_together_emitted(self, db):
        table = McMember._get_table()
        uniques = [c for c in table.constraints if isinstance(c, SAUniqueConstraint)]
        names = {c.name for c in uniques}
        assert "uq_mc_members_email_tenant_id" in names
        cols = {
            c.name: [col.name for col in c.columns] for c in uniques
        }
        assert cols["uq_mc_members_email_tenant_id"] == ["email", "tenant_id"]

    def test_named_unique_constraint_emitted(self, db):
        table = McMember._get_table()
        names = {
            c.name for c in table.constraints if isinstance(c, SAUniqueConstraint)
        }
        assert "uq_mc_email_status" in names

    def test_check_constraint_emitted(self, db):
        table = McMember._get_table()
        checks = [c for c in table.constraints if isinstance(c, SACheckConstraint)]
        assert any(c.name == "ck_mc_age_positive" for c in checks)

    def test_indexes_emitted(self, db):
        table = McMember._get_table()
        index_names = {ix.name for ix in table.indexes}
        assert "mc_status_idx" in index_names
        assert "ix_mc_members_status_age" in index_names

    def test_ddl_contains_constraints(self, db):
        table = McMember._get_table()
        ddl = str(CreateTable(table).compile(dialect=db.get_engine().dialect))
        assert "UNIQUE (email, tenant_id)" in ddl or "uq_mc_members_email_tenant_id" in ddl
        assert "age >= 0" in ddl

    async def test_unique_together_enforced_in_db(self, db):
        tenant = await McTenant.objects.create(name="acme")
        await McMember.objects.create(email="a@x.com", tenant=tenant, status="s1")
        import sqlalchemy.exc

        with pytest.raises(sqlalchemy.exc.IntegrityError):
            await McMember.objects.create(email="a@x.com", tenant=tenant, status="s2")
