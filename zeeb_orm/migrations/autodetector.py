"""
Auto-detect model changes by comparing model metadata against database schema.

Uses Alembic's compare_metadata internally but converts results to
Django-style Operation objects.
"""

from __future__ import annotations

from typing import Any

from zeeb_orm.migrations.operations import (
    AddField,
    AddIndex,
    AlterField,
    CreateModel,
    DeleteModel,
    RemoveField,
    RemoveIndex,
    Operation,
)


def _table_name_to_model_name(table_name: str) -> str:
    """Guess a model name from a table name (e.g. 'blog_posts' -> 'Post')."""
    from zeeb_orm.models.base import _model_registry
    # Try to find the model in the registry
    for name, cls in _model_registry.items():
        meta = getattr(cls, '_meta', None)
        if meta and getattr(meta, 'table_name', None) == table_name:
            return name
    # Fallback: title-case the table name
    parts = table_name.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts)


def detect_changes(
    database_url: str | None = None,
    migrations_dir: str | None = None,
) -> list[Operation]:
    """
    Compare current model state against existing migration state and return operations.

    This is the core of ``makemigrations`` — it detects what changed.

    Instead of comparing against the live database (which would re-detect
    already-migrated changes if migrations haven't been applied yet), this
    replays all existing migration files into an in-memory SQLite database
    and compares the model metadata against that state.

    Args:
        database_url: Unused, kept for backward compatibility.
        migrations_dir: Path to the migrations directory. If None, auto-detected.

    Returns:
        List of Operation objects representing detected changes.
    """
    from pathlib import Path
    from sqlalchemy import create_engine
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from zeeb_orm.models.base import metadata
    from zeeb_orm.migrations.executor import list_migration_files, load_migration

    # Resolve migrations directory
    if migrations_dir is not None:
        mig_dir = Path(migrations_dir)
    else:
        from zeeb_orm.migrations.state import find_project_root
        project_root = find_project_root() or Path.cwd()
        mig_dir = project_root / "migrations"

    # Build migration state in an in-memory SQLite database by replaying
    # all existing migration files forward.
    mem_engine = create_engine("sqlite:///:memory:")
    try:
        all_migrations = list_migration_files(mig_dir)
        if all_migrations:
            with mem_engine.connect() as conn:
                for _name, path in all_migrations:
                    mig = load_migration(path)
                    for op in mig.operations:
                        try:
                            op.forward(conn)
                        except Exception:
                            # Skip operations that fail in SQLite (e.g. unsupported types)
                            pass
                    conn.commit()

        # Compare model metadata against the in-memory migration state
        with mem_engine.connect() as conn:
            migration_ctx = MigrationContext.configure(conn)
            diffs = compare_metadata(migration_ctx, metadata)
    finally:
        mem_engine.dispose()

    return _convert_diffs(diffs)


def _convert_diffs(diffs: list) -> list[Operation]:
    """Convert Alembic diff tuples to Operation objects."""
    operations: list[Operation] = []

    for diff in diffs:
        op = _convert_single_diff(diff)
        if op is not None:
            if isinstance(op, list):
                operations.extend(op)
            else:
                operations.append(op)

    return operations


def _convert_single_diff(diff: tuple) -> Operation | list[Operation] | None:
    """Convert a single Alembic diff tuple to an Operation."""
    from sqlalchemy import Column

    diff_type = diff[0]

    if diff_type == "add_table":
        table = diff[1]
        model_name = _table_name_to_model_name(table.name)
        columns = [col.copy() for col in table.columns]
        pk_cols = [col.name for col in table.primary_key.columns]
        return CreateModel(
            name=model_name,
            table=table.name,
            columns=columns,
            primary_key=pk_cols,
        )

    elif diff_type == "remove_table":
        table = diff[1]
        model_name = _table_name_to_model_name(table.name)
        return DeleteModel(name=model_name, table=table.name)

    elif diff_type == "add_column":
        schema, table_name, column = diff[1], diff[2], diff[3]
        model_name = _table_name_to_model_name(table_name)
        return AddField(
            model_name=model_name,
            table=table_name,
            name=column.name,
            column=column.copy(),
        )

    elif diff_type == "remove_column":
        schema, table_name, column = diff[1], diff[2], diff[3]
        model_name = _table_name_to_model_name(table_name)
        return RemoveField(
            model_name=model_name,
            table=table_name,
            name=column.name,
        )

    elif diff_type == "modify_type":
        schema, table_name, col_name = diff[1], diff[2], diff[3]
        kwargs, old_type, new_type = diff[4], diff[5], diff[6]
        model_name = _table_name_to_model_name(table_name)
        return AlterField(
            model_name=model_name,
            table=table_name,
            name=col_name,
            column_type=new_type,
        )

    elif diff_type == "modify_nullable":
        schema, table_name, col_name = diff[1], diff[2], diff[3]
        kwargs, old_nullable, new_nullable = diff[4], diff[5], diff[6]
        model_name = _table_name_to_model_name(table_name)
        return AlterField(
            model_name=model_name,
            table=table_name,
            name=col_name,
            nullable=new_nullable,
        )

    elif diff_type == "add_index":
        index = diff[1]
        table_name = index.table.name if index.table is not None else ""
        model_name = _table_name_to_model_name(table_name)
        return AddIndex(
            model_name=model_name,
            table=table_name,
            name=index.name,
            columns=[col.name for col in index.columns],
            unique=index.unique,
        )

    elif diff_type == "remove_index":
        index = diff[1]
        table_name = index.table.name if index.table is not None else ""
        model_name = _table_name_to_model_name(table_name)
        return RemoveIndex(
            model_name=model_name,
            table=table_name,
            name=index.name,
        )

    return None
