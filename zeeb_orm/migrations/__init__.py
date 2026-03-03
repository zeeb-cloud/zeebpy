"""Django-style migrations for Zeeb ORM."""

from zeeb_orm.migrations.migration import Migration
from zeeb_orm.migrations import operations
from zeeb_orm.migrations.cli import (
    current,
    init_migrations,
    makemigrations,
    migrate,
    rollback,
    showmigrations,
)
from zeeb_orm.migrations.state import (
    MigrationError,
    MigrationState,
    check_migrations_applied,
    get_migration_state,
    require_migrations,
)

__all__ = [
    # Migration class & operations (for migration files)
    "Migration",
    "operations",
    # CLI functions
    "init_migrations",
    "makemigrations",
    "migrate",
    "rollback",
    "showmigrations",
    "current",
    # State management
    "MigrationError",
    "MigrationState",
    "check_migrations_applied",
    "get_migration_state",
    "require_migrations",
]
