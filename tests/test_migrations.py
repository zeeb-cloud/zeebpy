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

    def test_add_constraint_forward_unique_uses_alembic_operations(self):
        from alembic.operations import Operations

        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as conn, patch.object(
            Operations,
            "create_unique_constraint",
            autospec=True,
        ) as create_unique_constraint:
            op = AddConstraint(
                model_name="Post",
                table="posts",
                constraint=sa.UniqueConstraint("slug", name="uq_posts_slug"),
            )
            op.forward(conn)

        _, name, table, columns = create_unique_constraint.call_args.args
        assert name == "uq_posts_slug"
        assert table == "posts"
        assert columns == ["slug"]

    def test_add_constraint_backward_check_uses_alembic_operations(self):
        from alembic.operations import Operations

        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as conn, patch.object(
            Operations,
            "drop_constraint",
            autospec=True,
        ) as drop_constraint:
            op = AddConstraint(
                model_name="Post",
                table="posts",
                constraint=sa.CheckConstraint("length(slug) > 1", name="ck_posts_slug_len"),
            )
            op.backward(conn)

        _, name, table = drop_constraint.call_args.args
        assert name == "ck_posts_slug_len"
        assert table == "posts"
        assert drop_constraint.call_args.kwargs == {"type_": "check"}

    def test_remove_constraint_forward_uses_alembic_operations(self):
        from alembic.operations import Operations

        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as conn, patch.object(
            Operations,
            "drop_constraint",
            autospec=True,
        ) as drop_constraint:
            op = RemoveConstraint(
                model_name="Post",
                table="posts",
                name="uq_posts_slug",
                constraint_type="unique",
            )
            op.forward(conn)

        _, name, table = drop_constraint.call_args.args
        assert name == "uq_posts_slug"
        assert table == "posts"
        assert drop_constraint.call_args.kwargs == {"type_": "unique"}


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
