"""Tests for the migration system.

Covers:
- Autodetector: compares models against migration state, not the database
- No duplicate migrations when running makemigrations twice
- New field detection after initial migration exists
- _register_models warns on import failures
- New operations: RenameModel, RenameField, AddConstraint, RemoveConstraint
- RemoveField reversibility
- AlterField reversibility
- Migration.atomic transaction wrapping
- Migration pre/post migrate hooks
- Migration optimizer
- squashmigrations
- makemigrations --check / --dry-run
- migrate --plan / --fake-initial
"""

import shutil
import textwrap
import warnings
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from zeeb_orm.models.base import metadata, _model_registry, Model
from zeeb_orm.models.fields import CharField, TextField, IntegerField
from zeeb_orm.migrations.autodetector import detect_changes
from zeeb_orm.migrations.writer import write_migration
from zeeb_orm.migrations.executor import list_migration_files
from zeeb_orm.migrations.operations import (
    AddConstraint,
    AddField,
    AlterField,
    CreateModel,
    RemoveField,
    RemoveConstraint,
    RenameField,
    RenameModel,
)


@pytest.fixture()
def tmp_migrations_dir(tmp_path):
    """Create a temporary migrations directory."""
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    return mig_dir


@pytest.fixture(autouse=True)
def _clean_metadata():
    """Remove test tables from global metadata after each test."""
    tables_before = set(metadata.tables.keys())
    yield
    tables_after = set(metadata.tables.keys())
    for name in tables_after - tables_before:
        metadata.remove(metadata.tables[name])


def _register_test_model(table_name: str, columns: list) -> None:
    """Register a table directly in the global metadata for testing."""
    if table_name in metadata.tables:
        metadata.remove(metadata.tables[table_name])
    sa.Table(table_name, metadata, *columns)


# ---------------------------------------------------------------------------
# Autodetector tests
# ---------------------------------------------------------------------------


class TestDetectChanges:
    """Test that detect_changes compares against migration state."""

    def test_detects_new_model(self, tmp_migrations_dir):
        """A model not covered by any migration should be detected."""
        _register_test_model("test_items", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))

        create_ops = [o for o in ops if isinstance(o, CreateModel)]
        tables = [o.table for o in create_ops]
        assert "test_items" in tables

    def test_no_changes_after_migration_exists(self, tmp_migrations_dir):
        """If a migration already covers the model, detect_changes returns nothing for it."""
        _register_test_model("test_widgets", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
        ])

        # Create a migration that covers this model
        table = metadata.tables["test_widgets"]
        create_op = CreateModel(
            name="Widget",
            table="test_widgets",
            columns=[col.copy() for col in table.columns],
            primary_key=["id"],
        )
        write_migration(
            tmp_migrations_dir,
            operations=[create_op],
            name="initial",
            initial=True,
        )

        # Now detect_changes should find no changes for test_widgets
        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))

        create_tables = [o.table for o in ops if isinstance(o, CreateModel)]
        assert "test_widgets" not in create_tables

    def test_detects_new_field_after_initial(self, tmp_migrations_dir):
        """Adding a field after initial migration should be detected."""
        # First, create a migration with just id and title
        create_op = CreateModel(
            name="Article",
            table="test_articles",
            columns=[
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("title", sa.String(200), nullable=False),
            ],
            primary_key=["id"],
        )
        write_migration(
            tmp_migrations_dir,
            operations=[create_op],
            name="initial",
            initial=True,
        )

        # Now register a model with an additional 'body' column
        _register_test_model("test_articles", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("body", sa.Text(), nullable=True),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))

        add_ops = [o for o in ops if isinstance(o, AddField)]
        added_names = [(o.table, o.name) for o in add_ops]
        assert ("test_articles", "body") in added_names

    def test_running_makemigrations_twice_no_duplicate(self, tmp_migrations_dir):
        """Running detect_changes + write_migration twice should not create duplicates."""
        _register_test_model("test_things", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("value", sa.String(50), nullable=False),
        ])

        # First run: should detect changes
        ops1 = detect_changes(migrations_dir=str(tmp_migrations_dir))
        assert len(ops1) > 0

        # Write the migration
        write_migration(tmp_migrations_dir, operations=ops1, name="initial", initial=True)

        # Second run: should detect NO changes
        ops2 = detect_changes(migrations_dir=str(tmp_migrations_dir))
        create_tables = [o.table for o in ops2 if isinstance(o, CreateModel)]
        assert "test_things" not in create_tables


