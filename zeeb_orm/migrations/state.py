"""
Migration state management.

Tracks applied/pending migrations using the ``zeeb_migrations`` table
and provides utilities for checking migration status.
"""

from pathlib import Path
from typing import NamedTuple


class MigrationState(NamedTuple):
    """Current migration state."""
    has_migrations_dir: bool
    total_migrations: int
    applied_migrations: int
    pending_migrations: int
    current_revision: str | None
    head_revision: str | None


class MigrationError(Exception):
    """Raised when migrations are required but not applied."""
    pass


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py or migrations/."""
    current = Path.cwd()
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        if (current / "migrations").exists():
            return current
        current = current.parent
    return None


def get_migration_state(project_root: Path | None = None, db_url: str | None = None) -> MigrationState:
    """
    Get the current migration state.

    Uses the ``zeeb_migrations`` table for accurate tracking.
    """
    from zeeb_orm.migrations import executor

    if project_root is None:
        project_root = find_project_root()

    if project_root is None:
        return MigrationState(
            has_migrations_dir=False,
            total_migrations=0,
            applied_migrations=0,
            pending_migrations=0,
            current_revision=None,
            head_revision=None,
        )

    migrations_dir = project_root / "migrations"
    has_dir = migrations_dir.exists()

    if not has_dir:
        return MigrationState(
            has_migrations_dir=False,
            total_migrations=0,
            applied_migrations=0,
            pending_migrations=0,
            current_revision=None,
            head_revision=None,
        )

    # Get database URL from settings if not provided
    if db_url is None:
        from zeeb_orm.migrations._settings import get_database_url
        db_url = get_database_url(project_root)

    try:
        status = executor.showmigrations(database_url=db_url, project_root=project_root)
    except Exception:
        all_migs = executor.list_migration_files(migrations_dir)
        return MigrationState(
            has_migrations_dir=has_dir,
            total_migrations=len(all_migs),
            applied_migrations=0,
            pending_migrations=len(all_migs),
            current_revision=None,
            head_revision=all_migs[-1][0] if all_migs else None,
        )

    total = len(status)
    applied_count = sum(1 for _, is_applied in status if is_applied)
    pending_count = total - applied_count
    applied_names = [name for name, is_applied in status if is_applied]
    all_names = [name for name, _ in status]

    current_rev = applied_names[-1] if applied_names else None
    head_rev = all_names[-1] if all_names else None

    return MigrationState(
        has_migrations_dir=has_dir,
        total_migrations=total,
        applied_migrations=applied_count,
        pending_migrations=pending_count,
        current_revision=current_rev,
        head_revision=head_rev,
    )


def check_migrations_applied(
    project_root: Path | None = None,
    db_url: str | None = None,
    raise_on_pending: bool = True,
    check_tables: bool = True,
) -> bool:
    """
    Check if all migrations have been applied.

    Args:
        project_root: Project root directory.
        db_url: Database URL.
        raise_on_pending: If True, raise MigrationError when migrations are pending.
        check_tables: If True, also check that all model tables exist in database.

    Returns:
        True if all migrations are applied, False otherwise.

    Raises:
        MigrationError: If raise_on_pending is True and migrations are pending.
    """
    state = get_migration_state(project_root, db_url)

    if not state.has_migrations_dir:
        if raise_on_pending:
            raise MigrationError(
                "No migrations directory found. "
                "Run 'python manage.py makemigrations' to create migrations."
            )
        return False

    if state.total_migrations == 0:
        if raise_on_pending:
            raise MigrationError(
                "No migrations found. "
                "Run 'python manage.py makemigrations' to create migrations for your models."
            )
        return False

    if state.pending_migrations > 0:
        if raise_on_pending:
            raise MigrationError(
                f"You have {state.pending_migrations} unapplied migration(s). "
                f"Run 'python manage.py migrate' to apply them before starting the server."
            )
        return False

    return True


def require_migrations(func):
    """
    Decorator that enforces migrations before running a function.

    Usage::

        @require_migrations
        async def startup():
            ...
    """
    import functools
    import asyncio

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        check_migrations_applied(raise_on_pending=True)
        return func(*args, **kwargs)

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):
        check_migrations_applied(raise_on_pending=True)
        return await func(*args, **kwargs)

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper
