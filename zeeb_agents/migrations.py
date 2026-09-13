"""Agent functions for migration management."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError, close_matches
from zeeb_agents._utils.project import (
    list_apps as list_apps_util,
)
from zeeb_agents._utils.project import (
    load_project_settings,
    require_project_root,
    resolve_db_url,
)

# A non-commented ``class Foo(... Model ...):`` line — a real model definition
# (as opposed to the commented example in the scaffolded models.py).
_MODEL_CLASS_RE = re.compile(r"^class\s+\w+\s*\([^)]*Model[^)]*\)\s*:", re.MULTILINE)


def _unregistered_apps_with_models(root: Path) -> list[str]:
    """Return apps that define models on disk but are absent from INSTALLED_APPS.

    These are the silent-failure case: ``make_migrations`` walks
    ``INSTALLED_APPS`` only, so an unregistered app's models are never seen and
    "No changes detected" is misleadingly reported.
    """
    installed = set(load_project_settings(root).get("INSTALLED_APPS", []) or [])
    unregistered: list[str] = []
    for app in list_apps_util(root):
        if f"apps.{app}" in installed:
            continue
        models_py = root / "apps" / app / "models.py"
        try:
            text = models_py.read_text(encoding="utf-8")
        except OSError:
            continue
        if _MODEL_CLASS_RE.search(text):
            unregistered.append(app)
    return unregistered


@agent_function
async def run_migrations(
    target: str | None = None,
    fake: bool = False,
    fake_initial: bool = False,
    project_root: Path | None = None,
) -> AgentResult:
    """Apply pending migrations — or move the schema to a named migration.

    Equivalent to ``python manage.py migrate [target] [--fake] [--fake-initial]``.
    Without arguments every pending migration is applied. ``target`` walks the
    schema forward or backward to that migration (``"zero"`` unapplies
    everything) — the repair move when a bad migration must be unapplied
    before its file is deleted. ``fake`` records migrations as applied without
    running them, for a database whose schema was changed by hand.

    Args:
        target: Migration name to migrate to (``"0003_add_status"``, or
            ``"zero"``). Default ``None`` applies all pending.
        fake: Mark as applied without executing (default false).
        fake_initial: Skip the initial migration when its tables already
            exist (default false).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        applied (list[str]): names of the migrations that were applied
            (or faked), in order; empty when there was nothing to do.
        unapplied (list[str]): names of the migrations that were rolled back
            to reach ``target``; empty otherwise.
        target (str | None): the target that was requested.
        fake (bool): whether the run was faked.

    Notes:
        - "Nothing to apply" is reported as ``success=True`` with
          ``applied=[]``, not as a failure.
        - An unknown ``target`` fails with ``file_not_found`` and close-match
          ``suggestions``.
    """
    root = project_root

    def _run() -> dict:
        from zeeb_orm.migrations import executor

        settings = load_project_settings(root)
        db_url = resolve_db_url(settings, root)
        status = executor.showmigrations(database_url=db_url, project_root=root)
        names = [name for name, _ in status]
        if target not in (None, "zero") and target not in names:
            raise AgentError(
                f"No migration named '{target}'.",
                code="file_not_found",
                suggestions=close_matches(target, names),
            )
        before = {name for name, is_applied in status if is_applied}
        executor.migrate(
            target=target,
            database_url=db_url,
            project_root=root,
            fake=fake,
            fake_initial=fake_initial,
        )
        status = executor.showmigrations(database_url=db_url, project_root=root)
        after = {name for name, is_applied in status if is_applied}
        order = [name for name, _ in status]
        return {
            "applied": [name for name in order if name in after - before],
            "unapplied": [name for name in reversed(order) if name in before - after],
            "target": target,
            "fake": fake,
        }

    result = await asyncio.to_thread(_run)
    if result["applied"]:
        verb = "Faked" if fake else "Applied"
        message = f"{verb} {len(result['applied'])} migration(s)"
    elif result["unapplied"]:
        message = f"Rolled back {len(result['unapplied'])} migration(s) to {target}"
    else:
        message = "No pending migrations"
    return AgentResult(success=True, message=message, data=result)


@agent_function
async def make_migrations(
    name: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Detect model changes and write a new migration file.

    Equivalent to ``python manage.py makemigrations``.

    Args:
        name: Optional human-readable suffix for the migration file name.
        project_id: The host-assigned project id (required).

    Returns data (always):
        created (str | None): the new migration file name, or ``None`` when
            no model changes were detected.
        operations (list[str]): human-readable descriptions of the schema
            operations in the migration (empty when ``created`` is ``None``).

    Notes:
        - "No changes detected" is reported as ``success=True`` with
          ``created=None`` — it is not a failure. Check ``data["created"]``
          to know whether a file was actually written.
        - When ``created`` is ``None`` but apps on disk define models that are
          **not** in ``INSTALLED_APPS`` (so their models were never inspected),
          ``data["warning"]`` and ``data["unregistered_apps"]`` are populated —
          register them with ``install_app`` (``create_app`` does this
          automatically) and re-run.
    """
    root = require_project_root(project_root)

    def _run() -> dict:
        from zeeb_orm.migrations.autodetector import detect_changes
        from zeeb_orm.migrations.cli import _register_models
        from zeeb_orm.migrations.executor import list_migration_files
        from zeeb_orm.migrations.writer import write_migration

        migrations_dir = root / "migrations"
        migrations_dir.mkdir(parents=True, exist_ok=True)
        _register_models(root)
        operations = detect_changes(migrations_dir=str(migrations_dir))

        # An unregistered app's models are never inspected regardless of what
        # else changed, so surface it whether or not a migration was written.
        unregistered = _unregistered_apps_with_models(root)
        result: dict = {"created": None, "operations": []}
        if unregistered:
            joined = ", ".join(unregistered)
            result["unregistered_apps"] = unregistered
            result["warning"] = (
                f"App(s) with models are not in INSTALLED_APPS: {joined}. "
                f"Their models were not inspected — run install_app then "
                f"make_migrations again."
            )
        if not operations:
            return result

        existing = list_migration_files(migrations_dir)
        filepath = write_migration(
            migrations_dir,
            operations=operations,
            name=name,
            initial=len(existing) == 0,
        )
        result["created"] = filepath.name
        result["operations"] = [op.describe() for op in operations]
        return result

    result = await asyncio.to_thread(_run)
    if result["created"]:
        message = f"Created migration '{result['created']}'"
    else:
        message = "No changes detected — no migration created"
    if result.get("warning"):
        message = f"{message}. {result['warning']}"
    return AgentResult(success=True, message=message, data=result)