class TestAutodetectorAlterField:
    """Column-level diffs (type/nullable/remove) are detected and reversible.

    Regression guard: Alembic wraps these diffs in a list, which the converter
    used to drop silently — so AlterField autogeneration never fired.
    """

    def _write_initial(self, mig_dir, table, columns):
        write_migration(
            mig_dir,
            operations=[CreateModel(
                name="T", table=table,
                columns=columns, primary_key=["id"],
            )],
            name="initial",
            initial=True,
        )

    def test_detects_type_change(self, tmp_migrations_dir):
        self._write_initial(tmp_migrations_dir, "test_alt_type", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(50), nullable=False),
        ])
        _register_test_model("test_alt_type", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=False),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))

        alters = [o for o in ops if isinstance(o, AlterField) and o.column_type is not None]
        assert len(alters) == 1
        assert alters[0].old_column_type is not None
        assert alters[0].reversible is True

    def test_detects_nullable_change(self, tmp_migrations_dir):
        self._write_initial(tmp_migrations_dir, "test_alt_null", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(50), nullable=False),
        ])
        _register_test_model("test_alt_null", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(50), nullable=True),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))

        alters = [o for o in ops if isinstance(o, AlterField) and o.nullable is not None]
        assert len(alters) == 1
        assert alters[0].nullable is True
        assert alters[0].old_nullable is False
        assert alters[0].reversible is True

    def test_detects_removed_column_reversible(self, tmp_migrations_dir):
        self._write_initial(tmp_migrations_dir, "test_alt_rm", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("body", sa.Text(), nullable=True),
        ])
        _register_test_model("test_alt_rm", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))

        removes = [o for o in ops if isinstance(o, RemoveField)]
        assert len(removes) == 1
        assert removes[0].name == "body"
        assert removes[0].field is not None
        assert removes[0].reversible is True

    def test_alter_field_converges(self, tmp_migrations_dir):
        """detect -> write -> detect returns no further changes (F3 guard)."""
        self._write_initial(tmp_migrations_dir, "test_alt_conv", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(50), nullable=False),
        ])
        _register_test_model("test_alt_conv", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.Text(), nullable=True),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))
        assert ops, "expected an AlterField on first detect"
        write_migration(tmp_migrations_dir, operations=ops, name="alter")

        ops_again = detect_changes(migrations_dir=str(tmp_migrations_dir))
        assert ops_again == []

    def test_replay_failure_warns(self, tmp_migrations_dir):
        """A migration whose operation fails on SQLite replay emits a warning."""
        from zeeb_orm.migrations.operations import RunSQL

        write_migration(
            tmp_migrations_dir,
            operations=[RunSQL("THIS IS NOT VALID SQL")],
            name="bad",
            initial=True,
        )
        _register_test_model("test_replay_warn", [
            sa.Column("id", sa.Integer(), primary_key=True),
        ])

        with pytest.warns(RuntimeWarning, match="change detection may be incomplete"):
            detect_changes(migrations_dir=str(tmp_migrations_dir))

    def test_alter_field_forward_changes_type_on_sqlite(self, tmp_path):
        """AlterField.forward actually alters the column type via batch mode."""
        from sqlalchemy import create_engine, inspect as sa_inspect

        db = tmp_path / "alter.sqlite3"
        engine = create_engine(f"sqlite:///{db}")
        with engine.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE widget (id INTEGER PRIMARY KEY, qty VARCHAR(10))"
            ))

        op = AlterField(
            model_name="Widget", table="widget", name="qty",
            column_type=sa.Integer(), old_column_type=sa.String(10),
        )
        with engine.begin() as conn:
            op.forward(conn)

        with engine.connect() as conn:
            cols = {c["name"]: c["type"] for c in sa_inspect(conn).get_columns("widget")}
        assert isinstance(cols["qty"], sa.Integer)
        engine.dispose()


class TestMigrationDefaults:
    """The Migration base class uses immutable (shared-safe) defaults."""

    def test_base_defaults_are_empty(self):
        from zeeb_orm.migrations.migration import Migration
        assert tuple(Migration.dependencies) == ()
        assert tuple(Migration.replaces) == ()
        assert tuple(Migration.operations) == ()

    def test_subclass_list_does_not_leak_into_base(self):
        from zeeb_orm.migrations.migration import Migration

        class M(Migration):
            dependencies = ["0001_initial"]

        assert list(M.dependencies) == ["0001_initial"]
        assert tuple(Migration.dependencies) == ()  # base unchanged


class TestConstraintAndDefaultAutodetection:
    """Autodetect named unique constraints and server_default changes."""

    def _write_initial(self, mig_dir, table, columns, constraints=None):
        write_migration(
            mig_dir,
            operations=[CreateModel(
                name="T", table=table, columns=columns,
                primary_key=["id"], constraints=constraints,
            )],
            name="initial", initial=True,
        )

    def test_detects_added_unique_constraint(self, tmp_migrations_dir):
        self._write_initial(tmp_migrations_dir, "cad_a", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(100), nullable=False),
        ])
        _register_test_model("cad_a", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(100), nullable=False),
            sa.UniqueConstraint("email", name="uq_cad_a_email"),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))
        adds = [o for o in ops if isinstance(o, AddConstraint)]
        assert len(adds) == 1
        assert adds[0].constraint.name == "uq_cad_a_email"

    def test_detects_removed_unique_constraint(self, tmp_migrations_dir):
        self._write_initial(
            tmp_migrations_dir, "cad_r",
            [
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("email", sa.String(100), nullable=False),
            ],
            constraints=[sa.UniqueConstraint("email", name="uq_cad_r_email")],
        )
        _register_test_model("cad_r", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(100), nullable=False),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))
        removes = [o for o in ops if isinstance(o, RemoveConstraint)]
        assert len(removes) == 1
        assert removes[0].name == "uq_cad_r_email"

    def test_unique_constraint_converges(self, tmp_migrations_dir):
        """New model with a table-level unique constraint round-trips to []."""
        self._write_initial(
            tmp_migrations_dir, "cad_c",
            [
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("sku", sa.String(50), nullable=False),
            ],
            constraints=[sa.UniqueConstraint("sku", name="uq_cad_c_sku")],
        )
        # Model matches the migration exactly.
        _register_test_model("cad_c", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("sku", sa.String(50), nullable=False),
            sa.UniqueConstraint("sku", name="uq_cad_c_sku"),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))
        assert ops == []

    def test_detects_server_default_change_reversible(self, tmp_migrations_dir):
        self._write_initial(tmp_migrations_dir, "cad_sd", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("n", sa.Integer(), nullable=False, server_default="0"),
        ])
        _register_test_model("cad_sd", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("n", sa.Integer(), nullable=False, server_default="5"),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))
        alters = [o for o in ops if isinstance(o, AlterField) and o.server_default is not None]
        assert len(alters) == 1
        assert alters[0].old_server_default is not None
        assert alters[0].reversible is True

    def test_add_constraint_detection_converges(self, tmp_migrations_dir):
        """detect -> write -> detect for an added unique constraint yields []."""
        self._write_initial(tmp_migrations_dir, "cad_cv", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(100), nullable=False),
        ])
        _register_test_model("cad_cv", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(100), nullable=False),
            sa.UniqueConstraint("email", name="uq_cad_cv_email"),
        ])

        ops = detect_changes(migrations_dir=str(tmp_migrations_dir))
        assert any(isinstance(o, AddConstraint) for o in ops)
        write_migration(tmp_migrations_dir, operations=ops, name="add_uq")

        ops_again = detect_changes(migrations_dir=str(tmp_migrations_dir))
        assert ops_again == []


