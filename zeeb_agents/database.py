"""Agent functions for live database introspection and querying."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.project import load_project_settings

_SELECT_KEYWORDS = frozenset({"select", "with", "explain"})

# Statements / keywords that mutate state.  Checked with word boundaries so
# column names such as ``created_at`` / ``updated_at`` never false-positive.
_FORBIDDEN_KEYWORD_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|replace|grant"
    r"|attach|pragma|vacuum)\b",
    re.IGNORECASE,
)

_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` block comments."""
    sql = _BLOCK_COMMENT_RE.sub(" ", sql)
    sql = _LINE_COMMENT_RE.sub(" ", sql)
    return sql.strip()


def _validate_read_only_sql(sql: str) -> str | None:
    """Return an error message if *sql* is not a safe read-only query, else None.

    Rules:

    - comments are stripped before any check (no comment-prefixed bypasses);
    - exactly one statement (a single trailing ``;`` is tolerated);
    - the first keyword must be ``SELECT`` / ``WITH`` / ``EXPLAIN``;
    - no mutating keyword (INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/
      REPLACE/GRANT/ATTACH/PRAGMA/VACUUM) may appear anywhere in the
      statement, so ``WITH ... DELETE`` / ``EXPLAIN ANALYZE DELETE`` style
      bypasses are rejected too.
    """
    stripped = _strip_sql_comments(sql)
    if not stripped:
        return "Empty SQL statement"
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        return "Multiple SQL statements are not allowed"
    first_word = stripped.split()[0].lower()
    if first_word not in _SELECT_KEYWORDS:
        return "Only SELECT / WITH / EXPLAIN queries are allowed for safety"
    forbidden = _FORBIDDEN_KEYWORD_RE.search(stripped)
    if forbidden:
        return (
            f"Disallowed SQL keyword '{forbidden.group(1).upper()}' — "
            "only read-only SELECT / WITH / EXPLAIN queries are allowed for safety"
        )
    return None


def _sync_db_url(root: Path) -> str:
    """Return a *synchronous* SQLAlchemy DB URL (strips async drivers)."""
    settings = load_project_settings(root)
    url: str = settings.get("DATABASE", {}).get("url", "sqlite:///db.sqlite3")
    # Convert async drivers to sync equivalents for inspection
    url = url.replace("sqlite+aiosqlite://", "sqlite://")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("mysql+aiomysql://", "mysql://")
    return url


@agent_function
async def list_tables(project_root: Path | None = None) -> AgentResult:
    """Return the names of all tables in the project database.

    Args:
        project_root: Auto-detected if ``None``.
    """
    root = project_root

    def _run() -> list[str]:
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.connect():
            return sa_inspect(engine).get_table_names()

    tables = await asyncio.to_thread(_run)
    return AgentResult(
        success=True,
        message=f"Found {len(tables)} table(s)",
        data={"tables": sorted(tables)},
    )


@agent_function
async def describe_table(
    table_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Return column names and types for a database table.

    Args:
        table_name: Exact table name as stored in the database.
        project_root: Auto-detected if ``None``.
    """
    root = project_root

    def _run() -> list[dict]:
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect
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


@agent_function
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
    error = _validate_read_only_sql(sql)
    if error:
        return AgentResult(success=False, message=error)
    root = project_root

    def _run() -> list[dict]:
        from sqlalchemy import create_engine, text
        engine = create_engine(_sync_db_url(root))
        with engine.connect() as conn:
            # Defense in depth: run inside an explicit transaction that is
            # ALWAYS rolled back, so nothing that slips past the keyword
            # gate can ever be committed.
            trans = conn.begin()
            try:
                result = conn.execute(text(sql))
                keys = list(result.keys())
                rows = [dict(zip(keys, row)) for row in result.fetchall()]
            finally:
                trans.rollback()
        return rows

    rows = await asyncio.to_thread(_run)
    return AgentResult(
        success=True,
        message=f"Query returned {len(rows)} row(s)",
        data={"rows": rows, "count": len(rows)},
    )
