"""
Django-style migration commands — programmatic API.

Provides ``makemigrations``, ``migrate``, ``showmigrations``, ``rollback``,
and ``current`` without any subprocess calls or Alembic config files.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def _find_project_root() -> Path | None:
    from zeeb_orm.migrations.state import find_project_root
    return find_project_root()


def _get_database_url() -> str:
    """Get database URL from settings."""
    from zeeb_orm.conf.settings import get_settings
    return get_settings().database.url


def _get_migrations_dir(migrations_dir: str | None = None) -> Path:
    """Resolve migrations directory path."""
    if migrations_dir:
        return Path(migrations_dir)
    project_root = _find_project_root() or Path.cwd()
    return project_root / "migrations"


def _register_models(project_root: Path | None = None) -> None:
    """Import and register all models so metadata is populated."""
    from zeeb_orm.models.base import Model

    if project_root is None:
        project_root = _find_project_root() or Path.cwd()

    sys.path.insert(0, str(project_root))
    try:
        # Load settings to get INSTALLED_APPS and AUTH_USER_MODEL
        installed_apps: list[str] = []
        auth_user_model: str | None = None
        for item in project_root.iterdir():
            if item.is_dir() and (item / "settings.py").exists():
                import importlib.util
                spec = importlib.util.spec_from_file_location("settings", item / "settings.py")
                if spec and spec.loader:
                    settings_module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(settings_module)
                    installed_apps = list(getattr(settings_module, "INSTALLED_APPS", []))
                    auth_user_model = getattr(settings_module, "AUTH_USER_MODEL", None)
                break

        # Register auth models — Permission first (no FK dependencies)
        try:
            from zeeb_api.auth.models import Permission
            Permission._get_table()
            if not auth_user_model:
                from zeeb_api.auth.models import User
                User._get_table()
        except Exception:
            pass

        # Register models from installed apps (including custom user models)
        for app in installed_apps:
            if app == "zeeb_auth":
                continue
            clean_name = app.replace("apps.", "")
            import importlib
            import warnings

            # Try multiple import paths for flexibility
            import_paths = [f"apps.{clean_name}.models"]
            if not app.startswith("apps."):
                import_paths.append(f"{app}.models")

            imported = False
            last_error = None
            for import_path in import_paths:
                try:
                    models_module = importlib.import_module(import_path)
                    for name in dir(models_module):
                        obj = getattr(models_module, name)
                        if (isinstance(obj, type) and issubclass(obj, Model)
                                and obj is not Model
                                and not getattr(obj._meta, 'abstract', False)):
                            obj._get_table()
                    imported = True
                    break
                except ImportError as e:
                    last_error = e
                except Exception as e:
                    last_error = e
                    break  # Non-import errors are real problems

            if not imported and last_error is not None:
                warnings.warn(
                    f"Could not import models from app '{app}': {last_error}",
                    stacklevel=2,
                )

        # Clear cached user model so it re-resolves with sys.path set up
        try:
            from zeeb_api.auth.backends import clear_user_model_cache
            clear_user_model_cache()
        except Exception:
            pass

        # Register UserPermission after app models so that the user FK
        # target (default User or custom user model) is already available
        try:
            from zeeb_api.auth.models import UserPermission
            UserPermission._get_table()
        except Exception:
            pass

        # Resolve any pending reverse relations (callable FK targets that
        # were deferred during import are now resolvable)
        try:
            from zeeb_orm.models.base import _process_pending_relations
            _process_pending_relations(resolve_callables=True)
        except Exception:
            pass
    finally:
        if str(project_root) in sys.path:
            sys.path.remove(str(project_root))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_migrations(directory: str = "migrations") -> None:
    """Initialize migrations directory. Called automatically by makemigrations."""
    migrations_path = Path(directory)
    if migrations_path.exists():
        print(f"Migrations directory '{directory}' already exists.")
        return
    migrations_path.mkdir(parents=True)
    print(f"Created migrations directory: {directory}")


def makemigrations(
    message: str | None = None,
    autogenerate: bool = True,
    empty: bool = False,
    migrations_dir: str | None = None,
    check: bool = False,
    dry_run: bool = False,
) -> str | None:
    """
    Create a new migration by auto-detecting model changes.

    Like Django's ``python manage.py makemigrations``.

    Args:
        message: Human-readable migration description.
        autogenerate: Auto-detect model changes (default True).
        empty: Create an empty migration for manual editing.
        migrations_dir: Override migrations directory.
        check: If True, exit with code 1 when changes are detected (no file
               is written).  Useful in CI pipelines to ensure all migrations
               have been committed.
        dry_run: If True, print what would be generated without writing any
                 files.

    Returns:
        The migration name (e.g. ``"0001_initial"``) or None.
    """
    from zeeb_orm.migrations.writer import write_migration
    from zeeb_orm.migrations.autodetector import detect_changes

    project_root = _find_project_root() or Path.cwd()
    mig_dir = _get_migrations_dir(migrations_dir)

    # Auto-create migrations directory (unless --check / --dry-run)
    if not check and not dry_run:
        mig_dir.mkdir(parents=True, exist_ok=True)

    # Register all models so metadata is populated
    _register_models(project_root)

    if empty:
        if check or dry_run:
            print("Empty migration would be created.")
            return None
        filepath = write_migration(mig_dir, operations=[], name=message or "empty")
        migration_name = filepath.stem
        print(f"Migrations for 'all apps':")
        print(f"  {filepath.name}")
        print(f"\nEmpty migration created — edit it to add custom operations.")
        return migration_name

    # Detect changes against existing migration state
    operations = detect_changes(migrations_dir=str(mig_dir))

    if not operations:
        print("No changes detected.")
        return None

    # --check: report and signal with a non-zero exit
    if check:
        print("Pending model changes detected:")
        for op in operations:
            print(f"    - {op.describe()}")
        raise SystemExit(1)

    # --dry-run: show what would be written without actually writing
    if dry_run:
        from zeeb_orm.migrations.executor import list_migration_files
        from zeeb_orm.migrations.writer import next_migration_number, _auto_name, _slugify

        number = next_migration_number(mig_dir) if mig_dir.exists() else 1
        slug = _slugify(message) if message else (_auto_name(operations) if number > 1 else "initial")
        filename = f"{number:04d}_{slug}.py"
        print(f"Migrations for 'all apps' (dry run — no files written):")
        print(f"  {filename}")
        for op in operations:
            print(f"    - {op.describe()}")
        return None

    # Determine if this is the initial migration
    from zeeb_orm.migrations.executor import list_migration_files
    existing = list_migration_files(mig_dir)
    initial = len(existing) == 0

    filepath = write_migration(
        mig_dir,
        operations=operations,
        name=message,
        initial=initial,
    )
    migration_name = filepath.stem

    print(f"Migrations for 'all apps':")
    print(f"  {filepath.name}")
    for op in operations:
        print(f"    - {op.describe()}")

    return migration_name


def migrate(
    target: str | None = None,
    migrations_dir: str | None = None,
    fake: bool = False,
    fake_initial: bool = False,
    plan: bool = False,
) -> None:
    """
    Apply pending migrations.

    Like Django's ``python manage.py migrate``.

    Args:
        target: Target migration name, ``"zero"`` to rollback all,
                or None to apply all pending.
        migrations_dir: Override migrations directory.
        fake: Mark as applied without running.
        fake_initial: If True, skip the first migration when tables already
                      exist (for adopting migrations in an existing project).
        plan: If True, print what would happen without applying anything.
    """
    from zeeb_orm.migrations import executor

    mig_dir = _get_migrations_dir(migrations_dir)
    if not mig_dir.exists():
        print("No migrations directory found.")
        print("Run 'python manage.py makemigrations' to create migrations.")
        return

    db_url = _get_database_url()
    project_root = _find_project_root() or Path.cwd()

    if plan:
        planned = executor.migrate(
            target=target,
            database_url=db_url,
            project_root=project_root,
            plan=True,
            fake_initial=fake_initial,
        )
        if not planned:
            print("Planned migrations: (none)")
        else:
            print("Planned migrations:")
            for name in planned:
                print(f"  {name}")
        return

    applied = executor.migrate(
        target=target,
        database_url=db_url,
        project_root=project_root,
        fake=fake,
        fake_initial=fake_initial,
    )

    if target == "zero":
        if applied:
            for name in applied:
                print(f"  Unapplying {name}... OK")
        else:
            print("  No migrations to unapply.")
    elif applied:
        print("Running migrations:")
        for name in applied:
            prefix = "  Faking" if fake else "  Applying"
            print(f"{prefix} {name}... OK")
    else:
        print("  No migrations to apply.")


def rollback(
    steps: int = 1,
    migrations_dir: str | None = None,
) -> None:
    """
    Rollback the last N migrations.

    Args:
        steps: Number of migrations to roll back.
        migrations_dir: Override migrations directory.
    """
    from zeeb_orm.migrations import executor

    mig_dir = _get_migrations_dir(migrations_dir)
    if not mig_dir.exists():
        print("No migrations directory found.")
        return

    db_url = _get_database_url()
    project_root = _find_project_root() or Path.cwd()

    # Get current state and determine target
    status = executor.showmigrations(database_url=db_url, project_root=project_root)
    applied = [name for name, is_applied in status if is_applied]

    if not applied:
        print("No migrations to roll back.")
        return

    # Determine target: roll back `steps` from the end
    if steps >= len(applied):
        target = "zero"
    else:
        target = applied[-(steps + 1)]

    rolled_back = executor.migrate(
        target=target,
        database_url=db_url,
        project_root=project_root,
    )

    if rolled_back:
        for name in rolled_back:
            print(f"  Unapplying {name}... OK")
    else:
        print("  No migrations to roll back.")


def showmigrations(migrations_dir: str | None = None) -> None:
    """
    Show all migrations and their applied status.

    Like Django's ``python manage.py showmigrations``.
    """
    from zeeb_orm.migrations import executor

    mig_dir = _get_migrations_dir(migrations_dir)
    if not mig_dir.exists():
        print("No migrations directory found.")
        return

    db_url = _get_database_url()
    project_root = _find_project_root() or Path.cwd()

    status = executor.showmigrations(database_url=db_url, project_root=project_root)

    if not status:
        print("No migrations.")
        return

    for name, is_applied in status:
        marker = "[X]" if is_applied else "[ ]"
        print(f" {marker} {name}")


def current(migrations_dir: str | None = None) -> str | None:
    """Show the last applied migration."""
    from zeeb_orm.migrations import executor

    mig_dir = _get_migrations_dir(migrations_dir)
    if not mig_dir.exists():
        print("No migrations directory found.")
        return None

    db_url = _get_database_url()
    project_root = _find_project_root() or Path.cwd()

    status = executor.showmigrations(database_url=db_url, project_root=project_root)
    applied = [name for name, is_applied in status if is_applied]

    if applied:
        last = applied[-1]
        print(f"Current: {last}")
        return last
    else:
        print("Current: (no migrations applied)")
        return None


def squashmigrations(
    start: str,
    end: str,
    squashed_name: str | None = None,
    migrations_dir: str | None = None,
    no_optimize: bool = False,
) -> str | None:
    """
    Squash migrations from *start* through *end* into a single file.

    Like Django's ``python manage.py squashmigrations``.

    The resulting squashed migration replaces the range of migrations it
    covers.  When it is applied for the first time the individual migrations
    in the squashed range are skipped; when the squashed migration itself has
    been applied everywhere the original files can be deleted.

    Args:
        start: Name of the first migration in the range (inclusive).
        end: Name of the last migration in the range (inclusive).
        squashed_name: Optional human-readable name for the squashed file.
        migrations_dir: Override migrations directory.
        no_optimize: Skip the optimizer step and keep all operations as-is.

    Returns:
        The squashed migration name, or None on error.
    """
    from zeeb_orm.migrations.executor import list_migration_files, load_migration
    from zeeb_orm.migrations.optimizer import optimize
    from zeeb_orm.migrations.writer import write_migration, _slugify, next_migration_number

    mig_dir = _get_migrations_dir(migrations_dir)
    if not mig_dir.exists():
        print("No migrations directory found.")
        return None

    all_migrations = list_migration_files(mig_dir)
    all_names = [name for name, _ in all_migrations]

    if start not in all_names:
        print(f"Error: Migration '{start}' not found.")
        return None
    if end not in all_names:
        print(f"Error: Migration '{end}' not found.")
        return None

    start_idx = all_names.index(start)
    end_idx = all_names.index(end)

    if start_idx > end_idx:
        print(f"Error: '{start}' comes after '{end}'.")
        return None

    squash_range = all_migrations[start_idx: end_idx + 1]

    # Collect all operations in order
    all_ops = []
    for _name, path in squash_range:
        mig = load_migration(path)
        all_ops.extend(mig.operations)

    # Optionally optimize
    if not no_optimize:
        optimized_ops = optimize(all_ops)
        saved = len(all_ops) - len(optimized_ops)
        if saved:
            print(f"Optimized {len(all_ops)} operations down to {len(optimized_ops)} ({saved} removed).")
    else:
        optimized_ops = all_ops

    # Dependencies: the squashed migration depends on everything *before* start
    if start_idx > 0:
        dependencies = [all_names[start_idx - 1]]
    else:
        dependencies = []

    # Name for the squashed file
    if squashed_name:
        slug = _slugify(squashed_name)
    else:
        slug = f"squashed_{_slugify(start)}_to_{_slugify(end)}"

    # Write squashed migration using the number just before start (with a
    # _squashed suffix so it sorts into the right position)
    squash_number = int(start[:4]) if start[:4].isdigit() else (start_idx + 1)
    filename = f"{squash_number:04d}_{slug}.py"
    filepath = mig_dir / filename

    if filepath.exists():
        print(f"Error: File '{filename}' already exists. Choose a different name.")
        return None

    migration_name = filepath.stem
    from zeeb_orm.migrations.writer import _render_migration
    content = _render_migration(
        migration_name=migration_name,
        operations=optimized_ops,
        dependencies=dependencies,
        initial=(start_idx == 0),
    )

    # Add a header comment listing which migrations are replaced
    replaced = [name for name, _ in squash_range]
    replaced_comment = (
        "# This migration replaces the following migrations:\n"
        + "".join(f"#   - {n}\n" for n in replaced)
        + "#\n"
        + "# Once this migration has been applied everywhere, the original\n"
        + "# migration files can be deleted.\n"
    )
    # Insert after the docstring closing '''
    content = content.replace(
        'from zeeb_orm.migrations import Migration, operations',
        replaced_comment + 'from zeeb_orm.migrations import Migration, operations',
        1,
    )

    filepath.write_text(content)

    print(f"Squashed migration created: {filename}")
    print(f"  Replaces: {', '.join(replaced)}")
    print(f"  Operations: {len(optimized_ops)}")
    print(f"\nReview '{filename}', then commit it alongside the original files.")
    print("After deploying, you may delete the original migration files.")

    return migration_name