class TestOperationsCleanup:
    """RunPython reversibility property and identifier quoting in DDL."""

    def test_run_python_not_reversible_without_reverse(self):
        from zeeb_orm.migrations.operations import RunPython
        assert RunPython(lambda c: None).reversible is False

    def test_run_python_reversible_with_reverse(self):
        from zeeb_orm.migrations.operations import RunPython
        assert RunPython(lambda c: None, reverse_code=lambda c: None).reversible is True

    def test_create_model_roundtrip_reserved_word_table(self, tmp_path):
        """A table named like a reserved word survives create + drop via quoting."""
        from sqlalchemy import create_engine, inspect as sa_inspect

        db = tmp_path / "reserved.sqlite3"
        engine = create_engine(f"sqlite:///{db}")

        op = CreateModel(
            name="Order", table="order",
            columns=[
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("total", sa.Integer(), nullable=False),
            ],
            primary_key=["id"],
        )
        with engine.begin() as conn:
            op.forward(conn)
        with engine.connect() as conn:
            assert "order" in sa_inspect(conn).get_table_names()
        with engine.begin() as conn:
            op.backward(conn)
        with engine.connect() as conn:
            assert "order" not in sa_inspect(conn).get_table_names()
        engine.dispose()


# ---------------------------------------------------------------------------
# New operations
# ---------------------------------------------------------------------------


