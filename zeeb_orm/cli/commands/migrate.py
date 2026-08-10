"""Migration commands — makemigrations, migrate, showmigrations.

No subprocess calls, no Alembic config files visible to the user: these drive
the ``zeeb_orm.migrations`` engine directly. Every command accepts ``--json``
and reports through :mod:`zeeb_orm.cli.output`, so a caller does not have to
parse prose to learn what happened.
"""

import sys
from pathlib import Path
from typing import Any

from zeeb_orm.cli.output import fail, no_project, ok
from zeeb_orm.scaffold.naming import find_project_root


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
    """Initialize the migrations directory.

    Migration files live flat in this directory (``0001_initial.py``, …), which
    is what ``write_migration`` produces and what the executor, the state module
    and the pre-flight checks read.
    """
    project_root = find_project_root() or Path.cwd()
    migrations_dir = project_root / directory

    if migrations_dir.exists():
        print(f"Migrations already initialized in '{directory}'")
        return 0

    migrations_dir.mkdir(parents=True)
    # Keep the empty directory in version control so a fresh clone has it.
    (migrations_dir / ".gitkeep").write_text("")
    print(f"Created migrations directory: {directory}")
    return 0


def run_makemigrations(
    name: str | None,
    empty: bool,
    check: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
) -> int:
    """Create new migration files by detecting model changes."""
    from zeeb_orm.migrations.autodetector import detect_changes
    from zeeb_orm.migrations.executor import list_migration_files
    from zeeb_orm.migrations.writer import write_migration

    project_root = find_project_root()
    if project_root is None:
        return no_project("makemigrations", json_output=json_output)

    migrations_dir = project_root / "migrations"
    if not check and not dry_run:
        migrations_dir.mkdir(parents=True, exist_ok=True)

    # Register all models
    _register_models(project_root)

    installed_apps = [a.replace("apps.", "") for a in get_installed_apps(project_root)]
    scanned = ["Checking for model changes in installed apps:"]
    scanned += [f"  - {app_name}" for app_name in installed_apps]

    if empty:
        if check or dry_run:
            return ok(
                "An empty migration would be created.",
                json_output=json_output,
                lines=(*scanned, "Empty migration would be created."),
                would_create=True,
                apps=installed_apps,
            )
        filepath = write_migration(migrations_dir, operations=[], name=name or "empty")
        return ok(
            f"Created empty migration {filepath.name}.",
            json_output=json_output,
            lines=(
                *scanned,
                "\nMigrations for 'all apps':",
                f"  {filepath.name}",
                "\nEmpty migration created — edit it to add custom operations.",
            ),
            created=filepath.name,
            operations=[],
            apps=installed_apps,
        )

    operations = detect_changes(migrations_dir=str(migrations_dir))
    described = [op.describe() for op in operations]

    if not operations:
        return ok(
            "No changes detected.",
            json_output=json_output,
            lines=(*scanned, "\nNo changes detected."),
            changes_detected=False,
            operations=[],
            apps=installed_apps,
        )

    # --check: the CI question "did someone change a model without migrating?"
    if check:
        return fail(
            f"{len(operations)} model change(s) have no migration.",
            code="invalid_input",
            next_command="python manage.py makemigrations",
            json_output=json_output,
            pending_operations=described,
            apps=installed_apps,
        )

    if dry_run:
        from zeeb_orm.migrations.writer import _auto_name, _slugify, next_migration_number

        number = next_migration_number(migrations_dir) if migrations_dir.exists() else 1
        slug = _slugify(name) if name else (_auto_name(operations) if number > 1 else "initial")
        filename = f"{number:04d}_{slug}.py"
        return ok(
            f"{filename} would be created ({len(operations)} operation(s)).",
            json_output=json_output,
            lines=(
                *scanned,
                "\nMigrations for 'all apps' (dry run — no files written):",
                f"  {filename}",
                *(f"    - {line}" for line in described),
            ),
            would_create=filename,
            operations=described,
            apps=installed_apps,
        )

    initial = len(list_migration_files(migrations_dir)) == 0
    filepath = write_migration(
        migrations_dir, operations=operations, name=name, initial=initial
    )
    return ok(
        f"Created {filepath.name} with {len(operations)} operation(s).",
        json_output=json_output,
        lines=(
            *scanned,
            "\nMigrations for 'all apps':",
            f"  {filepath.name}",
            *(f"    - {line}" for line in described),
            "\nRun 'python manage.py migrate' to apply.",
        ),
        created=filepath.name,
        operations=described,
        changes_detected=True,
        apps=installed_apps,
        next_command="python manage.py migrate",
    )


