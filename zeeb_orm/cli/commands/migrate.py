"""Migration commands — makemigrations, migrate, showmigrations.

No subprocess calls, no Alembic config files visible to the user: these drive
the ``zeeb_orm.migrations`` engine directly. Every command accepts ``--json``
and reports through :mod:`zeeb_orm.cli.output`, so a caller does not have to
parse prose to learn what happened.
"""

from pathlib import Path
from typing import Any

from zeeb_orm.cli.output import fail, no_project, ok
from zeeb_orm.conf.project import SettingsImportError
from zeeb_orm.scaffold.naming import find_project_root


def load_project_settings(project_root: Path) -> dict[str, Any]:
    """``DATABASE`` and ``INSTALLED_APPS`` from the project's settings module.

    Resolved by :mod:`zeeb_orm.conf.project` (``[tool.zeeb] settings_module``
    first, then a sorted scan that skips ``apps/``). Without a settings module
    the database is the ORM's configured one (``DATABASE_URL`` / default).

    Raises:
        SettingsImportError: settings.py exists but does not import — never
            answered with a fallback database nobody configured.
    """
    from zeeb_orm.conf.project import load_settings_module, resolve_database_url

    module = load_settings_module(project_root)
    database = getattr(module, "DATABASE", None) if module is not None else None
    if not (isinstance(database, dict) and database.get("url")):
        database = {"url": resolve_database_url(None)}
    return {
        "DATABASE": database,
        "INSTALLED_APPS": list(getattr(module, "INSTALLED_APPS", []) or []),
    }


def get_installed_apps(project_root: Path) -> list[str]:
    """Get list of installed apps from settings."""
    settings = load_project_settings(project_root)
    apps = list(settings.get("INSTALLED_APPS", []))
    if "zeeb_auth" not in apps:
        apps.insert(0, "zeeb_auth")
    return apps


def _database_url(project_root: Path) -> str:
    return load_project_settings(project_root)["DATABASE"]["url"]


def _settings_failure(command: str, exc: Exception, *, json_output: bool) -> int:
    """The failure every migration command reports for a broken settings.py."""
    return fail(
        f"The project settings could not be imported, so the database to "
        f"{command} is unknown: {exc}",
        code="invalid_input",
        next_command="python manage.py check",
        json_output=json_output,
        settings_path=str(getattr(exc, "path", "")) or None,
    )


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


def _migration_failure(exc: Exception, *, json_output: bool) -> int:
    """The failure a migration command reports for an engine refusal."""
    from zeeb_orm.migrations.state import (
        IrreversibleError,
        MigrationLayoutError,
        UnknownMigrationError,
    )

    if isinstance(exc, UnknownMigrationError):
        return fail(
            str(exc),
            code="file_not_found",
            next_command="python manage.py showmigrations",
            json_output=json_output,
            suggestions=exc.candidates or exc.available[-3:],
            available=exc.available,
        )
    if isinstance(exc, MigrationLayoutError):
        return fail(
            str(exc),
            code="invalid_input",
            next_command="mv migrations/versions/*.py migrations/",
            json_output=json_output,
        )
    if isinstance(exc, IrreversibleError):
        return fail(
            str(exc),
            code="invalid_input",
            next_command="python manage.py showmigrations",
            json_output=json_output,
            recoverable=False,
        )
    return fail(
        str(exc),
        code="invalid_input",
        next_command="python manage.py showmigrations",
        json_output=json_output,
    )


