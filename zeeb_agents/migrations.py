"""Agent functions for migration management."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.project import load_project_settings, resolve_db_url


@agent_function
async def run_migrations(project_root: Path | None = None) -> AgentResult:
    """Apply all pending migrations.

    Equivalent to ``python manage.py migrate``.

    Returns data (on success):
        applied (list[str]): names of the migrations that were applied
            (empty list when there was nothing pending).

    Notes:
        - "Nothing to apply" is reported as ``success=True`` with
          ``applied=[]``, not as a failure.
    """
    root = project_root

    def _run() -> list[str]:
        from zeeb_orm.migrations import executor
        settings = load_project_settings(root)
        db_url = resolve_db_url(settings, root)
        return executor.migrate(database_url=db_url, project_root=root)

    applied = await asyncio.to_thread(_run)
    if applied:
        return AgentResult(
            success=True,
            message=f"Applied {len(applied)} migration(s)",
            data={"applied": applied},
        )
    return AgentResult(
        success=True,
        message="No pending migrations",
        data={"applied": []},
    )


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
    """
    root = project_root

    def _run() -> dict:
        from zeeb_orm.migrations.autodetector import detect_changes
        from zeeb_orm.migrations.cli import _register_models
        from zeeb_orm.migrations.executor import list_migration_files
        from zeeb_orm.migrations.writer import write_migration

        migrations_dir = root / "migrations"
        migrations_dir.mkdir(parents=True, exist_ok=True)
        _register_models(root)
        operations = detect_changes(migrations_dir=str(migrations_dir))
        if not operations:
            return {"created": None, "operations": []}

        existing = list_migration_files(migrations_dir)
        filepath = write_migration(
            migrations_dir,
            operations=operations,
            name=name,
            initial=len(existing) == 0,
        )
        return {
            "created": filepath.name,
            "operations": [op.describe() for op in operations],
        }

    result = await asyncio.to_thread(_run)
    if result["created"]:
        return AgentResult(
            success=True,
            message=f"Created migration '{result['created']}'",
            data=result,
        )
    return AgentResult(
        success=True,
        message="No changes detected — no migration created",
        data=result,
    )


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
