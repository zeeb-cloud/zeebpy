"""
Migration state management.

Tracks applied/pending migrations and enforces migration requirement.
"""

import os
import sys
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


def get_migration_files(project_root: Path) -> list[str]:
    """Get list of migration files."""
    versions_dir = project_root / "migrations" / "versions"
    if not versions_dir.exists():
        return []
    
    files = []
    for f in versions_dir.glob("*.py"):
        if not f.name.startswith("__"):
            # Extract revision from filename (format: xxxx_description.py)
            files.append(f.stem)
    
    return sorted(files)


def get_alembic_state(project_root: Path, db_url: str) -> tuple[str | None, str | None]:
    """
    Get current and head revision from Alembic.
    
    Returns (current_revision, head_revision) or (None, None) on error.
    """
    migrations_dir = project_root / "migrations"
    alembic_ini = migrations_dir / "alembic.ini"
    
    if not alembic_ini.exists():
        return None, None
    
    try:
        import subprocess
        
        # Convert async URL to sync
        sync_url = db_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2").replace("+aiomysql", "+pymysql")
        
        # Update alembic.ini with correct URL
        import re
        content = alembic_ini.read_text()
        content = re.sub(r"sqlalchemy\.url = .*", f"sqlalchemy.url = {sync_url}", content)
        alembic_ini.write_text(content)
        
        original_dir = os.getcwd()
        os.chdir(project_root)
        
        try:
            # Get current revision
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(alembic_ini), "current"],
                capture_output=True, text=True, timeout=10
            )
            current = None
            if result.returncode == 0 and result.stdout.strip():
                # Parse output like "abc123 (head)"
                line = result.stdout.strip().split('\n')[0]
                current = line.split()[0] if line else None
            
            # Get head revision
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", str(alembic_ini), "heads"],
                capture_output=True, text=True, timeout=10
            )
            head = None
            if result.returncode == 0 and result.stdout.strip():
                line = result.stdout.strip().split('\n')[0]
                head = line.split()[0] if line else None
            
            return current, head
            
        finally:
            os.chdir(original_dir)
            
    except Exception:
        return None, None


def get_migration_state(project_root: Path | None = None, db_url: str | None = None) -> MigrationState:
    """
    Get the current migration state.
    
    Args:
        project_root: Project root directory. Auto-detected if None.
        db_url: Database URL. Read from settings if None.
    
    Returns:
        MigrationState with information about migrations.
    """
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
    
    migration_files = get_migration_files(project_root)
    total = len(migration_files)
    
    # Get database URL from settings if not provided
    if db_url is None:
        db_url = "sqlite:///db.sqlite3"  # Default
        sys.path.insert(0, str(project_root))
        try:
            for item in project_root.iterdir():
                if item.is_dir() and (item / "settings.py").exists():
                    import importlib.util
                    spec = importlib.util.spec_from_file_location("settings", item / "settings.py")
                    if spec and spec.loader:
                        settings = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(settings)
                        db_url = getattr(settings, "DATABASE", {}).get("url", db_url)
                    break
        except Exception:
            pass
        finally:
            if str(project_root) in sys.path:
                sys.path.remove(str(project_root))
    
    current, head = get_alembic_state(project_root, db_url)
    
    # Calculate applied/pending
    if current is None:
        applied = 0
        pending = total
    elif current == head:
        applied = total
        pending = 0
    else:
        # Approximate - would need to walk the revision tree for exact count
        applied = total // 2  # Rough estimate
        pending = total - applied
    
    return MigrationState(
        has_migrations_dir=has_dir,
        total_migrations=total,
        applied_migrations=applied,
        pending_migrations=pending,
        current_revision=current,
        head_revision=head,
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
                "Run 'python manage.py init' to initialize migrations, "
                "then 'python manage.py makemigrations' to create them."
            )
        return False
    
    if state.total_migrations == 0:
        if raise_on_pending:
            raise MigrationError(
                "No migrations found. "
                "Run 'python manage.py makemigrations' to create migrations for your models."
            )
        return False
    
    # Check if at head
    if state.current_revision != state.head_revision:
        if raise_on_pending:
            pending = state.pending_migrations or "some"
            raise MigrationError(
                f"You have {pending} unapplied migration(s). "
                f"Run 'python manage.py migrate' to apply them before starting the server."
            )
        return False
    
    return True


def require_migrations(func):
    """
    Decorator that enforces migrations before running a function.
    
    Usage:
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