def run_migrate(
    migration: str | None,
    rollback_steps: int | None,
    fake: bool,
    fake_initial: bool = False,
    plan: bool = False,
    json_output: bool = False,
) -> int:
    """Apply or rollback migrations."""
    from zeeb_orm.migrations import executor

    project_root = find_project_root()
    if project_root is None:
        return no_project("migrate", json_output=json_output)

    migrations_dir = project_root / "migrations"
    if not migrations_dir.exists():
        return fail(
            "No migrations directory to apply.",
            code="file_not_found",
            next_command="python manage.py makemigrations",
            json_output=json_output,
            searched_in=str(migrations_dir),
        )

    settings = load_project_settings(project_root)
    db_url = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")

    if rollback_steps is not None:
        status = executor.showmigrations(database_url=db_url, project_root=project_root)
        applied = [name for name, is_applied in status if is_applied]

        if not applied:
            return ok(
                "No migrations to roll back.",
                json_output=json_output,
                lines=(f"Rolling back {rollback_steps} migration(s)...",
                       "  No migrations to roll back."),
                unapplied=[],
            )

        target = "zero" if rollback_steps >= len(applied) else applied[-(rollback_steps + 1)]

        if plan:
            planned = executor.migrate(
                target=target, database_url=db_url, project_root=project_root,
                plan=True, fake_initial=fake_initial,
            )
            return ok(
                f"{len(planned)} migration(s) would be rolled back.",
                json_output=json_output,
                lines=("Planned rollback:", *(f"  {n}" for n in planned)),
                plan=planned, target=target,
            )

        rolled_back = executor.migrate(
            target=target, database_url=db_url, project_root=project_root
        )
        return ok(
            f"Rolled back {len(rolled_back)} migration(s).",
            json_output=json_output,
            lines=(f"Rolling back {rollback_steps} migration(s)...",
                   *(f"  Unapplying {n}... OK" for n in rolled_back)),
            unapplied=rolled_back, target=target,
        )

    if migration:
        if migration == "zero":
            target = "zero"
        else:
            known = [name for name, _ in executor.list_migration_files(migrations_dir)]
            target = next(
                (name for name in known if name == migration or name.startswith(migration)),
                None,
            )
            if target is None:
                return fail(
                    f"Migration '{migration}' not found.",
                    code="file_not_found",
                    next_command="python manage.py showmigrations",
                    json_output=json_output,
                    suggestions=known[-3:],
                    available=known,
                )

        if plan:
            planned = executor.migrate(
                target=target, database_url=db_url, project_root=project_root,
                plan=True, fake_initial=fake_initial,
            )
            return ok(
                f"{len(planned)} migration(s) would run to reach {target}.",
                json_output=json_output,
                lines=(f"Migrating to: {target}", "Planned migrations:",
                       *(f"  {n}" for n in planned)),
                plan=planned, target=target,
            )

        # Direction matters for the wording: a target that is already applied
        # means this is a rollback, not a forward migration.
        status = executor.showmigrations(database_url=db_url, project_root=project_root)
        applied_set = {name for name, is_applied in status if is_applied}
        is_rollback = target == "zero" or target in applied_set

        result = executor.migrate(
            target=target, database_url=db_url, project_root=project_root,
            fake=fake, fake_initial=fake_initial,
        )
        verb = "Unapplying" if is_rollback else ("Faking" if fake else "Applying")
        lines = [f"Migrating to: {target}"]
        lines += [f"  {verb} {name}... OK" for name in result]
        if not result:
            lines.append("  No migrations to unapply." if is_rollback else
                         "  No migrations to apply.")
        return ok(
            f"{len(result)} migration(s) {'unapplied' if is_rollback else 'applied'}.",
            json_output=json_output,
            lines=tuple(lines),
            target=target,
            faked=fake,
            **({"unapplied": result} if is_rollback else {"applied": result}),
        )

    if plan:
        planned = executor.migrate(
            database_url=db_url, project_root=project_root,
            plan=True, fake_initial=fake_initial,
        )
        return ok(
            f"{len(planned)} migration(s) would be applied.",
            json_output=json_output,
            lines=(("Planned migrations:", *(f"  {n}" for n in planned)) if planned
                   else ("Planned migrations: (none)",)),
            plan=planned,
        )

    applied = executor.migrate(
        database_url=db_url, project_root=project_root,
        fake=fake, fake_initial=fake_initial,
    )
    verb = "Faking" if fake else "Applying"
    lines = ["Running migrations:"]
    lines += ([f"  {verb} {name}... OK" for name in applied] if applied
              else ["  No migrations to apply."])
    return ok(
        f"{len(applied)} migration(s) applied.",
        json_output=json_output,
        lines=tuple(lines),
        applied=applied,
        faked=fake,
    )


def run_showmigrations(json_output: bool = False) -> int:
    """Show which migrations exist and which of them are applied."""
    from zeeb_orm.migrations import executor

    project_root = find_project_root()
    if project_root is None:
        return no_project("showmigrations", json_output=json_output)

    migrations_dir = project_root / "migrations"
    if not migrations_dir.exists() or not (
        status := executor.showmigrations(
            database_url=load_project_settings(project_root)
            .get("DATABASE", {})
            .get("url", "sqlite:///db.sqlite3"),
            project_root=project_root,
        )
    ):
        return ok(
            "No migrations have been created yet.",
            json_output=json_output,
            lines=("No migrations found. Run 'python manage.py makemigrations' first.",),
            migrations=[],
            applied=0,
            pending=0,
            next_command="python manage.py makemigrations",
        )

    pending = [name for name, is_applied in status if not is_applied]
    return ok(
        f"{len(status) - len(pending)} applied, {len(pending)} pending.",
        json_output=json_output,
        lines=tuple(
            f" {'[X]' if is_applied else '[ ]'} {name}" for name, is_applied in status
        ),
        migrations=[
            {"name": name, "applied": is_applied} for name, is_applied in status
        ],
        applied=len(status) - len(pending),
        pending=len(pending),
        **({"next_command": "python manage.py migrate"} if pending else {}),
    )


def run_squashmigrations(
    start: str,
    end: str,
    squashed_name: str | None = None,
    no_optimize: bool = False,
) -> int:
    """Squash a range of migrations into a single file."""
    from zeeb_orm.migrations.cli import squashmigrations

    project_root = find_project_root()
    if project_root is None:
        return no_project("squashmigrations", json_output=False)

    migrations_dir = str(project_root / "migrations")
    result = squashmigrations(
        start=start,
        end=end,
        squashed_name=squashed_name,
        migrations_dir=migrations_dir,
        no_optimize=no_optimize,
    )
    return 0 if result else 1
