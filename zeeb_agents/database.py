"""Agent functions for live database introspection and querying."""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.project import load_project_settings, require_project_root

_SELECT_KEYWORDS = frozenset({"select", "with", "explain"})


def _is_select(sql: str) -> bool:
    """Return True if *sql* is a read-only query."""
    first_word = sql.strip().split()[0].lower() if sql.strip() else ""
    return first_word in _SELECT_KEYWORDS


def _sync_db_url(root: Path) -> str:
    """Return a *synchronous* SQLAlchemy DB URL (strips async drivers)."""
    settings = load_project_settings(root)
    url: str = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")
    # Convert async drivers to sync equivalents for inspection
    url = url.replace("sqlite+aiosqlite://", "sqlite://")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("mysql+aiomysql://", "mysql://")
    return url


async def list_tables(project_root: Path | None = None) -> AgentResult:
    """Return the names of all tables in the project database.

    Args:
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        def _run() -> list[str]:
            from sqlalchemy import create_engine, inspect as sa_inspect
            engine = create_engine(_sync_db_url(root))
            with engine.connect():
                return sa_inspect(engine).get_table_names()

        tables = await asyncio.to_thread(_run)
        return AgentResult(
            success=True,
            message=f"Found {len(tables)} table(s)",
            data={"tables": sorted(tables)},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def describe_table(
    table_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Return column names and types for a database table.

    Args:
        table_name: Exact table name as stored in the database.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)

        def _run() -> list[dict]:
            from sqlalchemy import create_engine, inspect as sa_inspect
            engine = create_engine(_sync_db_url(root))
            inspector = sa_inspect(engine)
            cols = inspector.get_columns(table_name)
            return [
                {
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": c.get("nullable", True),
                    "default": str(c.get("default")) if c.get("default") is not None else None,
                }
                for c in cols
            ]

        columns = await asyncio.to_thread(_run)
        return AgentResult(
            success=True,
            message=f"Table '{table_name}' has {len(columns)} column(s)",
            data={"table": table_name, "columns": columns},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def run_query(
    sql: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Execute a read-only SQL query against the project database.

    Only ``SELECT``, ``WITH``, and ``EXPLAIN`` queries are allowed.

    Args:
        sql: A SQL query string.
        project_root: Auto-detected if ``None``.
    """
    if not _is_select(sql):
        return AgentResult(
            success=False,
            message="Only SELECT / WITH / EXPLAIN queries are allowed for safety",
        )
    try:
        root = require_project_root(project_root)

        def _run() -> list[dict]:
            from sqlalchemy import create_engine, text
            engine = create_engine(_sync_db_url(root))
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                keys = list(result.keys())
                rows = [dict(zip(keys, row)) for row in result.fetchall()]
            return rows

        rows = await asyncio.to_thread(_run)
        return AgentResult(
            success=True,
            message=f"Query returned {len(rows)} row(s)",
            data={"rows": rows, "count": len(rows)},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
