"""
Django-style migration commands — programmatic API.

Provides ``makemigrations``, ``migrate``, ``showmigrations``, ``rollback``,
and ``current`` without any subprocess calls or Alembic config files.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Any


def _find_project_root() -> Path | None:
    from zeeb_orm.migrations.state import find_project_root
    return find_project_root()


def _get_database_url(project_root: Path | None = None) -> str:
    """The database URL, resolved exactly as ``manage.py migrate`` resolves it.

    ``DATABASE["url"]`` from the project's settings.py when it declares one,
    else the ORM configuration (``DATABASE_URL`` / default). A settings.py
    that does not import raises
    :class:`~zeeb_orm.conf.project.SettingsImportError`.
    """
    from zeeb_orm.conf.project import resolve_database_url

    if project_root is None:
        project_root = _find_project_root()
    return resolve_database_url(project_root)


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
        # Point zeeb_api's settings discovery at the target project so
        # AUTH_USER_MODEL resolves even when the process CWD is elsewhere
        # (zeeb_agents operates on projects by path).
        try:
            from zeeb_api.auth.backends import set_project_root
            set_project_root(project_root)
        except ImportError:
            pass  # zeeb_api is optional

        # Load settings to get INSTALLED_APPS and AUTH_USER_MODEL
        from zeeb_orm.migrations._settings import load_settings_module
        settings_module = load_settings_module(project_root)
        installed_apps: list[str] = (
            list(getattr(settings_module, "INSTALLED_APPS", []))
            if settings_module is not None else []
        )
        auth_user_model: str | None = (
            getattr(settings_module, "AUTH_USER_MODEL", None)
            if settings_module is not None else None
        )

        # Register auth models — Permission first (no FK dependencies)
        try:
            from zeeb_api.auth.models import Permission
            Permission._get_table()
            if not auth_user_model:
                from zeeb_api.auth.models import User
                User._get_table()
        except ImportError:
            pass  # zeeb_api is optional — no auth models to register
        except Exception as exc:
            warnings.warn(
                f"Could not register zeeb_api auth models: {exc}",
                stacklevel=2,
            )

        # Register models from installed apps (including custom user models).
        # Two phases: first import every app's models module so ALL model
        # classes land in the registry, then build tables. This makes
        # cross-app string references (e.g. ForeignKey("accounts.User") from
        # an app listed earlier in INSTALLED_APPS) independent of app order.
        # Failures here are fatal, not warnings — schema tooling running
        # against an incomplete model state can generate destructive
        # migrations (missing tables diff as DeleteModel).
        from zeeb_orm.exceptions import ModelRegistrationError

        failures: list[str] = []
        app_models: list[tuple[str, type[Model]]] = []
        seen_models: set[type[Model]] = set()
        for app in installed_apps:
            if app == "zeeb_auth":
                continue
            clean_name = app.replace("apps.", "")
            import importlib

            # Try multiple import paths for flexibility
            import_paths = [f"apps.{clean_name}.models"]
            if not app.startswith("apps."):
                import_paths.append(f"{app}.models")

            imported = False
            last_error: Exception | None = None
            module_missing = False
            for import_path in import_paths:
                try:
                    models_module = importlib.import_module(import_path)
                    for name in dir(models_module):
                        obj = getattr(models_module, name)
                        if (isinstance(obj, type) and issubclass(obj, Model)
                                and obj is not Model
                                and not getattr(obj._meta, 'abstract', False)
                                and obj not in seen_models):
                            seen_models.add(obj)
                            app_models.append((app, obj))
                    imported = True
                    break
                except ModuleNotFoundError as e:
                    last_error = e
                    # Only the models module itself (or its package) being
                    # absent is skippable; a ModuleNotFoundError raised by an
                    # import *inside* an existing models.py is a real failure
                    # that must not silently yield "No changes detected".
                    module_missing = e.name is not None and import_path.startswith(e.name)
                    if not module_missing:
                        break
                except Exception as e:
                    last_error = e
                    break  # Errors inside an existing module are real problems

            if not imported and last_error is not None:
                if module_missing:
                    # App without an importable models module — skippable.
                    warnings.warn(
                        f"Could not import models from app '{app}': {last_error}",
                        stacklevel=2,
                    )
                else:
                    failures.append(f"app '{app}': {last_error!r}")

        for app, model_cls in app_models:
            try:
                model_cls._get_table()
            except Exception as exc:
                failures.append(
                    f"model '{model_cls.__name__}' (app '{app}'): {exc}"
                )

        if failures:
            raise ModelRegistrationError(
                "Could not register all models from INSTALLED_APPS — refusing "
                "to continue with an incomplete model state:\n  - "
                + "\n  - ".join(failures)
            )

        # Clear cached user model so it re-resolves with sys.path set up
        try:
            from zeeb_api.auth.backends import clear_user_model_cache
            clear_user_model_cache()
        except ImportError:
            pass  # zeeb_api is optional
        except Exception as exc:
            warnings.warn(
                f"Could not clear cached user model: {exc}",
                stacklevel=2,
            )

        # Register UserPermission after app models so that the user FK
        # target (default User or custom user model) is already available
        try:
            from zeeb_api.auth.models import UserPermission
            UserPermission._get_table()
        except ImportError:
            pass  # zeeb_api is optional
        except Exception as exc:
            warnings.warn(
                f"Could not register UserPermission model: {exc}",
                stacklevel=2,
            )

        # Resolve any pending reverse relations (callable FK targets that
        # were deferred during import are now resolvable)
        try:
            from zeeb_orm.models.base import _process_pending_relations
            _process_pending_relations(resolve_callables=True)
        except Exception as exc:
            warnings.warn(
                f"Could not resolve pending model relations: {exc}",
                stacklevel=2,
            )
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
    accept_renames: bool = False,
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
        accept_renames: Emit ``RenameField`` for likely renames (a removed
                 and an added field with the same definition) instead of
                 ``RemoveField`` + ``AddField``. Without it they are only
                 reported, on stderr.

    Returns:
        The migration name (e.g. ``"0001_initial"``) or None.
    """
    from zeeb_orm.migrations.autogen import make_migration

    project_root = _find_project_root() or Path.cwd()
    mig_dir = _get_migrations_dir(migrations_dir)

    # Auto-create migrations directory (unless --check / --dry-run)
    if not check and not dry_run:
        mig_dir.mkdir(parents=True, exist_ok=True)

    # Register all models so metadata is populated
    _register_models(project_root)

    if empty and (check or dry_run):
        print("Empty migration would be created.")
        return None

    draft = make_migration(
        mig_dir,
        name=message,
        empty=empty,
        dry_run=check or dry_run,
        accept_renames=accept_renames,
    )
    for warning in draft.warnings():
        print(f"WARNING: {warning}", file=sys.stderr)

    if not draft.changed:
        print("No changes detected.")
        return None

    # --check: report and signal with a non-zero exit
    if check:
        print("Pending model changes detected:")
        for op in draft.operations:
            print(f"    - {op.describe()}")
        raise SystemExit(1)

    if dry_run:
        print("Migrations for 'all apps' (dry run — no files written):")
        print(f"  {draft.name}.py")
        for op in draft.operations:
            print(f"    - {op.describe()}")
        return None

    print("Migrations for 'all apps':")
    print(f"  {draft.name}.py")
    if empty:
        print("\nEmpty migration created — edit it to add custom operations.")
    for op in draft.operations:
        print(f"    - {op.describe()}")

    return draft.name


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
        target: Target migration name (or a unique prefix such as ``"0003"``),
                ``"zero"`` to rollback all, or None to apply all pending.
        migrations_dir: Override migrations directory.
        fake: Mark as applied without running.
        fake_initial: If True, skip the first migration when tables already
                      exist (for adopting migrations in an existing project).
        plan: If True, print what would happen without applying anything.

    Raises:
        UnknownMigrationError: *target* matches no migration, or several.
    """
    from zeeb_orm.migrations import executor

    mig_dir = _get_migrations_dir(migrations_dir)
    if not mig_dir.exists():
        print("No migrations directory found.")
        print("Run 'python manage.py makemigrations' to create migrations.")
        return

    project_root = _find_project_root() or Path.cwd()
    db_url = _get_database_url(project_root)

    if plan:
        planned = executor.migrate(
            target=target,
            database_url=db_url,
            project_root=project_root,
            plan=True,
            fake_initial=fake_initial,
            migrations_dir=mig_dir,
        )
        if not planned:
            print("Planned migrations: (none)")
        else:
            print("Planned migrations:")
            for name in planned:
                print(f"  {name}")
        return

    result = executor.migrate_with_result(
        target=target,
        database_url=db_url,
        project_root=project_root,
        fake=fake,
        fake_initial=fake_initial,
        migrations_dir=mig_dir,
    )

    for name in result.unapplied:
        print(f"  Unapplying {name}... OK")
    if result.applied:
        print("Running migrations:")
        for name in result.applied:
            prefix = "  Faking" if fake else "  Applying"
            print(f"{prefix} {name}... OK")
    if not result.names:
        print("  No migrations to unapply." if target == "zero" else "  No migrations to apply.")


def rollback(
    steps: int = 1,
    migrations_dir: str | None = None,
) -> None:
    """
    Rollback the last N migrations.

    Args:
        steps: Number of migrations to roll back (a squash counts as the
               migrations it replaces while their files exist, else as one).
        migrations_dir: Override migrations directory.
    """
    from zeeb_orm.migrations import executor

    mig_dir = _get_migrations_dir(migrations_dir)
    if not mig_dir.exists():
        print("No migrations directory found.")
        return

    project_root = _find_project_root() or Path.cwd()
    db_url = _get_database_url(project_root)

    rolled_back = executor.rollback(
        steps, database_url=db_url, project_root=project_root, migrations_dir=mig_dir
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

    project_root = _find_project_root() or Path.cwd()
    db_url = _get_database_url(project_root)

    status = executor.showmigrations(
        database_url=db_url, project_root=project_root, migrations_dir=mig_dir
    )

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

    project_root = _find_project_root() or Path.cwd()
    db_url = _get_database_url(project_root)

    status = executor.showmigrations(
        database_url=db_url, project_root=project_root, migrations_dir=mig_dir
    )
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

    Like Django's ``python manage.py squashmigrations``. The generated file
    records the squashed migrations in its ``replaces`` attribute, so the
    executor treats the originals as superseded: it skips them when applying
    and marks them applied automatically when the squashed migration runs.
    A database that has only some of them applied finishes them one by one
    instead, and records the squash once they all are.

    The squashed file and the original files can therefore coexist safely —
    operations are not applied twice. Once the squashed migration has been
    deployed everywhere, the original migration files may be deleted.

    ``RunPython`` functions defined in the squashed files are copied into the
    new file. A range containing a migration that overrides
    ``pre_migrate``/``post_migrate`` is refused: those hooks cannot be carried
    into a squash.

    Args:
        start: Name of the first migration in the range (inclusive).
        end: Name of the last migration in the range (inclusive).
        squashed_name: Optional human-readable name for the squashed file.
        migrations_dir: Override migrations directory.
        no_optimize: Skip the optimizer step and keep all operations as-is.

    Returns:
        The squashed migration name, or None on error.
    """
    from zeeb_orm.migrations.executor import MigrationGraph, _load_all
    from zeeb_orm.migrations.migration import Migration
    from zeeb_orm.migrations.optimizer import optimize
    from zeeb_orm.migrations.serializer import MigrationSerializationError
    from zeeb_orm.migrations.writer import _render, _slugify, save_migration

    mig_dir = _get_migrations_dir(migrations_dir)
    if not mig_dir.exists():
        print("No migrations directory found.")
        return None

    loaded = _load_all(mig_dir)
    all_names = [name for name, _, _ in loaded]
    migrations = {name: mig for name, _, mig in loaded}

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

    in_range = all_names[start_idx: end_idx + 1]
    graph = MigrationGraph(loaded)

    # A migration already squashed into one outside the range would be applied
    # twice (once by each squash).
    for name in in_range:
        owner = graph.resolve(name)
        if owner != name and owner not in in_range:
            print(f"Error: '{name}' is already squashed into '{owner}'; squash that instead.")
            return None
    for name in in_range:
        mig = migrations[name]
        hooks = [
            hook for hook in ("pre_migrate", "post_migrate")
            if getattr(type(mig), hook) is not getattr(Migration, hook)
        ]
        if hooks:
            print(
                f"Error: '{name}' overrides {' and '.join(hooks)}, which a squashed "
                f"migration cannot carry over. Squash a range without it."
            )
            return None

    # Operations in the order a fresh database runs them; a squash inside the
    # range stands in for the originals it replaces.
    members = [name for name in graph.order if name in in_range]
    all_ops: list[Any] = []
    for name in members:
        all_ops.extend(migrations[name].operations)

    # Optionally optimize
    if not no_optimize:
        optimized_ops = optimize(all_ops)
        saved = len(all_ops) - len(optimized_ops)
        if saved:
            print(
                f"Optimized {len(all_ops)} operations down to {len(optimized_ops)} "
                f"({saved} removed)."
            )
    else:
        optimized_ops = all_ops

    # Everything the squash replaces: the range, plus what squashes in it replace.
    replaced: list[str] = []
    for name in in_range:
        for covered in [*migrations[name].replaces, name]:
            if covered not in replaced:
                replaced.append(covered)

    # Dependencies: whatever the squashed migrations depend on outside the
    # range — normally just the migration before start.
    dependencies: list[str] = []
    for name in members:
        for dep in graph.dependencies(name):
            if dep not in replaced and dep not in dependencies:
                dependencies.append(dep)
    if not dependencies and start_idx > 0:
        before = graph.resolve(all_names[start_idx - 1])
        if before not in replaced:
            dependencies = [before]

    # Name for the squashed file
    if squashed_name:
        slug = _slugify(squashed_name)
    else:
        slug = f"squashed_{_slugify(start)}_to_{_slugify(end)}"

    # Numbered right after the end migration — or, when a later migration
    # already holds that number, after the last migration: every number stays
    # unique, so "0003" names one file. Apply order follows the dependency
    # graph, so a squash numbered after migrations that depend on it still
    # runs before them.
    end_number = int(end[:4]) if end[:4].isdigit() else end_idx + 1
    taken = {int(n[:4]) for n in all_names if n[:4].isdigit()}
    squash_number = end_number + 1 if end_number + 1 not in taken else max(taken) + 1
    filename = f"{squash_number:04d}_{slug}.py"
    filepath = mig_dir / filename

    if filepath.exists():
        print(f"Error: File '{filename}' already exists. Choose a different name.")
        return None

    migration_name = filepath.stem
    comment = "\n".join(
        [
            "This squashed migration replaces the following migrations:",
            *(f"  - {n}" for n in replaced),
            "",
            "The 'replaces' attribute ensures that when this migration is applied,",
            "the replaced migrations are automatically marked as applied.",
            "Once this migration has been deployed everywhere, the original",
            "migration files can be safely deleted.",
        ]
    )
    try:
        content, renames = _render(
            migration_name=migration_name,
            operations=optimized_ops,
            dependencies=dependencies,
            initial=any(migrations[name].initial for name in members),
            replaces=replaced,
            comment=comment,
            atomic=all(migrations[name].atomic for name in members),
        )
        save_migration(filepath, content, optimized_ops, renames)
    except MigrationSerializationError as exc:
        print(f"Error: {exc}")
        return None

    print(f"Squashed migration created: {filename}")
    print(f"  Replaces: {', '.join(replaced)}")
    print(f"  Operations: {len(optimized_ops)}")
    print(f"\nReview '{filename}', then commit it alongside the original files.")
    print("After deploying, you may delete the original migration files.")

    return migration_name