@agent_function
async def get_migration_status(project_root: Path | None = None) -> AgentResult:
    """Return the status of all migrations (applied / pending).

    Equivalent to ``python manage.py showmigrations``.

    Returns data (on success):
        migrations (list[dict]): every migration as
            ``{"name": str, "applied": bool}`` in order.
        applied (list[str]): names of the already-applied migrations.
        pending (list[str]): names of the not-yet-applied migrations.
        pending_count (int): len(pending).
    """
    root = project_root

    def _run() -> list[dict]:
        from zeeb_orm.migrations import executor
        settings = load_project_settings(root)
        db_url = resolve_db_url(settings, root)
        status = executor.showmigrations(database_url=db_url, project_root=root)
        return [{"name": name, "applied": applied} for name, applied in status]

    migrations = await asyncio.to_thread(_run)
    pending = [m["name"] for m in migrations if not m["applied"]]
    applied = [m["name"] for m in migrations if m["applied"]]
    return AgentResult(
        success=True,
        message=f"{len(migrations)} migration(s), {len(pending)} pending",
        data={
            "migrations": migrations,
            "applied": applied,
            "pending": pending,
            "pending_count": len(pending),
        },
    )


@agent_function
async def rollback_migration(
    steps: int = 1,
    project_root: Path | None = None,
) -> AgentResult:
    """Roll back the last *steps* migration(s).

    Equivalent to ``python manage.py migrate --rollback <N>``.

    Args:
        steps: How many applied migrations to roll back, counting from the
            most recent (default 1). When ``steps`` >= the number of applied
            migrations, everything is rolled back (target ``zero``).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        rolled_back (list[str]): names of the migrations that were rolled
            back (empty list when nothing was applied).
    """
    root = project_root

    def _run() -> list[str]:
        from zeeb_orm.migrations import executor
        settings = load_project_settings(root)
        db_url = resolve_db_url(settings, root)
        status = executor.showmigrations(database_url=db_url, project_root=root)
        applied = [name for name, is_applied in status if is_applied]
        if not applied:
            return []
        target = "zero" if steps >= len(applied) else applied[-(steps + 1)]
        return executor.migrate(target=target, database_url=db_url, project_root=root)

    rolled_back = await asyncio.to_thread(_run)
    if not rolled_back:
        return AgentResult(success=True, message="No migrations to roll back", data={"rolled_back": []})
    return AgentResult(
        success=True,
        message=f"Rolled back {len(rolled_back)} migration(s)",
        data={"rolled_back": rolled_back},
    )


def _find_migration(migrations_dir: Path, name: str) -> tuple[list[str], tuple[str, Path] | None]:
    """Resolve ``0003``, ``0003_x`` or ``0003_x.py`` to a migration file."""
    from zeeb_orm.migrations.executor import list_migration_files

    files = list_migration_files(migrations_dir) if migrations_dir.is_dir() else []
    wanted = name[:-3] if name.endswith(".py") else name
    names = [n for n, _ in files]
    exact = next(((n, p) for n, p in files if n == wanted), None)
    if exact:
        return names, exact
    by_number = [(n, p) for n, p in files if n.split("_", 1)[0] == wanted]
    if len(by_number) == 1:
        return names, by_number[0]
    return names, None


