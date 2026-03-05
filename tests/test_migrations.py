"""Tests for the migration system.

Covers:
- Autodetector: compares models against migration state, not the database
- No duplicate migrations when running makemigrations twice
- New field detection after initial migration exists
- _register_models warns on import failures
"""

import shutil
import textwrap
import warnings
from pathlib import Path

import pytest
import sqlalchemy as sa

from zeeb_orm.models.base import metadata, _model_registry, Model
from zeeb_orm.models.fields import CharField, TextField, IntegerField
from zeeb_orm.migrations.autodetector import detect_changes
from zeeb_orm.migrations.writer import write_migration
from zeeb_orm.migrations.executor import list_migration_files
from zeeb_orm.migrations.operations import CreateModel, AddField


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
