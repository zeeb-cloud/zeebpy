"""Schema changes on existing tables: makemigrations, migrate, no drift, rollback.

Each class pins a defect that used to break a project:

- adding a foreign key or a ``unique=True`` field, or removing a foreign key,
  crashed ``migrate`` on SQLite — and, because change detection replays the
  migrations on SQLite, wedged every later ``makemigrations`` on any database
- changing ``Meta.table_name`` dropped the table (and its rows) and created
  an empty one, naming the dropped model after a guess from its table name
- a NOT NULL ``AddField`` with nothing to fill existing rows was written,
  could never be applied, and wedged later ``makemigrations``
- ``RemoveConstraint`` could not be rolled back
- a model and the foreign key pointing at it, deleted together, came out as
  ``DeleteModel`` before ``RemoveField``
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from zeeb_orm import Model, fields
from zeeb_orm.migrations import executor
from zeeb_orm.migrations.autodetector import NotNullFieldError, order_operations
from zeeb_orm.migrations.autogen import make_migration
from zeeb_orm.migrations.executor import list_migration_files, load_migration
from zeeb_orm.migrations.operations import (
    AddField,
    AlterModelTable,
    CreateModel,
    DeleteModel,
    RemoveConstraint,
    RemoveField,
)
from zeeb_orm.migrations.optimizer import optimize
from zeeb_orm.models.base import metadata


@pytest.fixture()
def mig_dir(tmp_path):
    path = tmp_path / "migrations"
    path.mkdir()
    return path


@pytest.fixture()
def db(tmp_path):
    return f"sqlite:///{tmp_path / 'schema.sqlite3'}"


def _drop_from_metadata(*tables: str) -> None:
    for name in tables:
        if name in metadata.tables:
            metadata.remove(metadata.tables[name])


def _make(mig_dir) -> list[str]:
    """makemigrations; the descriptions of what it wrote."""
    draft = make_migration(mig_dir)
    return [op.describe() for op in draft.operations]


def _migrate(mig_dir, db, **kwargs):
    return executor.migrate(database_url=db, migrations_dir=mig_dir, **kwargs)


def _rollback(mig_dir, db, steps=1):
    return executor.rollback(steps, database_url=db, migrations_dir=mig_dir)


def _sql(db, statement, **params):
    engine = sa.create_engine(db)
    try:
        with engine.begin() as conn:
            result = conn.execute(sa.text(statement), params)
            return result.fetchall() if result.returns_rows else None
    finally:
        engine.dispose()


def _inspect(db, what, table):
    engine = sa.create_engine(db)
    try:
        with engine.connect() as conn:
            return getattr(sa.inspect(conn), what)(table)
    finally:
        engine.dispose()


def _tables(db):
    engine = sa.create_engine(db)
    try:
        with engine.connect() as conn:
            return set(sa.inspect(conn).get_table_names())
    finally:
        engine.dispose()


def _last_ops(mig_dir):
    return list(load_migration(list_migration_files(mig_dir)[-1][1]).operations)


def _author_and_book(*, with_owner: bool):
    """``ScAuthor`` and ``ScBook`` (optionally with ``owner -> ScAuthor``)."""
    _drop_from_metadata("sc_author", "sc_book")

    class ScAuthor(Model):
        name = fields.CharField(max_length=50)

        class Meta:
            table_name = "sc_author"

    body = {
        "title": fields.CharField(max_length=50),
        "Meta": type("Meta", (), {"table_name": "sc_book"}),
        "__module__": __name__,
    }
    if with_owner:
        body["owner"] = fields.ForeignKey(
            "ScAuthor", on_delete="SET_NULL", null=True, related_name="books"
        )
    book = type("ScBook", (Model,), body)
    ScAuthor._get_table()
    book._get_table()
    return ScAuthor, book


def _insert_book(db, title="t"):
    import uuid

    _sql(db, "INSERT INTO sc_book (id, title) VALUES (:id, :title)",
         id=uuid.uuid4().hex, title=title)


# ---------------------------------------------------------------------------
# 1. Foreign keys and unique fields on an existing table
# ---------------------------------------------------------------------------


class TestAddAndRemoveConstrainedColumns:
    def test_adding_a_nullable_foreign_key_to_a_table_with_rows(self, mig_dir, db):
        _author_and_book(with_owner=False)
        _make(mig_dir)
        _migrate(mig_dir, db)
        _insert_book(db)

        _author_and_book(with_owner=True)
        assert _make(mig_dir) == ["Add field owner_id to ScBook"]
        _migrate(mig_dir, db)

        fks = _inspect(db, "get_foreign_keys", "sc_book")
        assert [(fk["constrained_columns"], fk["referred_table"]) for fk in fks] == [
            (["owner_id"], "sc_author")
        ]
        assert fks[0]["options"].get("ondelete") == "SET NULL"
        assert _sql(db, "SELECT title FROM sc_book") == [("t",)]
        assert make_migration(mig_dir).changed is False

        _rollback(mig_dir, db)
        assert "owner_id" not in {c["name"] for c in _inspect(db, "get_columns", "sc_book")}
        assert _sql(db, "SELECT title FROM sc_book") == [("t",)]

    def test_adding_a_nullable_unique_field(self, mig_dir, db):
        class ScCode(Model):
            title = fields.CharField(max_length=50)

            class Meta:
                table_name = "sc_code"

        ScCode._get_table()
        _make(mig_dir)
        _migrate(mig_dir, db)
        _sql(db, "INSERT INTO sc_code (id, title) VALUES ('a1', 'x')")

        _drop_from_metadata("sc_code")

        class ScCode(Model):  # noqa: F811 — the model, changed
            title = fields.CharField(max_length=50)
            code = fields.CharField(max_length=20, unique=True, null=True)

            class Meta:
                table_name = "sc_code"

        ScCode._get_table()
        assert _make(mig_dir) == ["Add field code to ScCode"]
        _migrate(mig_dir, db)
        uniques = _inspect(db, "get_unique_constraints", "sc_code")
        assert {"name": "uq_sc_code_code", "column_names": ["code"]} in [
            {"name": u["name"], "column_names": u["column_names"]} for u in uniques
        ]
        _sql(db, "UPDATE sc_code SET code = 'c'")
        with pytest.raises(sa.exc.IntegrityError):
            _sql(db, "INSERT INTO sc_code (id, title, code) VALUES ('a2', 'y', 'c')")
        assert make_migration(mig_dir).changed is False

        _rollback(mig_dir, db)
        assert "code" not in {c["name"] for c in _inspect(db, "get_columns", "sc_code")}

    def test_removing_a_foreign_key_field(self, mig_dir, db):
        _author_and_book(with_owner=True)
        _make(mig_dir)
        _migrate(mig_dir, db)
        _insert_book(db)

        _author_and_book(with_owner=False)
        ops = _make(mig_dir)
        assert "Remove field owner_id from ScBook" in ops
        _migrate(mig_dir, db)
        assert "owner_id" not in {c["name"] for c in _inspect(db, "get_columns", "sc_book")}
        assert _inspect(db, "get_foreign_keys", "sc_book") == []
        assert _sql(db, "SELECT title FROM sc_book") == [("t",)]
        assert make_migration(mig_dir).changed is False

        _rollback(mig_dir, db)
        fks = _inspect(db, "get_foreign_keys", "sc_book")
        assert [fk["referred_table"] for fk in fks] == ["sc_author"]
        assert _sql(db, "SELECT title FROM sc_book") == [("t",)]


# ---------------------------------------------------------------------------
# 2. Meta.table_name changed: a rename, not a drop
# ---------------------------------------------------------------------------


class TestTableRename:
    def _post(self, table):
        _drop_from_metadata("sc_blogpost", "sc_posts")

        class ScPost(Model):
            title = fields.CharField(max_length=50)

            class Meta:
                table_name = table

        ScPost._get_table()

    def test_changing_the_table_name_keeps_the_rows(self, mig_dir, db):
        self._post("sc_blogpost")
        _make(mig_dir)
        _migrate(mig_dir, db)
        _sql(db, "INSERT INTO sc_blogpost (id, title) VALUES ('p1', 'kept')")

        self._post("sc_posts")
        assert _make(mig_dir) == ["Rename table sc_blogpost to sc_posts for ScPost"]
        (op,) = _last_ops(mig_dir)
        assert isinstance(op, AlterModelTable) and op.reversible

        _migrate(mig_dir, db)
        assert "sc_blogpost" not in _tables(db)
        assert _sql(db, "SELECT title FROM sc_posts") == [("kept",)]
        assert make_migration(mig_dir).changed is False

        _rollback(mig_dir, db)
        assert "sc_posts" not in _tables(db)
        assert _sql(db, "SELECT title FROM sc_blogpost") == [("kept",)]

    def test_a_renamed_table_with_a_new_column_is_one_rename_and_one_add(self, mig_dir):
        self._post("sc_blogpost")
        _make(mig_dir)
        _drop_from_metadata("sc_blogpost")

        class ScPost(Model):
            title = fields.CharField(max_length=50)
            subtitle = fields.CharField(max_length=50, null=True)

            class Meta:
                table_name = "sc_posts"

        ScPost._get_table()
        assert _make(mig_dir) == [
            "Rename table sc_blogpost to sc_posts for ScPost",
            "Add field subtitle to ScPost",
        ]

    def test_a_deleted_model_is_named_as_its_migration_named_it(self, mig_dir):
        self._post("sc_blogpost")
        _make(mig_dir)
        _drop_from_metadata("sc_blogpost")
        assert _make(mig_dir) == ["Delete model ScPost"]  # not "ScBlogpost"


# ---------------------------------------------------------------------------
# 3. NOT NULL fields need something to fill existing rows
# ---------------------------------------------------------------------------


class TestNotNullAdditions:
    def _base(self):
        _drop_from_metadata("sc_entry")

        class ScEntry(Model):
            title = fields.CharField(max_length=50)

            class Meta:
                table_name = "sc_entry"

        ScEntry._get_table()

    def _with(self, **extra):
        _drop_from_metadata("sc_entry")
        body = {
            "title": fields.CharField(max_length=50),
            "Meta": type("Meta", (), {"table_name": "sc_entry"}),
            "__module__": __name__,
            **extra,
        }
        type("ScEntry", (Model,), body)._get_table()

    def _seeded(self, mig_dir, db):
        self._base()
        _make(mig_dir)
        _migrate(mig_dir, db)
        _sql(db, "INSERT INTO sc_entry (id, title) VALUES ('e1', 'old row')")

    def test_auto_now_add_backfills_existing_rows_with_now(self, mig_dir, db):
        self._seeded(mig_dir, db)
        self._with(created_at=fields.DateTimeField(auto_now_add=True))
        assert _make(mig_dir) == ["Add field created_at to ScEntry"]
        (op,) = _last_ops(mig_dir)
        assert isinstance(op, AddField) and op.backfill is not None

        _migrate(mig_dir, db)
        [(value,)] = _sql(db, "SELECT created_at FROM sc_entry")
        assert value  # filled, not NULL
        column = {c["name"]: c for c in _inspect(db, "get_columns", "sc_entry")}["created_at"]
        assert column["nullable"] is False
        assert column["default"] is None  # the backfill is not left behind
        assert make_migration(mig_dir).changed is False

        _rollback(mig_dir, db)
        assert "created_at" not in {c["name"] for c in _inspect(db, "get_columns", "sc_entry")}

    def test_auto_now_date_field_backfills_too(self, mig_dir, db):
        self._seeded(mig_dir, db)
        self._with(day=fields.DateField(auto_now=True))
        _make(mig_dir)
        _migrate(mig_dir, db)
        [(value,)] = _sql(db, "SELECT day FROM sc_entry")
        assert value
        assert make_migration(mig_dir).changed is False

    def test_a_not_null_field_without_a_default_is_refused_before_writing(self, mig_dir, db):
        self._seeded(mig_dir, db)
        before = list_migration_files(mig_dir)
        self._with(rank=fields.IntegerField())
        with pytest.raises(NotNullFieldError, match=r"ScEntry\.rank.*null=True") as info:
            make_migration(mig_dir)
        assert info.value.fields == ["ScEntry.rank"]
        assert list_migration_files(mig_dir) == before

    def test_a_static_default_still_works(self, mig_dir, db):
        self._seeded(mig_dir, db)
        self._with(status=fields.CharField(max_length=10, default="draft"))
        assert _make(mig_dir) == ["Add field status to ScEntry"]
        _migrate(mig_dir, db)
        assert _sql(db, "SELECT status FROM sc_entry") == [("draft",)]
        assert make_migration(mig_dir).changed is False
        _rollback(mig_dir, db)


# ---------------------------------------------------------------------------
# 4. RemoveConstraint is reversible
# ---------------------------------------------------------------------------


class TestRemoveConstraintRollback:
    def _slugged(self, unique: bool):
        _drop_from_metadata("sc_slugged")
        body = {
            "slug": fields.CharField(max_length=20, unique=unique),
            "Meta": type("Meta", (), {"table_name": "sc_slugged"}),
            "__module__": __name__,
        }
        type("ScSlugged", (Model,), body)._get_table()

    def test_dropping_unique_true_rolls_back(self, mig_dir, db):
        self._slugged(unique=True)
        _make(mig_dir)
        _migrate(mig_dir, db)
        _sql(db, "INSERT INTO sc_slugged (id, slug) VALUES ('s1', 'a')")

        self._slugged(unique=False)
        assert _make(mig_dir) == ["Remove constraint uq_sc_slugged_slug from ScSlugged"]
        (op,) = _last_ops(mig_dir)
        assert isinstance(op, RemoveConstraint) and op.reversible
        _migrate(mig_dir, db)
        assert _inspect(db, "get_unique_constraints", "sc_slugged") == []
        assert make_migration(mig_dir).changed is False

        assert _rollback(mig_dir, db)
        uniques = _inspect(db, "get_unique_constraints", "sc_slugged")
        assert [(u["name"], u["column_names"]) for u in uniques] == [
            ("uq_sc_slugged_slug", ["slug"])
        ]
        assert _sql(db, "SELECT slug FROM sc_slugged") == [("a",)]

    def test_removing_a_unique_field_rolls_back(self, mig_dir, db):
        _drop_from_metadata("sc_slugged")
        body = {
            "title": fields.CharField(max_length=20),
            "code": fields.CharField(max_length=20, unique=True, null=True),
            "Meta": type("Meta", (), {"table_name": "sc_slugged"}),
            "__module__": __name__,
        }
        type("ScSlugged", (Model,), body)._get_table()
        _make(mig_dir)
        _migrate(mig_dir, db)

        _drop_from_metadata("sc_slugged")
        body.pop("code")
        body["title"] = fields.CharField(max_length=20)
        type("ScSlugged", (Model,), body)._get_table()
        ops = _make(mig_dir)
        assert "Remove field code from ScSlugged" in ops
        _migrate(mig_dir, db)
        assert _rollback(mig_dir, db)
        uniques = _inspect(db, "get_unique_constraints", "sc_slugged")
        assert [u["column_names"] for u in uniques] == [["code"]]

    def test_the_constraint_survives_the_file(self, mig_dir):
        from zeeb_orm.migrations.writer import write_migration

        op = RemoveConstraint(
            model_name="M", table="t", name="uq_t_a",
            constraint=sa.UniqueConstraint("a", "b", name="uq_t_a"),
        )
        path = write_migration(mig_dir, [op], name="drop")
        (loaded,) = load_migration(path).operations
        assert loaded.reversible
        assert list(loaded.constraint._pending_colargs) == ["a", "b"]
        # A file written before the constraint was recorded still refuses.
        assert RemoveConstraint(model_name="M", table="t", name="uq").reversible is False


# ---------------------------------------------------------------------------
# 5. A model and the foreign key pointing at it, deleted together
# ---------------------------------------------------------------------------


class TestDeletionOrder:
    def test_the_foreign_key_goes_before_the_table_it_points_at(self, mig_dir, db):
        _author_and_book(with_owner=True)
        _make(mig_dir)
        _migrate(mig_dir, db)
        _insert_book(db)

        _drop_from_metadata("sc_author", "sc_book")

        class ScBook(Model):
            title = fields.CharField(max_length=50)

            class Meta:
                table_name = "sc_book"

        ScBook._get_table()
        ops = _last_ops_after(mig_dir)
        names = [op.describe() for op in ops]
        assert names.index("Remove field owner_id from ScBook") < names.index(
            "Delete model ScAuthor"
        )
        # The optimizer (squashmigrations) keeps that order.
        assert [op.describe() for op in optimize(ops)] == names

        _migrate(mig_dir, db)
        assert "sc_author" not in _tables(db)
        _rollback(mig_dir, db)
        assert "sc_author" in _tables(db)
        fks = _inspect(db, "get_foreign_keys", "sc_book")
        assert [fk["referred_table"] for fk in fks] == ["sc_author"]

    def test_tables_referencing_each_other_in_a_chain_drop_leaf_first(self):
        def fk_col(target):
            return sa.Column("ref_id", sa.Integer(), sa.ForeignKey(f"{target}.id"))

        def dropped(table, *columns):
            return DeleteModel(
                name=table, table=table,
                columns=[sa.Column("id", sa.Integer()), *columns], primary_key=["id"],
            )

        ops = [
            dropped("sc_a"),
            dropped("sc_b", fk_col("sc_a")),
            dropped("sc_c", fk_col("sc_b")),
            RemoveField(model_name="D", table="sc_d", name="ref_id", field=fk_col("sc_c")),
        ]
        assert [op.table for op in order_operations(ops)] == ["sc_d", "sc_c", "sc_b", "sc_a"]

    def test_a_created_table_goes_before_the_key_that_references_it(self):
        add = AddField(
            model_name="B", table="sc_b", name="a_id",
            column=sa.Column("a_id", sa.Integer(), sa.ForeignKey("sc_a.id"), nullable=True),
        )
        create = CreateModel(
            name="A", table="sc_a", columns=[sa.Column("id", sa.Integer())], primary_key=["id"]
        )
        assert order_operations([add, create]) == [create, add]


def _last_ops_after(mig_dir):
    make_migration(mig_dir)
    return _last_ops(mig_dir)
