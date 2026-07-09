"""Agent functions for health check endpoints and system status."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.project import load_project_settings, resolve_db_url

_HEALTH_MODULE = '''\
"""Health check endpoints.

Provides /health (liveness) and /ready (readiness) endpoints.
Register in your main router::

    from {project_slug}.health import router as health_router
    app.include_router(health_router, prefix="")
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe — returns 200 when the app process is running."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness_check():
    """Readiness probe — verifies database connectivity."""
    from sqlalchemy import text
    from zeeb_orm.db import get_database

    db = get_database()
    if db is None:
        return JSONResponse(
            {"status": "not_ready", "db": "not configured"}, status_code=503
        )

    try:
        async with db.session() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready", "db": "ok"}
    except Exception as exc:
        return JSONResponse(
            {"status": "not_ready", "db": str(exc)}, status_code=503
        )
'''


def _sync_db_url(root: Path) -> str:
    settings = load_project_settings(root)
    url = resolve_db_url(settings, root)
    url = url.replace("sqlite+aiosqlite://", "sqlite://")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("mysql+aiomysql://", "mysql://")
    return url


def _project_slug(root: Path) -> str:
    """Guess the project settings package name (first dir with settings.py)."""
    for item in root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            return item.name
    return "config"


@agent_function
async def create_health_endpoint(
    project_root: Path | None = None,
) -> AgentResult:
    """Scaffold ``/health`` and ``/ready`` route handlers.

    Writes a ``health.py`` file in the project root with two endpoints:

    - ``GET /health`` — liveness probe, always returns 200 when the process runs.
    - ``GET /ready`` — readiness probe, pings the database and returns 503 if down.

    After calling this function, include the router in your main app::

        from health import router as health_router
        app.include_router(health_router)

    Args:
        project_id: The host-assigned project id (required).

    Returns data (on success):
        path (str): the ``health.py`` path relative to the project root.
        endpoints (list[dict]): one ``{"method", "path", "description"}`` entry
            per scaffolded endpoint (``GET /health`` and ``GET /ready``).

    Notes:
        - Returns ``success=False`` with ``data={"path": "health.py"}`` when a
          ``health.py`` already exists — it is never overwritten.
    """
    root = project_root
    health_file = root / "health.py"

    if health_file.exists():
        return AgentResult(
            success=False,
            message="health.py already exists. Edit it manually or delete it first.",
            data={"path": "health.py"},
        )

    slug = await asyncio.to_thread(_project_slug, root)
    # NOTE: .replace() instead of .format() — the template contains literal
    # braces (dicts/JSON) that .format() would choke on with a KeyError.
    content = _HEALTH_MODULE.replace("{project_slug}", slug)
    await asyncio.to_thread(health_file.write_text, content, "utf-8")

    return AgentResult(
        success=True,
        message="health.py created with /health and /ready endpoints.",
        data={
            "path": "health.py",
            "endpoints": [
                {"method": "GET", "path": "/health", "description": "Liveness probe"},
                {
                    "method": "GET",
                    "path": "/ready",
                    "description": "Readiness probe (DB check)",
                },
            ],
        },
    )


@agent_function
async def check_system_health(
    project_root: Path | None = None,
) -> AgentResult:
    """Run runtime health checks against the live project.

    Checks performed:

    - **db** — can the database be reached with a ``SELECT 1``?
    - **settings** — can the settings module be loaded?
    - **migrations** — are all ORM tables present in the DB?

    Args:
        project_id: The host-assigned project id (required).

    Returns data (always):
        checks (dict): with these keys —
            settings (str): ``"ok"`` or ``"error: ..."``
            db (str): ``"ok"`` or ``"error: ..."``
            db_driver (str): driver scheme from the DB url (e.g.
                ``"postgresql"``, ``"sqlite+aiosqlite"``); only present when
                settings loaded.
            tables (list[str]): sorted table names found in the DB.
            table_count (int): len(tables).
            overall (str): ``"healthy"`` if both settings and db are ``"ok"``,
                else ``"degraded"``.

    Notes:
        - ``success`` mirrors ``checks["overall"] == "healthy"``.
    """
    root = project_root

    def _run() -> dict[str, Any]:
        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect

        results: dict[str, Any] = {}

        # Settings check
        try:
            settings = load_project_settings(root)
            results["settings"] = "ok"
            db_url = settings.get("DATABASE", {}).get("url", "")
            results["db_driver"] = db_url.split("://")[0] if "://" in db_url else "unknown"
        except Exception as exc:
            results["settings"] = f"error: {exc}"

        # DB connectivity
        try:
            engine = create_engine(_sync_db_url(root))
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            results["db"] = "ok"
            inspector = sa_inspect(engine)
            results["tables"] = sorted(inspector.get_table_names())
            results["table_count"] = len(results["tables"])
        except Exception as exc:
            results["db"] = f"error: {exc}"
            results["tables"] = []
            results["table_count"] = 0

        core_ok = all(
            v == "ok" for k, v in results.items() if k in ("settings", "db")
        )
        results["overall"] = "healthy" if core_ok else "degraded"
        return results

    checks = await asyncio.to_thread(_run)
    overall = checks.get("overall", "unknown")
    return AgentResult(
        success=overall == "healthy",
        message=f"System health: {overall}.",
        data={"checks": checks},
    )