def run_makemigrations(
    name: str | None,
    empty: bool,
    check: bool = False,
    dry_run: bool = False,
    json_output: bool = False,
    accept_renames: bool = False,
) -> int:
    """Create new migration files by detecting model changes.

    A removed and an added field with the same definition on one model are
    reported as a likely rename (``possible_renames`` in the JSON data and a
    WARNING line): written as RemoveField + AddField they drop the column's
    data. ``accept_renames`` emits ``RenameField`` for them instead.
    """
    from zeeb_orm.migrations.autodetector import MigrationReplayError
    from zeeb_orm.migrations.autogen import make_migration
    from zeeb_orm.migrations.state import MigrationError

    project_root = find_project_root()
    if project_root is None:
        return no_project("makemigrations", json_output=json_output)

    migrations_dir = project_root / "migrations"
    if not check and not dry_run:
        migrations_dir.mkdir(parents=True, exist_ok=True)

    # Register all models
    try:
        _register_models(project_root)
        installed_apps = [a.replace("apps.", "") for a in get_installed_apps(project_root)]
    except SettingsImportError as exc:
        return _settings_failure("makemigrations", exc, json_output=json_output)
    scanned = ["Checking for model changes in installed apps:"]
    scanned += [f"  - {app_name}" for app_name in installed_apps]

    if empty and (check or dry_run):
        return ok(
            "An empty migration would be created.",
            json_output=json_output,
            lines=(*scanned, "Empty migration would be created."),
            would_create=True,
            apps=installed_apps,
        )

    try:
        draft = make_migration(
            migrations_dir,
            name=name,
            empty=empty,
            dry_run=check or dry_run,
            accept_renames=accept_renames,
        )
    except MigrationReplayError as exc:
        return fail(
            str(exc),
            code="invalid_input",
            next_command="python manage.py showmigrations",
            json_output=json_output,
            recoverable=False,
        )
    except MigrationError as exc:
        return _migration_failure(exc, json_output=json_output)

    if empty:
        return ok(
            f"Created empty migration {draft.name}.py.",
            json_output=json_output,
            lines=(
                *scanned,
                "\nMigrations for 'all apps':",
                f"  {draft.name}.py",
                "\nEmpty migration created — edit it to add custom operations.",
            ),
            created=f"{draft.name}.py",
            operations=[],
            apps=installed_apps,
        )

    described = [op.describe() for op in draft.operations]
    report = draft.report
    assert report is not None
    warning_lines = tuple(f"WARNING: {line}" for line in draft.warnings())
    findings = {
        "possible_renames": [r.as_dict() for r in report.possible_renames],
        "renamed": [r.as_dict() for r in report.renamed],
        "skipped_during_replay": list(report.skipped),
        "warnings": draft.warnings(),
    }

    if not draft.changed:
        return ok(
            "No changes detected.",
            json_output=json_output,
            lines=(*scanned, *warning_lines, "\nNo changes detected."),
            changes_detected=False,
            operations=[],
            apps=installed_apps,
            **findings,
        )

    # --check: the CI question "did someone change a model without migrating?"
    if check:
        return fail(
            f"{len(described)} model change(s) have no migration.",
            code="invalid_input",
            next_command="python manage.py makemigrations",
            json_output=json_output,
            pending_operations=described,
            apps=installed_apps,
            **findings,
        )

    filename = f"{draft.name}.py"
    if dry_run:
        return ok(
            f"{filename} would be created ({len(described)} operation(s)).",
            json_output=json_output,
            lines=(
                *scanned,
                "\nMigrations for 'all apps' (dry run — no files written):",
                f"  {filename}",
                *(f"    - {line}" for line in described),
                *warning_lines,
            ),
            would_create=filename,
            operations=described,
            apps=installed_apps,
            **findings,
        )

    return ok(
        f"Created {filename} with {len(described)} operation(s).",
        json_output=json_output,
        lines=(
            *scanned,
            "\nMigrations for 'all apps':",
            f"  {filename}",
            *(f"    - {line}" for line in described),
            *warning_lines,
            "\nRun 'python manage.py migrate' to apply.",
        ),
        created=filename,
        operations=described,
        changes_detected=True,
        apps=installed_apps,
        next_command="python manage.py migrate",
        **findings,
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
    from zeeb_orm.migrations.state import MigrationError

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

    try:
        db_url = _database_url(project_root)
    except SettingsImportError as exc:
        return _settings_failure("migrate", exc, json_output=json_output)

    try:
        return _run_migrate(
            project_root, db_url, migration, rollback_steps, fake, fake_initial, plan,
            json_output=json_output,
        )
    except MigrationError as exc:
        return _migration_failure(exc, json_output=json_output)


def _run_migrate(
    project_root: Path,
    db_url: str,
    migration: str | None,
    rollback_steps: int | None,
    fake: bool,
    fake_initial: bool,
    plan: bool,
    *,
    json_output: bool,
) -> int:
    from zeeb_orm.migrations import executor

    if rollback_steps is not None:
        rollback = executor.rollback_with_result(
            rollback_steps, database_url=db_url, project_root=project_root,
            fake=fake, plan=plan,
        )
        target = rollback.target
        if target is None:
            return ok(
                "No migrations to roll back.",
                json_output=json_output,
                lines=(f"Rolling back {rollback_steps} migration(s)...",
                       "  No migrations to roll back."),
                unapplied=[],
            )

        if plan:
            planned = rollback.names
            return ok(
                f"{len(planned)} migration(s) would be rolled back.",
                json_output=json_output,
                lines=("Planned rollback:", *(f"  {n}" for n in planned)),
                plan=planned, target=target,
            )

        rolled_back = rollback.unapplied
        return ok(
            f"Rolled back {len(rolled_back)} migration(s).",
            json_output=json_output,
            lines=(f"Rolling back {rollback_steps} migration(s)...",
                   *(f"  Unapplying {n}... OK" for n in rolled_back)),
            unapplied=rolled_back, target=target,
        )

    if migration:
        if plan:
            planned_result = executor.migrate_with_result(
                target=migration, database_url=db_url, project_root=project_root,
                plan=True, fake_initial=fake_initial,
            )
            planned, target = planned_result.names, planned_result.target
            return ok(
                f"{len(planned)} migration(s) would run to reach {target}.",
                json_output=json_output,
                lines=(f"Migrating to: {target}", "Planned migrations:",
                       *(f"  {n}" for n in planned)),
                plan=planned, target=target,
            )

        result = executor.migrate_with_result(
            target=migration, database_url=db_url, project_root=project_root,
            fake=fake, fake_initial=fake_initial,
        )
        target = result.target
        is_rollback = target == "zero" or (bool(result.unapplied) and not result.applied)
        lines = [f"Migrating to: {target}"]
        lines += [f"  Unapplying {name}... OK" for name in result.unapplied]
        verb = "Faking" if fake else "Applying"
        lines += [f"  {verb} {name}... OK" for name in result.applied]
        if not result.names:
            lines.append("  No migrations to unapply." if is_rollback else
                         "  No migrations to apply.")
        data: dict[str, Any] = {"unapplied": result.unapplied} if result.unapplied else {}
        if result.applied or not result.unapplied:
            data["applied"] = result.applied
        summary = (
            f"{len(result.unapplied)} migration(s) unapplied." if is_rollback
            else f"{len(result.applied)} migration(s) applied."
        )
        return ok(
            summary,
            json_output=json_output,
            lines=tuple(lines),
            target=target,
            faked=fake,
            **data,
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
    try:
        db_url = _database_url(project_root)
    except SettingsImportError as exc:
        return _settings_failure("showmigrations", exc, json_output=json_output)
    if not migrations_dir.exists() or not (
        status := executor.showmigrations(database_url=db_url, project_root=project_root)
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