class TestNewOperations:
    """Tests for RenameModel, RenameField, RemoveField reversibility, AlterField reversibility."""

    def test_rename_model_repr(self):
        op = RenameModel(old_name="Foo", new_name="Bar", old_table="foos", new_table="bars")
        r = repr(op)
        assert "RenameModel" in r
        assert "'foos'" in r
        assert "'bars'" in r

    def test_rename_model_describe(self):
        op = RenameModel(old_name="Foo", new_name="Bar", old_table="foos", new_table="bars")
        assert "Foo" in op.describe()
        assert "Bar" in op.describe()

    def test_rename_field_repr(self):
        op = RenameField(model_name="Post", table="posts", old_name="body", new_name="content")
        r = repr(op)
        assert "RenameField" in r
        assert "'body'" in r
        assert "'content'" in r

    def test_rename_field_describe(self):
        op = RenameField(model_name="Post", table="posts", old_name="body", new_name="content")
        assert "body" in op.describe()
        assert "content" in op.describe()

    def test_remove_field_reversible_with_field(self):
        col = sa.Column("views", sa.Integer(), nullable=False)
        op = RemoveField(model_name="Post", table="posts", name="views", field=col)
        assert op.reversible is True

    def test_remove_field_not_reversible_without_field(self):
        op = RemoveField(model_name="Post", table="posts", name="views")
        assert op.reversible is False

    def test_remove_field_repr_with_field(self):
        col = sa.Column("views", sa.Integer(), nullable=False)
        op = RemoveField(model_name="Post", table="posts", name="views", field=col)
        r = repr(op)
        assert "field=" in r

    def test_alter_field_reversible_with_old_type(self):
        op = AlterField(
            model_name="Post",
            table="posts",
            name="views",
            column_type=sa.Integer(),
            old_column_type=sa.String(100),
        )
        assert op.reversible is True

    def test_alter_field_reversible_with_old_nullable(self):
        op = AlterField(
            model_name="Post",
            table="posts",
            name="views",
            nullable=False,
            old_nullable=True,
        )
        assert op.reversible is True

    def test_alter_field_not_reversible_without_old_info(self):
        op = AlterField(
            model_name="Post",
            table="posts",
            name="views",
            column_type=sa.Integer(),
        )
        assert op.reversible is False

    def test_alter_field_repr_includes_old_type(self):
        op = AlterField(
            model_name="Post",
            table="posts",
            name="views",
            column_type=sa.Integer(),
            old_column_type=sa.String(100),
        )
        r = repr(op)
        assert "old_column_type" in r

    def test_add_constraint_describe_and_repr(self):
        op = AddConstraint(
            model_name="Post",
            table="posts",
            constraint=sa.UniqueConstraint("slug", name="uq_posts_slug"),
        )
        assert op.describe() == "Add constraint uq_posts_slug to Post"
        r = repr(op)
        assert "AddConstraint" in r
        assert "'slug'" in r
        assert "uq_posts_slug" in r

    def test_remove_constraint_describe_and_repr(self):
        op = RemoveConstraint(
            model_name="Post",
            table="posts",
            name="uq_posts_slug",
            constraint_type="unique",
        )
        assert op.describe() == "Remove constraint uq_posts_slug from Post"
        r = repr(op)
        assert "RemoveConstraint" in r
        assert "constraint_type='unique'" in r

    def test_add_constraint_forward_unique_real_sqlite(self, tmp_path):
        """AddConstraint.forward actually creates the constraint via batch mode."""
        from sqlalchemy import create_engine, inspect as sa_inspect

        engine = create_engine(f"sqlite:///{tmp_path / 'ac.sqlite3'}")
        with engine.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE posts (id INTEGER PRIMARY KEY, slug VARCHAR(50) NOT NULL)"
            ))
            AddConstraint(
                model_name="Post", table="posts",
                constraint=sa.UniqueConstraint("slug", name="uq_posts_slug"),
            ).forward(conn)

        with engine.connect() as conn:
            uniques = sa_inspect(conn).get_unique_constraints("posts")
        names = {u["name"] for u in uniques}
        assert "uq_posts_slug" in names
        engine.dispose()

    def test_add_constraint_roundtrip_real_sqlite(self, tmp_path):
        """forward then backward leaves the table without the constraint."""
        from sqlalchemy import create_engine, inspect as sa_inspect

        engine = create_engine(f"sqlite:///{tmp_path / 'acr.sqlite3'}")
        op = AddConstraint(
            model_name="Post", table="posts",
            constraint=sa.UniqueConstraint("slug", name="uq_posts_slug"),
        )
        with engine.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE posts (id INTEGER PRIMARY KEY, slug VARCHAR(50) NOT NULL)"
            ))
            op.forward(conn)
        with engine.begin() as conn:
            op.backward(conn)
        with engine.connect() as conn:
            uniques = sa_inspect(conn).get_unique_constraints("posts")
        assert "uq_posts_slug" not in {u["name"] for u in uniques}
        engine.dispose()

    def test_remove_constraint_forward_real_sqlite(self, tmp_path):
        """RemoveConstraint.forward drops an existing unique constraint."""
        from sqlalchemy import create_engine, inspect as sa_inspect

        engine = create_engine(f"sqlite:///{tmp_path / 'rc.sqlite3'}")
        with engine.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE posts (id INTEGER PRIMARY KEY, "
                "slug VARCHAR(50) NOT NULL, CONSTRAINT uq_posts_slug UNIQUE (slug))"
            ))
            RemoveConstraint(
                model_name="Post", table="posts",
                name="uq_posts_slug", constraint_type="unique",
            ).forward(conn)

        with engine.connect() as conn:
            uniques = sa_inspect(conn).get_unique_constraints("posts")
        assert "uq_posts_slug" not in {u["name"] for u in uniques}
        engine.dispose()


# ---------------------------------------------------------------------------
# Migration atomic and hooks
# ---------------------------------------------------------------------------


class TestMigrationBase:
    """Test Migration class new features: atomic, pre/post migrate hooks."""

    def test_atomic_default_true(self):
        from zeeb_orm.migrations.migration import Migration
        m = Migration()
        assert m.atomic is True

    def test_atomic_can_be_disabled(self):
        from zeeb_orm.migrations.migration import Migration

        class MyMigration(Migration):
            atomic = False

        assert MyMigration().atomic is False

    def test_pre_post_migrate_hooks_are_callable(self):
        from zeeb_orm.migrations.migration import Migration
        m = Migration()
        # Should not raise
        m.pre_migrate(None)
        m.post_migrate(None)

    def test_pre_post_migrate_hooks_can_be_overridden(self):
        from zeeb_orm.migrations.migration import Migration

        calls = []

        class MyMigration(Migration):
            def pre_migrate(self, connection):
                calls.append("pre")

            def post_migrate(self, connection):
                calls.append("post")

        m = MyMigration()
        m.pre_migrate(None)
        m.post_migrate(None)
        assert calls == ["pre", "post"]


# ---------------------------------------------------------------------------
# Migration optimizer
# ---------------------------------------------------------------------------


