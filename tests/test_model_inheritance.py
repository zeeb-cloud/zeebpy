"""Tests for model inheritance: Meta options and the inherited primary key.

Two defects motivated these:

* ``ModelBase.__new__`` pops ``Meta`` from every processed namespace, so a
  parent's ``Meta`` was unreachable and all of its options were silently
  dropped by subclasses.
* A primary key coming from a parent set ``has_pk`` (blocking the auto-PK)
  but never ``_meta.pk``, leaving it ``None`` — which mistypes foreign keys
  pointing at the model and breaks ``obj.pk``, joins and deletes.
"""

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateTable

from zeeb_orm import (
    Model,
    close_all_connections,
    configure,
    fields,
    setup_database,
)
from zeeb_orm.models.options import Index, UniqueConstraint


class MiBase(Model):
    """Abstract base contributing a PK, a field and several Meta options."""

    id = fields.BigAutoField()
    slug = fields.CharField(max_length=40)

    class Meta:
        abstract = True
        ordering = ["-slug"]
        table_name = "mi_should_not_leak"
        unique_together = [("slug",)]
        indexes = [Index(fields=["slug"], name="mi_shared_slug_idx")]


class MiChild(MiBase):
    """Inherits everything; declares no Meta at all."""

    name = fields.CharField(max_length=40)


class MiSibling(MiBase):
    """Overrides one option; the rest is still inherited."""

    label = fields.CharField(max_length=40)

    class Meta:
        ordering = ["label"]


class MiOwnPk(MiBase):
    """An own PK must win over the inherited one."""

    code = fields.CharField(max_length=10, primary_key=True)


class MiRef(Model):
    """FK pointing at a model whose PK is inherited."""

    target = fields.ForeignKey(MiChild, related_name="refs")

    class Meta:
        table_name = "mi_refs"


MODELS = (MiChild, MiSibling, MiOwnPk, MiRef)


@pytest.fixture
def tables():
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


class TestInheritedMetaOptions:
    def test_ordering_is_inherited(self):
        assert MiChild._meta.ordering == ["-slug"]

    def test_own_meta_wins_over_inherited(self):
        assert MiSibling._meta.ordering == ["label"]

    def test_unique_together_is_inherited(self):
        assert MiChild._meta.unique_together == [("slug",)]
        assert MiSibling._meta.unique_together == [("slug",)]

    def test_table_name_is_not_inherited(self):
        # Each model owns its table; the parent's name must not leak.
        assert MiChild._meta.db_table == "mi_child"
        assert MiSibling._meta.db_table == "mi_sibling"
        assert MiChild._meta.table_name == MiChild._meta.db_table

    def test_abstract_is_not_inherited(self):
        # Otherwise AbstractBaseUser -> AbstractUser -> User leaves no
        # concrete model at all.
        assert MiBase._meta.abstract is True
        assert MiChild._meta.abstract is False
        assert MiSibling._meta.abstract is False

    def test_inherited_index_names_do_not_collide(self, tables):
        # An explicitly named index on an abstract base would be registered
        # once per sibling under the same name.
        names = sorted(
            idx.name
            for idx in MiChild._get_table().indexes | MiSibling._get_table().indexes
        )
        assert names == ["ix_mi_child_slug", "ix_mi_sibling_slug"]
        assert all(idx.name is None for idx in MiChild._meta.indexes)

    def test_parent_meta_is_not_mutated_by_a_child(self):
        assert [idx.name for idx in MiBase._meta.indexes] == ["mi_shared_slug_idx"]
        assert MiBase._meta.ordering == ["-slug"]

    def test_inherited_lists_are_not_shared(self):
        assert MiChild._meta.unique_together is not MiBase._meta.unique_together
        MiChild._meta.ordering.append("__scratch__")
        try:
            assert MiSibling._meta.ordering == ["label"]
            assert MiBase._meta.ordering == ["-slug"]
        finally:
            MiChild._meta.ordering.remove("__scratch__")

    def test_undeclared_options_keep_their_defaults(self):
        assert MiChild._meta.managed is True
        assert MiChild._meta.index_together == []
        assert MiChild._meta.constraints == []

    def test_a_declared_option_is_recorded(self):
        assert "ordering" in MiSibling._meta.declared_options
        assert "abstract" not in MiChild._meta.declared_options


class TestInheritedPrimaryKey:
    def test_inherited_pk_is_recorded(self):
        assert MiChild._meta.pk is not None
        assert isinstance(MiChild._meta.pk, fields.BigAutoField)
        assert MiChild._meta.pk_name == "id"

    def test_no_extra_auto_pk_is_injected(self):
        names = [f.name for f in MiChild._meta.local_fields]
        assert names.count("id") == 1

    def test_own_pk_overrides_the_inherited_one(self):
        assert MiOwnPk._meta.pk_name == "code"
        assert isinstance(MiOwnPk._meta.pk, fields.CharField)

    def test_own_pk_replaces_rather_than_joins_the_inherited_one(self, tables):
        # A composite (id, code) key is never what was meant, and SQLite
        # refuses it outright for an autoincrement column.
        pk_fields = [f.name for f in MiOwnPk._meta.local_fields if f.primary_key]
        assert pk_fields == ["code"]
        pk_columns = [c.name for c in MiOwnPk._get_table().primary_key.columns]
        assert pk_columns == ["code"]
        # Non-PK fields from the base are still inherited
        assert "slug" in [f.name for f in MiOwnPk._meta.local_fields]

    def test_fk_column_type_follows_the_inherited_pk(self, tables):
        # With _meta.pk None, sa_builder silently fell back to Integer().
        column = MiRef._get_table().c.target_id
        assert "BIGINT" in str(column.type)
        assert column.type.compile(dialect=postgresql.dialect()) == "BIGINT"
        assert column.type.compile(dialect=sqlite.dialect()) == "INTEGER"

    def test_child_table_has_the_inherited_pk(self, tables):
        ddl = str(CreateTable(MiChild._get_table()).compile(dialect=sqlite.dialect()))
        assert "PRIMARY KEY (id)" in ddl

    @pytest.mark.asyncio
    async def test_pk_accessor_works(self, db):
        child = await MiChild.objects.create(slug="a", name="Alpha")
        assert child.pk == child.id
        assert child.pk is not None

    @pytest.mark.asyncio
    async def test_ordering_from_the_abstract_base_is_applied(self, db):
        await MiChild.objects.create(slug="a", name="Alpha")
        await MiChild.objects.create(slug="c", name="Gamma")
        await MiChild.objects.create(slug="b", name="Beta")
        assert [c.slug for c in await MiChild.objects.all()] == ["c", "b", "a"]

    @pytest.mark.asyncio
    async def test_fk_traversal_and_delete_work(self, db):
        child = await MiChild.objects.create(slug="a", name="Alpha")
        await MiRef.objects.create(target=child)
        assert await MiRef.objects.filter(target__slug="a").count() == 1
        await child.delete()
        assert await MiRef.objects.count() == 0