@agent_function
async def show_migration(
    name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Show one migration file — its source, operations, dependencies and applied state.

    Read-only. The step before repairing a migration: read what it does and
    whether the database has it, then roll it back with
    ``run_migrations(target=...)``, squash it, or delete its file.

    Args:
        name: The migration name (``"0003_add_status"``), its number
            (``"0003"``), or the file name (``"0003_add_status.py"``).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        name (str): the resolved migration name.
        path (str): project-relative path of the file.
        content (str): the file's source.
        operations (list[str]): human-readable descriptions of its operations.
        dependencies (list[str]): migrations it depends on.
        replaces (list[str]): migrations it squashes (empty unless squashed).
        applied (bool | None): whether the database has it; ``None`` when the
            database could not be reached.

    Notes:
        - Fails with ``file_not_found`` (with close-match ``suggestions``) for
          an unknown migration.
    """
    root = require_project_root(project_root)

    def _run() -> dict:
        from zeeb_orm.migrations import executor
        from zeeb_orm.migrations.executor import load_migration

        migrations_dir = root / "migrations"
        names, found = _find_migration(migrations_dir, name)
        if found is None:
            raise AgentError(
                f"No migration named '{name}'.",
                code="file_not_found",
                suggestions=close_matches(name, names),
            )
        resolved, path = found
        migration = load_migration(path)
        applied: bool | None
        try:
            settings = load_project_settings(root)
            db_url = resolve_db_url(settings, root)
            status = dict(executor.showmigrations(database_url=db_url, project_root=root))
            applied = bool(status.get(resolved))
        except Exception:
            applied = None
        return {
            "name": resolved,
            "path": path.relative_to(root).as_posix(),
            "content": path.read_text(encoding="utf-8"),
            "operations": [op.describe() for op in migration.operations],
            "dependencies": list(migration.dependencies),
            "replaces": list(migration.replaces),
            "applied": applied,
        }

    data = await asyncio.to_thread(_run)
    states = {True: "applied", False: "pending", None: "database unreachable"}
    state = states[data["applied"]]
    return AgentResult(
        success=True,
        message=f"{data['name']}: {len(data['operations'])} operation(s), {state}",
        data=data,
    )


@agent_function
async def squash_migrations(
    start: str,
    end: str,
    name: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Squash a range of migrations into one file.

    Equivalent to ``python manage.py squashmigrations``. The new file records
    the originals in ``replaces``, so the executor treats them as superseded:
    applying is never repeated, and a database that already has the originals
    is marked as having the squashed one. The original files may be deleted
    (``delete_file``) once the squashed migration has been deployed
    everywhere.

    Args:
        start: First migration in the range (inclusive), by name or number.
        end: Last migration in the range (inclusive), by name or number.
        name: Optional human-readable suffix for the squashed file.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        created (str): the squashed migration's name.
        path (str): project-relative path of the new file.
        replaces (list[str]): the migrations it supersedes, in order.
        output (str): what the squash reported (optimizer summary).

    Notes:
        - Fails with ``file_not_found`` (with ``suggestions``) when ``start``
          or ``end`` is unknown, and ``invalid_input`` when ``start`` comes
          after ``end``.
    """
    root = require_project_root(project_root)

    def _run() -> dict:
        import contextlib
        import io

        from zeeb_orm.migrations.cli import squashmigrations
        from zeeb_orm.migrations.executor import load_migration

        migrations_dir = root / "migrations"
        names, first = _find_migration(migrations_dir, start)
        _, last = _find_migration(migrations_dir, end)
        for label, found in (("start", first), ("end", last)):
            if found is None:
                wanted = start if label == "start" else end
                raise AgentError(
                    f"No migration named '{wanted}' ({label}).",
                    code="file_not_found",
                    suggestions=close_matches(wanted, names),
                )
        if names.index(first[0]) > names.index(last[0]):
            raise AgentError(
                f"'{first[0]}' comes after '{last[0]}' — start must precede end.",
                code="invalid_input",
            )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            created = squashmigrations(
                first[0], last[0], squashed_name=name, migrations_dir=str(migrations_dir)
            )
        output = buffer.getvalue().strip()
        if created is None:
            raise AgentError(output or "Squash failed.", code="invalid_input")
        path = migrations_dir / f"{created}.py"
        migration = load_migration(path)
        return {
            "created": created,
            "path": path.relative_to(root).as_posix(),
            "replaces": list(migration.replaces),
            "output": output,
        }

    data = await asyncio.to_thread(_run)
    return AgentResult(
        success=True,
        message=f"Squashed {len(data['replaces'])} migration(s) into {data['created']}",
        data=data,
    )