class TestMigrationOptimizer:
    """Tests for the migration optimizer."""

    def test_create_model_plus_add_field_fused(self):
        from zeeb_orm.migrations.optimizer import optimize

        create_op = CreateModel(
            name="Post",
            table="posts",
            columns=[sa.Column("id", sa.Integer(), primary_key=True)],
            primary_key=["id"],
        )
        add_op = AddField(
            model_name="Post",
            table="posts",
            name="title",
            column=sa.Column("title", sa.String(200), nullable=False),
        )
        result = optimize([create_op, add_op])
        assert len(result) == 1
        assert isinstance(result[0], CreateModel)
        col_names = [c.name for c in result[0].columns]
        assert "title" in col_names

    def test_create_then_delete_same_table_eliminated(self):
        from zeeb_orm.migrations.optimizer import optimize
        from zeeb_orm.migrations.operations import DeleteModel

        create_op = CreateModel(
            name="Temp",
            table="temps",
            columns=[sa.Column("id", sa.Integer(), primary_key=True)],
        )
        delete_op = DeleteModel(name="Temp", table="temps")
        result = optimize([create_op, delete_op])
        assert result == []

    def test_add_then_remove_same_column_eliminated(self):
        from zeeb_orm.migrations.optimizer import optimize

        add_op = AddField(
            model_name="Post",
            table="posts",
            name="draft",
            column=sa.Column("draft", sa.Boolean(), nullable=True),
        )
        remove_op = RemoveField(model_name="Post", table="posts", name="draft")
        result = optimize([add_op, remove_op])
        assert result == []

    def test_add_then_alter_same_column_fused(self):
        from zeeb_orm.migrations.optimizer import optimize

        add_op = AddField(
            model_name="Post",
            table="posts",
            name="views",
            column=sa.Column("views", sa.String(50), nullable=True),
        )
        alter_op = AlterField(
            model_name="Post",
            table="posts",
            name="views",
            column_type=sa.Integer(),
            nullable=False,
        )
        result = optimize([add_op, alter_op])
        assert len(result) == 1
        assert isinstance(result[0], AddField)

    def test_no_optimization_across_blocking_op(self):
        """Operations on same table must not be reordered past each other."""
        from zeeb_orm.migrations.optimizer import optimize
        from zeeb_orm.migrations.operations import DeleteModel

        create_op = CreateModel(
            name="Post",
            table="posts",
            columns=[sa.Column("id", sa.Integer(), primary_key=True)],
        )
        delete_op = DeleteModel(name="Post", table="posts")
        add_op = AddField(
            model_name="Post",
            table="posts",
            name="title",
            column=sa.Column("title", sa.String(200)),
        )
        # create + delete cancel, but add_op after delete cannot fuse with create
        result = optimize([create_op, delete_op, add_op])
        # create+delete cancel, add_op remains
        assert len(result) == 1
        assert isinstance(result[0], AddField)

    def test_noop_returns_same(self):
        from zeeb_orm.migrations.optimizer import optimize

        add_op = AddField(
            model_name="Post",
            table="posts",
            name="slug",
            column=sa.Column("slug", sa.String(100)),
        )
        result = optimize([add_op])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# squashmigrations
# ---------------------------------------------------------------------------


