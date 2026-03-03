"""Migration commands — makemigrations, migrate, showmigrations.

Django-style migration commands.  No subprocess calls, no Alembic config
files visible to the user.  Uses the ``zeeb_orm.migrations`` engine
internally.
"""

import os
import sys
from pathlib import Path
from typing import Any


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py."""
    current = Path.cwd()
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent
    return None


def load_project_settings(project_root: Path) -> dict[str, Any]:
    """Load project settings dynamically."""
    settings: dict[str, Any] = {
        "DATABASE": {"url": "sqlite:///db.sqlite3"},
        "INSTALLED_APPS": [],
    }

    for item in project_root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            settings_path = item / "settings.py"
            sys.path.insert(0, str(project_root))
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("settings", settings_path)
                if spec and spec.loader:
                    settings_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(settings_module)
                    if hasattr(settings_module, "DATABASE"):
                        settings["DATABASE"] = settings_module.DATABASE
                    if hasattr(settings_module, "INSTALLED_APPS"):
                        settings["INSTALLED_APPS"] = settings_module.INSTALLED_APPS
            except Exception as e:
                print(f"Warning: Could not load settings: {e}")
            finally:
                if str(project_root) in sys.path:
                    sys.path.remove(str(project_root))
            break

    return settings


def get_installed_apps(project_root: Path) -> list[str]:
    """Get list of installed apps from settings."""
    settings = load_project_settings(project_root)
    apps = list(settings.get("INSTALLED_APPS", []))
    if "zeeb_auth" not in apps:
        apps.insert(0, "zeeb_auth")
    return apps


def _register_models(project_root: Path) -> None:
    """Import and register all models so metadata is populated."""
    from zeeb_orm.migrations.cli import _register_models as do_register
    do_register(project_root)


# ---------------------------------------------------------------------------
# CLI command handlers (called from cli/main.py)
# ---------------------------------------------------------------------------

def run_init(directory: str) -> int:
    """Initialize migrations directory."""
    project_root = find_project_root() or Path.cwd()
    migrations_dir = project_root / directory

    if migrations_dir.exists():
        print(f"Migrations already initialized in '{directory}'")
        return 0

    migrations_dir.mkdir(parents=True)
    print(f"Created migrations directory: {directory}")
    return 0


def run_makemigrations(app: str | None, name: str | None, empty: bool) -> int:
    """Create new migration files by detecting model changes."""
    from zeeb_orm.migrations.autodetector import detect_changes
    from zeeb_orm.migrations.writer import write_migration
    from zeeb_orm.migrations.executor import list_migration_files

    project_root = find_project_root()
    if project_root is None:
        print("Error: Could not find project root (no manage.py found)")
        return 1

    migrations_dir = project_root / "migrations"
    migrations_dir.mkdir(parents=True, exist_ok=True)

    # Register all models
    _register_models(project_root)

    settings = load_project_settings(project_root)
    db_url = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")

    installed_apps = get_installed_apps(project_root)
    print("Checking for model changes in installed apps:")
    for app_name in installed_apps:
        clean_name = app_name.replace("apps.", "")
        print(f"  - {clean_name}")

    if empty:
        filepath = write_migration(
            migrations_dir,
            operations=[],
            name=name or "empty",
        )
        print(f"\nMigrations for 'all apps':")
        print(f"  {filepath.name}")
        print(f"\nEmpty migration created — edit it to add custom operations.")
        return 0

    # Detect changes
    operations = detect_changes(db_url)

    if not operations:
        print("\nNo changes detected.")
        return 0

    # Determine if initial
    existing = list_migration_files(migrations_dir)
    initial = len(existing) == 0

    filepath = write_migration(
        migrations_dir,
        operations=operations,
        name=name,
        initial=initial,
    )

    print(f"\nMigrations for 'all apps':")
    print(f"  {filepath.name}")
    for op in operations:
        print(f"    - {op.describe()}")
    print(f"\nRun 'python manage.py migrate' to apply.")
    return 0


def run_migrate(app: str | None, migration: str | None, rollback_steps: int | None, fake: bool) -> int:
    """Apply or rollback migrations."""
    from zeeb_orm.migrations import executor

    project_root = find_project_root()
    if project_root is None:
        print("Error: Could not find project root")
        return 1

    migrations_dir = project_root / "migrations"
    if not migrations_dir.exists():
        print("No migrations found. Run 'python manage.py makemigrations' first.")
        return 1

    settings = load_project_settings(project_root)
    db_url = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")

    if rollback_steps is not None:
        # Rollback N migrations
        print(f"Rolling back {rollback_steps} migration(s)...")
        status = executor.showmigrations(database_url=db_url, project_root=project_root)
        applied = [name for name, is_applied in status if is_applied]

        if not applied:
            print("  No migrations to roll back.")
            return 0

        if rollback_steps >= len(applied):
            target = "zero"
        else:
            target = applied[-(rollback_steps + 1)]

        rolled_back = executor.migrate(target=target, database_url=db_url, project_root=project_root)
        for name in rolled_back:
            print(f"  Unapplying {name}... OK")
        return 0

    elif migration:
        # Migrate to specific target
        if migration == "zero":
            target = "zero"
        else:
            # Find matching migration name
            all_migs = executor.list_migration_files(migrations_dir)
            target = None
            for mig_name, _ in all_migs:
                if mig_name == migration or mig_name.startswith(migration):
                    target = mig_name
                    break
            if target is None:
                print(f"Error: Migration '{migration}' not found.")
                return 1

        print(f"Migrating to: {target}")
        applied = executor.migrate(target=target, database_url=db_url, project_root=project_root, fake=fake)
        for name in applied:
            prefix = "  Faking" if fake else "  Applying"
            print(f"{prefix} {name}... OK")
        if not applied:
            print("  No migrations to apply.")

    else:
        # Apply all pending
        print("Running migrations:")
        applied = executor.migrate(database_url=db_url, project_root=project_root, fake=fake)
        if applied:
            for name in applied:
                prefix = "  Faking" if fake else "  Applying"
                print(f"{prefix} {name}... OK")
        else:
            print("  No migrations to apply.")

    return 0


def run_showmigrations(app: str | None) -> int:
    """Show migration status."""
    from zeeb_orm.migrations import executor

    project_root = find_project_root()
    if project_root is None:
        print("Error: Could not find project root")
        return 1

    migrations_dir = project_root / "migrations"
    if not migrations_dir.exists():
        print("No migrations found. Run 'python manage.py makemigrations' first.")
        return 0

    settings = load_project_settings(project_root)
    db_url = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")

    status = executor.showmigrations(database_url=db_url, project_root=project_root)

    if not status:
        print("No migrations.")
        return 0

    for name, is_applied in status:
        marker = "[X]" if is_applied else "[ ]"
        print(f" {marker} {name}")

    return 0
