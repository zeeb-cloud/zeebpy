"""
Migration executor — loads, orders, and runs migrations.

Tracks applied migrations in a ``zeeb_migrations`` table, similar to
Django's ``django_migrations`` table.
"""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zeeb_orm.migrations.migration import Migration


# ---------------------------------------------------------------------------
# Migration tracking table
# ---------------------------------------------------------------------------

_TRACKING_TABLE = "zeeb_migrations"
_CREATE_TRACKING_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TRACKING_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(255) NOT NULL UNIQUE,
    applied_at TIMESTAMP NOT NULL
)
"""
# Postgres variant (AUTOINCREMENT → SERIAL)
_CREATE_TRACKING_SQL_PG = f"""
CREATE TABLE IF NOT EXISTS {_TRACKING_TABLE} (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    applied_at TIMESTAMP NOT NULL
)
"""


def _get_sync_url(url: str) -> str:
    return (
        url.replace("+asyncpg", "")
        .replace("+aiomysql", "")
        .replace("+aiosqlite", "")
    )


# ---------------------------------------------------------------------------
# Loading migration files
# ---------------------------------------------------------------------------

def get_migrations_dir(project_root: Path | None = None) -> Path:
    """Return the migrations directory, creating it if needed."""
    if project_root is None:
        project_root = _find_project_root() or Path.cwd()
    return project_root / "migrations"


def _find_project_root() -> Path | None:
    from zeeb_orm.migrations.state import find_project_root
    return find_project_root()


def list_migration_files(migrations_dir: Path) -> list[tuple[str, Path]]:
    """
    Return ``[(name, path), ...]`` sorted by name.

    Migration files match ``NNNN_description.py`` (e.g. ``0001_initial.py``).
    """
    if not migrations_dir.exists():
        return []

    pattern = re.compile(r"^\d{4}_.+\.py$")
    files = []
    for p in sorted(migrations_dir.iterdir()):
        if pattern.match(p.name) and p.is_file():
            files.append((p.stem, p))
    return files


def load_migration(path: Path) -> Migration:
    """Import a migration file and return its ``Migration`` instance."""
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load migration: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Find the Migration subclass in the module
    for name in dir(module):
        obj = getattr(module, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, Migration)
            and obj is not Migration
        ):
            return obj()

    raise ImportError(f"No Migration class found in {path}")


# ---------------------------------------------------------------------------
# Tracking applied migrations
# ---------------------------------------------------------------------------

def _ensure_tracking_table(connection) -> None:
    """Create the zeeb_migrations table if it doesn't exist."""
    from sqlalchemy import text, inspect as sa_inspect

    inspector = sa_inspect(connection)
    if _TRACKING_TABLE not in inspector.get_table_names():
        dialect = connection.engine.dialect.name
        if dialect == "postgresql":
            connection.execute(text(_CREATE_TRACKING_SQL_PG))
        else:
            connection.execute(text(_CREATE_TRACKING_SQL))
        connection.commit()


def get_applied_migrations(connection) -> set[str]:
    """Return set of applied migration names."""
    from sqlalchemy import text

    _ensure_tracking_table(connection)
    rows = connection.execute(
        text(f"SELECT name FROM {_TRACKING_TABLE} ORDER BY name")
    ).fetchall()
    return {row[0] for row in rows}


def record_migration(connection, name: str) -> None:
    """Record a migration as applied."""
    from sqlalchemy import text

    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        text(f"INSERT INTO {_TRACKING_TABLE} (name, applied_at) VALUES (:name, :applied_at)"),
        {"name": name, "applied_at": now},
    )


def unrecord_migration(connection, name: str) -> None:
    """Remove a migration from the applied list."""
    from sqlalchemy import text

    connection.execute(
        text(f"DELETE FROM {_TRACKING_TABLE} WHERE name = :name"),
        {"name": name},
    )


# ---------------------------------------------------------------------------
# Executing migrations
# ---------------------------------------------------------------------------