class TestSquashMigrations:
    """Tests for squashmigrations command."""

    def test_squash_creates_file(self, tmp_migrations_dir):
        from zeeb_orm.migrations.cli import squashmigrations

        # Write two migrations
        write_migration(
            tmp_migrations_dir,
            operations=[
                CreateModel(
                    name="Post",
                    table="sq_posts",
                    columns=[sa.Column("id", sa.Integer(), primary_key=True)],
                    primary_key=["id"],
                )
            ],
            name="initial",
            initial=True,
        )
        write_migration(
            tmp_migrations_dir,
            operations=[
                AddField(
                    model_name="Post",
                    table="sq_posts",
                    name="title",
                    column=sa.Column("title", sa.String(200), nullable=False),
                )
            ],
            name="add_title",
        )

        all_migs = list_migration_files(tmp_migrations_dir)
        start = all_migs[0][0]
        end = all_migs[1][0]

        result = squashmigrations(
            start=start,
            end=end,
            squashed_name="squashed",
            migrations_dir=str(tmp_migrations_dir),
        )
        assert result is not None

        # Squashed file should exist
        files = list(tmp_migrations_dir.glob("*squashed*.py"))
        assert len(files) == 1

    def test_squash_with_optimizer_reduces_operations(self, tmp_migrations_dir):
        from zeeb_orm.migrations.cli import squashmigrations

        write_migration(
            tmp_migrations_dir,
            operations=[
                CreateModel(
                    name="Widget",
                    table="sq_widgets",
                    columns=[sa.Column("id", sa.Integer(), primary_key=True)],
                    primary_key=["id"],
                )
            ],
            name="initial",
            initial=True,
        )
        write_migration(
            tmp_migrations_dir,
            operations=[
                AddField(
                    model_name="Widget",
                    table="sq_widgets",
                    name="name",
                    column=sa.Column("name", sa.String(100), nullable=False),
                )
            ],
            name="add_name",
        )

        all_migs = list_migration_files(tmp_migrations_dir)
        start = all_migs[0][0]
        end = all_migs[1][0]

        # With optimizer: 2 ops (CreateModel + AddField) → 1 op (CreateModel with column)
        result = squashmigrations(
            start=start,
            end=end,
            migrations_dir=str(tmp_migrations_dir),
        )
        assert result is not None
        # Squashed migration should be numbered after the end migration (0003)
        squash_file = next(tmp_migrations_dir.glob("0003*squashed*.py"), None)
        assert squash_file is not None
        content = squash_file.read_text()
        # After optimization CreateModel includes name column, so only one CreateModel op
        assert "CreateModel" in content
        # Check that replaces attribute is present
        assert "replaces = ['0001_initial', '0002_add_name']" in content

    def test_squash_invalid_start(self, tmp_migrations_dir):
        from zeeb_orm.migrations.cli import squashmigrations

        write_migration(
            tmp_migrations_dir,
            operations=[],
            name="initial",
            initial=True,
        )

        result = squashmigrations(
            start="9999_nonexistent",
            end=list_migration_files(tmp_migrations_dir)[0][0],
            migrations_dir=str(tmp_migrations_dir),
        )
        assert result is None

    def test_squash_no_optimize_flag(self, tmp_migrations_dir):
        from zeeb_orm.migrations.cli import squashmigrations

        write_migration(
            tmp_migrations_dir,
            operations=[
                CreateModel(
                    name="Thing",
                    table="sq_things",
                    columns=[sa.Column("id", sa.Integer(), primary_key=True)],
                    primary_key=["id"],
                )
            ],
            name="initial",
            initial=True,
        )
        write_migration(
            tmp_migrations_dir,
            operations=[
                AddField(
                    model_name="Thing",
                    table="sq_things",
                    name="label",
                    column=sa.Column("label", sa.String(50), nullable=True),
                )
            ],
            name="add_label",
        )

        all_migs = list_migration_files(tmp_migrations_dir)
        start = all_migs[0][0]
        end = all_migs[1][0]

        result = squashmigrations(
            start=start,
            end=end,
            migrations_dir=str(tmp_migrations_dir),
            no_optimize=True,
        )
        assert result is not None
        # Squashed migration should be numbered after the end migration (0003)
        squash_file = next(tmp_migrations_dir.glob("0003*squashed*.py"), None)
        assert squash_file is not None
        content = squash_file.read_text()
        # Both operations should remain
        assert "CreateModel" in content
        assert "AddField" in content
        # Check that replaces attribute is present
        assert "replaces = ['0001_initial', '0002_add_label']" in content

    def test_squash_replaces_mechanism(self, tmp_migrations_dir):
        """Test that squashed migrations properly replace original migrations."""
        from zeeb_orm.migrations.cli import squashmigrations
        from zeeb_orm.migrations import executor
        
        # Create two migrations
        write_migration(
            tmp_migrations_dir,
            operations=[
                CreateModel(
                    name="Item",
                    table="sq_items",
                    columns=[sa.Column("id", sa.Integer(), primary_key=True)],
                    primary_key=["id"],
                )
            ],
            name="initial",
            initial=True,
        )
        write_migration(
            tmp_migrations_dir,
            operations=[
                AddField(
                    model_name="Item",
                    table="sq_items",
                    name="name",
                    column=sa.Column("name", sa.String(100), nullable=False),
                )
            ],
            name="add_name",
        )
        
        all_migs = list_migration_files(tmp_migrations_dir)
        start = all_migs[0][0]
        end = all_migs[1][0]
        
        # Create squashed migration
        result = squashmigrations(
            start=start,
            end=end,
            migrations_dir=str(tmp_migrations_dir),
        )
        assert result is not None
        
        # Verify the squashed migration has replaces attribute
        squash_file = next(tmp_migrations_dir.glob("0003*squashed*.py"), None)
        assert squash_file is not None
        
        # Load the squashed migration and verify replaces
        from zeeb_orm.migrations.executor import load_migration
        squashed_mig = load_migration(squash_file)
        assert hasattr(squashed_mig, 'replaces')
        assert squashed_mig.replaces == ['0001_initial', '0002_add_name']
        
        # Verify that when we apply the squashed migration, the replaced ones
        # are skipped and marked as applied
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name
        
        try:
            applied = executor.migrate(
                target=None,
                database_url=f"sqlite:///{db_path}",
                project_root=tmp_migrations_dir.parent,
            )
            
            # Only the squashed migration should be in the applied list
            # (the replaced migrations are skipped)
            assert '0003_squashed_0001_initial_to_0002_add_name' in applied
            assert '0001_initial' not in applied
            assert '0002_add_name' not in applied
            
            # Check what's in the database - all three should be marked as applied
            from sqlalchemy import create_engine
            from zeeb_orm.migrations.executor import get_applied_migrations
            engine = create_engine(f"sqlite:///{db_path}")
            with engine.connect() as conn:
                applied_in_db = get_applied_migrations(conn)
            engine.dispose()
            
            # All three should be marked as applied in the database
            assert '0001_initial' in applied_in_db
            assert '0002_add_name' in applied_in_db
            assert '0003_squashed_0001_initial_to_0002_add_name' in applied_in_db
        finally:
            import os
            if os.path.exists(db_path):
                os.unlink(db_path)


class TestSettingsDiscovery:
    """Shared settings.py discovery helper."""

    def _make_project(self, tmp_path, body):
        proj = tmp_path / "myproj"
        proj.mkdir()
        (proj / "settings.py").write_text(textwrap.dedent(body))
        return tmp_path

    def test_loads_module_and_restores_sys_path(self, tmp_path):
        import sys
        from zeeb_orm.migrations._settings import (
            load_settings_module, get_database_url, get_installed_apps,
        )
        root = self._make_project(tmp_path, """
            DATABASE = {"url": "sqlite:///custom.db"}
            INSTALLED_APPS = ["apps.blog", "apps.shop"]
        """)
        before = list(sys.path)

        module = load_settings_module(root)
        assert module is not None
        assert get_database_url(root) == "sqlite:///custom.db"
        assert get_installed_apps(root) == ["apps.blog", "apps.shop"]
        assert sys.path == before  # sys.path restored

    def test_missing_settings_returns_defaults(self, tmp_path):
        from zeeb_orm.migrations._settings import (
            load_settings_module, get_database_url, get_installed_apps,
        )
        assert load_settings_module(tmp_path) is None
        assert get_installed_apps(tmp_path) == []
        assert get_database_url(tmp_path) == "sqlite:///db.sqlite3"


