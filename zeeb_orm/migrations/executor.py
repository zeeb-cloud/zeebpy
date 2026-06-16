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
from typing import Any, NamedTuple

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

class _PlanItem(NamedTuple):
    """One step in a migration plan."""

    name: str
    migration: Migration
    backward: bool = False
    # Forward-only flags that skip running operations:
    fake_initial: bool = False        # initial migration whose tables already exist
    replaces_satisfied: bool = False  # squash whose replaced migrations are all applied


def _load_all(migrations_dir: Path) -> list[tuple[str, Path, Migration]]:
    """Load every migration file once, in filename order."""
    return [
        (name, path, load_migration(path))
        for name, path in list_migration_files(migrations_dir)
    ]


def _build_plan(
    conn,
    loaded: list[tuple[str, Path, Migration]],
    applied: set[str],
    target: str | None,
    fake_initial: bool,
) -> list[_PlanItem]:
    """Compute the ordered list of steps to reach *target*.

    - ``target == "zero"``  → unapply all applied migrations (reverse order).
    - ``target`` is a known migration name → move forward or backward to it.
    - otherwise (``None`` or an unknown name) → apply all pending migrations.
    """
    names = [name for name, _, _ in loaded]

    # Migrations superseded by a squash file present in the directory — never
    # applied directly while their replacement exists.
    replaced_by_squash: set[str] = set()
    for _name, _path, mig in loaded:
        if mig.replaces:
            replaced_by_squash.update(mig.replaces)

    def _forward(name: str, mig: Migration) -> _PlanItem:
        if fake_initial and mig.initial and _tables_exist(conn, mig):
            return _PlanItem(name=name, migration=mig, fake_initial=True)
        # A squash whose replaced migrations are all already applied must not
        # re-run its operations on an existing database — only record itself.
        if mig.replaces and all(r in applied for r in mig.replaces):
            return _PlanItem(name=name, migration=mig, replaces_satisfied=True)
        return _PlanItem(name=name, migration=mig)

    if target == "zero":
        # Migrations replaced by a present squash are unapplied via that squash
        # (which also unrecords their tracking rows), so skip them here to avoid
        # dropping the same table twice.
        return [
            _PlanItem(name=name, migration=mig, backward=True)
            for name, _path, mig in reversed(loaded)
            if name in applied and name not in replaced_by_squash
        ]

    if target in names:
        target_idx = names.index(target)
        current_idx = -1
        for i, name in enumerate(names):
            if name in applied:
                current_idx = i

        if target_idx > current_idx:
            return [
                _forward(name, mig)
                for name, _path, mig in loaded[current_idx + 1: target_idx + 1]
                if name not in applied and name not in replaced_by_squash
            ]
        return [
            _PlanItem(name=name, migration=mig, backward=True)
            for name, _path, mig in reversed(loaded[target_idx + 1: current_idx + 1])
            if name in applied and name not in replaced_by_squash
        ]

    # Apply all pending (target is None or an unknown name).
    return [
        _forward(name, mig)
        for name, _path, mig in loaded
        if name not in applied and name not in replaced_by_squash
    ]


def _validate_dependencies(
    plan: list[_PlanItem],
    loaded: list[tuple[str, Path, Migration]],
    applied: set[str],
) -> None:
    """Ensure each forward migration's declared dependencies are met first.

    A dependency is satisfied if it is already applied, covered by a squash's
    ``replaces``, or scheduled earlier in this plan. An unmet dependency that
    names a *known* migration is an error (out-of-order history); one that names
    an *unknown* migration only warns (e.g. a squashed original was deleted).
    """
    import warnings

    from zeeb_orm.migrations.state import MigrationError

    known = {name for name, _, _ in loaded}
    replaced_by_squash: set[str] = set()
    for _name, _path, mig in loaded:
        replaced_by_squash.update(mig.replaces)

    satisfied = set(applied) | replaced_by_squash
    for item in plan:
        if item.backward:
            continue
        for dep in item.migration.dependencies:
            if dep in satisfied:
                continue
            if dep in known:
                raise MigrationError(
                    f"Migration '{item.name}' depends on '{dep}', which is not "
                    f"applied and not scheduled to run before it."
                )
            warnings.warn(
                f"Migration '{item.name}' depends on unknown migration "
                f"'{dep}' (file missing and not covered by a squash); continuing.",
                RuntimeWarning,
                stacklevel=2,
            )
        satisfied.add(item.name)


def _apply_plan(
    conn,
    plan: list[_PlanItem],
    applied: set[str],
    fake: bool,
) -> list[str]:
    """Execute a plan, recording each step. Returns the names processed."""
    processed: list[str] = []

    for item in plan:
        mig = item.migration
        if item.backward:
            if not fake:
                _execute(conn, mig, backward=True)
            unrecord_migration(conn, item.name)
            # A squash recorded its replaced migrations as applied at apply
            # time; unrecord them together so tracking stays consistent.
            for replaced_name in mig.replaces:
                unrecord_migration(conn, replaced_name)
            conn.commit()
            processed.append(item.name)
            continue

        # Forward. Skip running operations when faking, faking the initial
        # (tables exist), or when a squash's replaced migrations are all applied.
        skip_run = fake or item.fake_initial or item.replaces_satisfied
        if not skip_run:
            _execute(conn, mig, backward=False)
        record_migration(conn, item.name)
        conn.commit()
        processed.append(item.name)

        # A squash marks its replaced migrations applied too.
        if mig.replaces:
            for replaced_name in mig.replaces:
                if replaced_name not in applied and replaced_name not in processed:
                    record_migration(conn, replaced_name)
                    conn.commit()

    return processed


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

    try:
        with engine.connect() as conn:
            applied = get_applied_migrations(conn)
            loaded = _load_all(migrations_dir)
            plan_items = _build_plan(conn, loaded, applied, target, fake_initial)
            _validate_dependencies(plan_items, loaded, applied)

            if plan:
                # A faked-initial migration runs nothing, so it is omitted from
                # the plan output (matching the historical behavior).
                return [item.name for item in plan_items if not item.fake_initial]

            return _apply_plan(conn, plan_items, applied, fake)
    finally:
        engine.dispose()


def _execute(conn, mig, backward: bool = False) -> None:
    """Run a migration's operations forward or backward, respecting ``atomic``."""
    def _body() -> None:
        mig.pre_migrate(conn)
        ops = list(reversed(mig.operations)) if backward else list(mig.operations)
        for op in ops:
            if backward:
                op.backward(conn)
            else:
                op.forward(conn)
        mig.post_migrate(conn)

    if mig.atomic:
        with conn.begin_nested():
            _body()
    else:
        _body()


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