def migrate(
    target: str | None = None,
    database_url: str | None = None,
    project_root: Path | None = None,
    fake: bool = False,
    fake_initial: bool = False,
    plan: bool = False,
) -> list[str]:
    """
    Apply pending migrations (or migrate to a specific target).

    Args:
        target: Target migration name, ``"zero"`` to rollback all,
                or ``None`` to apply all pending.
        database_url: Database URL. Reads from settings if None.
        project_root: Project root directory.
        fake: If True, mark as applied without running.
        fake_initial: If True, skip the initial migration when the tables
                      already exist (useful when adopting migrations in an
                      existing project).
        plan: If True, return the list of migrations that *would* be
              applied/unapplied without actually executing anything.

    Returns:
        List of migration names that were (or would be) applied/unapplied.
    """
    from sqlalchemy import create_engine

    if database_url is None:
        from zeeb_orm.conf.settings import get_settings
        database_url = get_settings().database.url

    sync_url = _get_sync_url(database_url)
    engine = create_engine(sync_url)

    migrations_dir = get_migrations_dir(project_root)
    all_migrations = list_migration_files(migrations_dir)

    applied_names: list[str] = []

    try:
        with engine.connect() as conn:
            applied = get_applied_migrations(conn)
            
            # Build a set of migrations that are replaced by squashed migrations
            # (both already-applied and unapplied squashed migrations)
            replaced_by_applied = set()
            for name, path in all_migrations:
                mig = load_migration(path)
                if hasattr(mig, 'replaces') and mig.replaces:
                    # If this squashed migration is already applied OR exists in the list,
                    # mark its replaced migrations as superseded
                    replaced_by_applied.update(mig.replaces)

            if target == "zero":
                # Rollback all applied migrations in reverse order
                to_unapply = [
                    (name, path) for name, path in reversed(all_migrations) if name in applied
                ]
                if plan:
                    return [name for name, _ in to_unapply]
                for name, path in to_unapply:
                    mig = load_migration(path)
                    if not fake:
                        _run_backward(conn, mig)
                    unrecord_migration(conn, name)
                    conn.commit()
                    applied_names.append(name)

            elif target and target in {name for name, _ in all_migrations}:
                # Migrate to specific target (forward or backward)
                target_idx = next(
                    i for i, (name, _) in enumerate(all_migrations) if name == target
                )
                current_idx = -1
                for i, (name, _) in enumerate(all_migrations):
                    if name in applied:
                        current_idx = i

                if target_idx > current_idx:
                    # Forward to target
                    to_apply = [
                        (name, path)
                        for name, path in all_migrations[current_idx + 1: target_idx + 1]
                        if name not in applied and name not in replaced_by_applied
                    ]
                    if plan:
                        # Filter out initial migrations when fake_initial=True and tables exist
                        if fake_initial:
                            filtered = []
                            for name, path in to_apply:
                                mig = load_migration(path)
                                if mig.initial and _tables_exist(conn, mig):
                                    continue  # Skip this migration in the plan
                                filtered.append(name)
                            return filtered
                        return [name for name, _ in to_apply]
                    for name, path in to_apply:
                        mig = load_migration(path)
                        if not fake:
                            if fake_initial and mig.initial:
                                if _tables_exist(conn, mig):
                                    record_migration(conn, name)
                                    conn.commit()
                                    applied_names.append(name)
                                    continue
                            _run_forward(conn, mig)
                        record_migration(conn, name)
                        conn.commit()
                        applied_names.append(name)
                        
                        # If this migration replaces others, mark them as applied too
                        if hasattr(mig, 'replaces') and mig.replaces:
                            for replaced_name in mig.replaces:
                                if replaced_name not in applied and replaced_name not in applied_names:
                                    record_migration(conn, replaced_name)
                                    conn.commit()
                else:
                    # Backward to target
                    to_unapply = [
                        (name, path)
                        for name, path in reversed(all_migrations[target_idx + 1: current_idx + 1])
                        if name in applied
                    ]
                    if plan:
                        return [name for name, _ in to_unapply]
                    for name, path in to_unapply:
                        mig = load_migration(path)
                        if not fake:
                            _run_backward(conn, mig)
                        unrecord_migration(conn, name)
                        conn.commit()
                        applied_names.append(name)

            else:
                # Apply all pending
                to_apply = [
                    (name, path)
                    for name, path in all_migrations
                    if name not in applied and name not in replaced_by_applied
                ]
                if plan:
                    planned_names: list[str] = []
                    for name, path in to_apply:
                        mig = load_migration(path)
                        if fake_initial and mig.initial and _tables_exist(conn, mig):
                            continue
                        planned_names.append(name)
                    return planned_names
                for name, path in to_apply:
                    mig = load_migration(path)
                    if not fake:
                        if fake_initial and mig.initial:
                            if _tables_exist(conn, mig):
                                record_migration(conn, name)
                                conn.commit()
                                applied_names.append(name)
                                continue
                        _run_forward(conn, mig)
                    record_migration(conn, name)
                    conn.commit()
                    applied_names.append(name)
                    
                    # If this migration replaces others, mark them as applied too
                    if hasattr(mig, 'replaces') and mig.replaces:
                        for replaced_name in mig.replaces:
                            if replaced_name not in applied and replaced_name not in applied_names:
                                record_migration(conn, replaced_name)
                                conn.commit()

    finally:
        engine.dispose()

    return applied_names


def _run_forward(conn, mig) -> None:
    """Run a migration's forward operations, respecting atomic setting."""
    mig.pre_migrate(conn)
    if mig.atomic:
        with conn.begin_nested():
            for op in mig.operations:
                op.forward(conn)
    else:
        for op in mig.operations:
            op.forward(conn)
    mig.post_migrate(conn)


def _run_backward(conn, mig) -> None:
    """Run a migration's backward operations, respecting atomic setting."""
    mig.pre_migrate(conn)
    if mig.atomic:
        with conn.begin_nested():
            for op in reversed(mig.operations):
                op.backward(conn)
    else:
        for op in reversed(mig.operations):
            op.backward(conn)
    mig.post_migrate(conn)


def _tables_exist(conn, mig) -> bool:
    """Return True if all tables created by the initial migration already exist."""
    from sqlalchemy import inspect as sa_inspect
    from zeeb_orm.migrations.operations import CreateModel

    inspector = sa_inspect(conn)
    existing = set(inspector.get_table_names())
    create_ops = [op for op in mig.operations if isinstance(op, CreateModel)]
    if not create_ops:
        return False
    return all(op.table in existing for op in create_ops)


def showmigrations(
    database_url: str | None = None,
    project_root: Path | None = None,
) -> list[tuple[str, bool]]:
    """
    Return list of ``(migration_name, is_applied)`` tuples.

    Args:
        database_url: Database URL.
        project_root: Project root directory.

    Returns:
        List of (name, applied) tuples.
    """
    from sqlalchemy import create_engine

    if database_url is None:
        from zeeb_orm.conf.settings import get_settings
        database_url = get_settings().database.url

    sync_url = _get_sync_url(database_url)
    engine = create_engine(sync_url)

    migrations_dir = get_migrations_dir(project_root)
    all_migrations = list_migration_files(migrations_dir)

    result: list[tuple[str, bool]] = []

    try:
        with engine.connect() as conn:
            applied = get_applied_migrations(conn)
            for name, _ in all_migrations:
                result.append((name, name in applied))
    finally:
        engine.dispose()

    return result