class TestDependencyValidation:
    """The executor validates declared dependencies before applying."""

    def _write_raw(self, mig_dir, filename, body):
        (mig_dir / filename).write_text(textwrap.dedent(body))

    def test_unmet_known_dependency_raises(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations import executor
        from zeeb_orm.migrations.state import MigrationError

        # 0001 declares a dependency on the *later* 0002 — an impossible order.
        self._write_raw(tmp_migrations_dir, "0001_first.py", """
            from zeeb_orm.migrations import Migration, operations

            class Migration(Migration):
                initial = True
                dependencies = ['0002_second']
                operations = []
        """)
        self._write_raw(tmp_migrations_dir, "0002_second.py", """
            from zeeb_orm.migrations import Migration, operations

            class Migration(Migration):
                dependencies = []
                operations = []
        """)

        db = f"sqlite:///{tmp_path / 'dep.sqlite3'}"
        with pytest.raises(MigrationError, match="depends on '0002_second'"):
            executor.migrate(database_url=db, project_root=tmp_migrations_dir.parent)

    def test_normal_chain_passes(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations import executor
        names = _write_chain(tmp_migrations_dir, 3)
        db = f"sqlite:///{tmp_path / 'dep_ok.sqlite3'}"
        applied = executor.migrate(database_url=db, project_root=tmp_migrations_dir.parent)
        assert applied == names

    def test_unknown_dependency_warns(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations import executor

        self._write_raw(tmp_migrations_dir, "0001_only.py", """
            from zeeb_orm.migrations import Migration, operations

            class Migration(Migration):
                initial = True
                dependencies = ['0000_deleted_original']
                operations = []
        """)
        db = f"sqlite:///{tmp_path / 'dep_warn.sqlite3'}"
        with pytest.warns(RuntimeWarning, match="depends on unknown migration"):
            executor.migrate(database_url=db, project_root=tmp_migrations_dir.parent)


class TestSquashTrackingConsistency:
    """Squash apply/rollback keeps the tracking table consistent."""

    def _write_squash_pair(self, mig_dir):
        write_migration(
            mig_dir,
            operations=[CreateModel(
                name="Item", table="sqc_items",
                columns=[sa.Column("id", sa.Integer(), primary_key=True)],
                primary_key=["id"],
            )],
            name="initial", initial=True,
        )
        write_migration(
            mig_dir,
            operations=[AddField(
                model_name="Item", table="sqc_items", name="name",
                column=sa.Column("name", sa.String(100), nullable=True),
            )],
            name="add_name",
        )

    def test_rollback_squash_unrecords_replaced(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations.cli import squashmigrations
        from zeeb_orm.migrations import executor
        from sqlalchemy import create_engine
        from zeeb_orm.migrations.executor import get_applied_migrations

        self._write_squash_pair(tmp_migrations_dir)
        names = [n for n, _ in list_migration_files(tmp_migrations_dir)]
        squashmigrations(start=names[0], end=names[1],
                         migrations_dir=str(tmp_migrations_dir))

        db = f"sqlite:///{tmp_path / 'sqc.sqlite3'}"
        root = tmp_migrations_dir.parent

        executor.migrate(database_url=db, project_root=root)
        executor.migrate(target="zero", database_url=db, project_root=root)

        engine = create_engine(db)
        with engine.connect() as conn:
            remaining = get_applied_migrations(conn)
        engine.dispose()
        # Neither the squash nor the replaced originals stay marked applied.
        assert remaining == set()

    def test_squash_added_after_originals_applied_does_not_rerun(self, tmp_migrations_dir, tmp_path):
        """If originals are already applied, a later squash only records itself."""
        from zeeb_orm.migrations.cli import squashmigrations
        from zeeb_orm.migrations import executor

        self._write_squash_pair(tmp_migrations_dir)
        names = [n for n, _ in list_migration_files(tmp_migrations_dir)]
        db = f"sqlite:///{tmp_path / 'sqc2.sqlite3'}"
        root = tmp_migrations_dir.parent

        # Apply the originals normally first.
        executor.migrate(database_url=db, project_root=root)

        # Now create the squash and migrate again — its ops (which would
        # re-create the table / re-add the column) must NOT run.
        squashmigrations(start=names[0], end=names[1],
                         migrations_dir=str(tmp_migrations_dir))
        applied = executor.migrate(database_url=db, project_root=root)

        # The squash is recorded (no crash from re-running AddField on an
        # existing column), and only the squash name is returned.
        assert len(applied) == 1
        assert "squashed" in applied[0]


# ---------------------------------------------------------------------------
# makemigrations --check / --dry-run
# ---------------------------------------------------------------------------


class TestMakemigrationsFlags:
    """Tests for --check and --dry-run flags on makemigrations."""

    def test_check_raises_system_exit_when_changes(self, tmp_migrations_dir):
        from zeeb_orm.migrations.cli import makemigrations

        _register_test_model("check_model", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
        ])

        with pytest.raises(SystemExit) as exc_info:
            makemigrations(
                migrations_dir=str(tmp_migrations_dir),
                check=True,
            )
        assert exc_info.value.code == 1

    def test_check_no_exit_when_no_changes(self, tmp_migrations_dir):
        from zeeb_orm.migrations.cli import makemigrations

        # No models registered → no changes
        result = makemigrations(
            migrations_dir=str(tmp_migrations_dir),
            check=True,
        )
        assert result is None

    def test_dry_run_does_not_write_file(self, tmp_migrations_dir):
        from zeeb_orm.migrations.cli import makemigrations

        _register_test_model("dry_run_model", [
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("value", sa.String(50), nullable=False),
        ])

        result = makemigrations(
            migrations_dir=str(tmp_migrations_dir),
            dry_run=True,
        )
        # No file should be written
        py_files = list(tmp_migrations_dir.glob("*.py"))
        assert py_files == []
        assert result is None


# ---------------------------------------------------------------------------
# executor.migrate — end-to-end behavior (pinning tests for the refactor)
# ---------------------------------------------------------------------------


def _write_chain(mig_dir, n):
    """Write n simple migrations: 0001 creates a table, 0002.. add columns."""
    write_migration(
        mig_dir,
        operations=[CreateModel(
            name="Row", table="exec_rows",
            columns=[sa.Column("id", sa.Integer(), primary_key=True)],
            primary_key=["id"],
        )],
        name="initial", initial=True,
    )
    for i in range(2, n + 1):
        write_migration(
            mig_dir,
            operations=[AddField(
                model_name="Row", table="exec_rows", name=f"c{i}",
                column=sa.Column(f"c{i}", sa.Integer(), nullable=True),
            )],
            name=f"add_c{i}",
        )
    return [name for name, _ in list_migration_files(mig_dir)]


def _table_names(db_url):
    from sqlalchemy import create_engine, inspect as sa_inspect
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            return set(sa_inspect(conn).get_table_names())
    finally:
        engine.dispose()


class TestExecutorMigrate:
    """Pin executor.migrate behavior before/through the plan/apply refactor."""

    def _db(self, tmp_path):
        return f"sqlite:///{tmp_path / 'exec.sqlite3'}"

    def test_migrate_all_then_zero(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations import executor
        names = _write_chain(tmp_migrations_dir, 3)
        db = self._db(tmp_path)
        root = tmp_migrations_dir.parent

        applied = executor.migrate(database_url=db, project_root=root)
        assert applied == names
        assert "exec_rows" in _table_names(db)

        unapplied = executor.migrate(target="zero", database_url=db, project_root=root)
        assert unapplied == list(reversed(names))
        assert "exec_rows" not in _table_names(db)

    def test_migrate_to_target_forward_then_backward(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations import executor
        names = _write_chain(tmp_migrations_dir, 4)
        db = self._db(tmp_path)
        root = tmp_migrations_dir.parent

        fwd = executor.migrate(target=names[2], database_url=db, project_root=root)
        assert fwd == names[:3]

        back = executor.migrate(target=names[0], database_url=db, project_root=root)
        assert back == [names[2], names[1]]

        status = executor.showmigrations(database_url=db, project_root=root)
        applied_now = [n for n, ok in status if ok]
        assert applied_now == [names[0]]

    def test_fake_records_without_creating_tables(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations import executor
        names = _write_chain(tmp_migrations_dir, 2)
        db = self._db(tmp_path)
        root = tmp_migrations_dir.parent

        applied = executor.migrate(database_url=db, project_root=root, fake=True)
        assert applied == names
        assert "exec_rows" not in _table_names(db)  # nothing actually ran

    def test_fake_initial_with_existing_tables(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations import executor
        from sqlalchemy import create_engine, text
        names = _write_chain(tmp_migrations_dir, 2)
        db = self._db(tmp_path)
        root = tmp_migrations_dir.parent

        # Pre-create the table the initial migration would create.
        engine = create_engine(db)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE exec_rows (id INTEGER PRIMARY KEY)"))
        engine.dispose()

        applied = executor.migrate(
            database_url=db, project_root=root, fake_initial=True,
        )
        # Initial is recorded (faked); the column add runs for real.
        assert names[0] in applied
        status = executor.showmigrations(database_url=db, project_root=root)
        assert all(ok for _, ok in status)

    def test_plan_leaves_db_untouched(self, tmp_migrations_dir, tmp_path):
        from zeeb_orm.migrations import executor
        from sqlalchemy import create_engine
        from zeeb_orm.migrations.executor import get_applied_migrations
        names = _write_chain(tmp_migrations_dir, 2)
        db = self._db(tmp_path)
        root = tmp_migrations_dir.parent

        planned = executor.migrate(database_url=db, project_root=root, plan=True)
        assert planned == names

        engine = create_engine(db)
        with engine.connect() as conn:
            assert get_applied_migrations(conn) == set()
        engine.dispose()
        assert "exec_rows" not in _table_names(db)


# ---------------------------------------------------------------------------
# migrate --plan
# ---------------------------------------------------------------------------


class TestMigratePlan:
    """Tests for migrate --plan flag."""

    def test_plan_returns_pending_migrations(self, tmp_migrations_dir):
        from zeeb_orm.migrations import executor

        _register_test_model("plan_items", [
            sa.Column("id", sa.Integer(), primary_key=True),
        ])

        table = metadata.tables["plan_items"]
        create_op = CreateModel(
            name="PlanItem",
            table="plan_items",
            columns=[col.copy() for col in table.columns],
            primary_key=["id"],
        )
        write_migration(
            tmp_migrations_dir,
            operations=[create_op],
            name="initial",
            initial=True,
        )

        planned = executor.migrate(
            target=None,
            database_url="sqlite:///:memory:",
            project_root=tmp_migrations_dir.parent,
            plan=True,
        )
        assert len(planned) == 1
        assert "initial" in planned[0]
