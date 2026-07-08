"""Agent functions for user management in zeeb_api projects."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError, fail
from zeeb_agents._utils.project import load_project_settings, resolve_db_url


def _sync_db_url(root: Path) -> str:
    """Return a synchronous SQLAlchemy DB URL."""
    settings = load_project_settings(root)
    url = resolve_db_url(settings, root)
    url = url.replace("sqlite+aiosqlite://", "sqlite://")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("mysql+aiomysql://", "mysql://")
    return url


def _find_user_table(inspector: Any) -> str | None:
    """Detect the user table by looking for email + password columns."""
    for name in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns(name)}
        if {"email", "password"} <= cols:
            return name
    return None


def _require_user_table(inspector: Any) -> str:
    """Return the user table name; fail with a migration hint if absent."""
    table = _find_user_table(inspector)
    if not table:
        raise AgentError(
            "Could not locate a user table (needs email + password columns). "
            "Run make_migrations() and run_migrations() first if the project "
            "has a user model.",
            code="no_user_table",
        )
    return table


def _hash_password(raw_password: str) -> str:
    """Hash a plain-text password using zeeb_api's hasher."""
    from zeeb_api.auth.hashers import make_password
    return make_password(raw_password)


def _row_to_dict(row: Any, cols: list[str]) -> dict[str, Any]:
    data = dict(zip(cols, row))
    data.pop("password", None)
    return data


@agent_function
async def create_user(
    email: str,
    password: str,
    is_staff: bool = False,
    is_superuser: bool = False,
    project_root: Path | None = None,
) -> AgentResult:
    """Create a new user in the project's user table.

    Passwords are hashed with zeeb_api's PBKDF2 hasher before storage.

    Args:
        email: User email address (must be unique).
        password: Plain-text password.
        is_staff: Grant staff privileges.
        is_superuser: Grant superuser privileges.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        <columns> (Any): every column of the created row, keyed by name, with
            ``password`` removed
        table (str): the user table the row was inserted into

    Notes:
        - The user table is detected by scanning for one with both ``email`` and
          ``password`` columns; if none is found the call fails with
          ``error_code="no_user_table"`` and a hint to run migrations.
        - A duplicate email fails with ``error_code="already_exists"``.
        - Only the ``email``/``password``/``is_active``/``is_staff``/
          ``is_superuser`` columns that actually exist on the table are inserted.
    """
    root = project_root
    hashed_pw = await asyncio.to_thread(_hash_password, password)

    def _run() -> dict[str, Any]:
        from uuid import uuid4

        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.begin() as conn:
            inspector = sa_inspect(engine)
            table = _require_user_table(inspector)
            # Explicit duplicate check — the unique constraint on email may
            # be missing from the DDL (unnamed constraints are not
            # auto-migrated), so don't rely on IntegrityError alone.
            existing = conn.execute(
                text(f"SELECT 1 FROM {table} WHERE email = :email"),  # noqa: S608
                {"email": email},
            ).fetchone()
            if existing is not None:
                raise AgentError(
                    f"User '{email}' already exists.",
                    code="already_exists",
                    email=email,
                )
            columns = inspector.get_columns(table)
            cols = {c["name"] for c in columns}
            data: dict[str, Any] = {
                "email": email,
                "password": hashed_pw,
                "is_active": True,
                "is_staff": is_staff,
                "is_superuser": is_superuser,
            }
            # zeeb models default to UUID PKs whose value is generated
            # client-side (no DB server default) — supply one for raw SQL.
            id_col = next((c for c in columns if c["name"] == "id"), None)
            if (
                id_col is not None
                and "INT" not in str(id_col["type"]).upper()
                and id_col.get("default") is None
            ):
                data["id"] = uuid4().hex
            if "date_joined" in cols:
                from datetime import datetime, timezone

                data["date_joined"] = datetime.now(timezone.utc).isoformat()
            # Custom user models may declare extra NOT NULL columns without a
            # DB-level default (zeeb field defaults are client-side only and
            # this INSERT bypasses the ORM). Fill common scalar types with a
            # neutral value so user creation cannot fail on them.
            for col in columns:
                cname = col["name"]
                if cname in data or cname == "id":
                    continue
                if col.get("nullable", True) or col.get("default") is not None:
                    continue
                ctype = str(col["type"]).upper()
                if "CHAR" in ctype or "TEXT" in ctype:
                    data[cname] = ""
                elif "BOOL" in ctype:
                    data[cname] = False
                elif any(t in ctype for t in ("INT", "NUMERIC", "DECIMAL", "FLOAT", "DOUBLE")):
                    data[cname] = 0
                # Other types (dates, json, uuid, …) stay unset — failing
                # loudly beats inserting a made-up value there.
            # Only include columns that exist in this table
            insert_data = {k: v for k, v in data.items() if k in cols}
            placeholders = ", ".join(f":{k}" for k in insert_data)
            col_list = ", ".join(insert_data.keys())
            from sqlalchemy.exc import IntegrityError
            try:
                conn.execute(
                    text(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"),
                    insert_data,
                )
            except IntegrityError as exc:
                if "unique" in str(exc).lower():
                    raise AgentError(
                        f"User '{email}' already exists.",
                        code="already_exists",
                        email=email,
                    ) from exc
                raise
            row = conn.execute(text(f"SELECT * FROM {table} WHERE email = :email"), {"email": email}).fetchone()
            all_cols = [c["name"] for c in inspector.get_columns(table)]
            result = _row_to_dict(row, all_cols)
            result["table"] = table
            return result

    user = await asyncio.to_thread(_run)
    return AgentResult(
        success=True,
        message=f"User '{email}' created successfully.",
        data=user,
    )


@agent_function
async def list_users(
    limit: int = 50,
    offset: int = 0,
    project_root: Path | None = None,
) -> AgentResult:
    """Return a list of users from the project's user table.

    Passwords are omitted from the result.

    Args:
        limit: Maximum number of users to return.
        offset: Number of users to skip.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        users (list[dict]): each row keyed by column name, ``password`` removed
        total (int): total user count in the table (ignores limit/offset)
        limit (int): echoes *limit*
        offset (int): echoes *offset*

    Notes:
        - If no user table is found the call fails with
          ``error_code="no_user_table"``.
    """
    root = project_root

    def _run() -> dict[str, Any]:
        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.connect() as conn:
            inspector = sa_inspect(engine)
            table = _require_user_table(inspector)
            all_cols = [c["name"] for c in inspector.get_columns(table)]
            rows = conn.execute(text(f"SELECT * FROM {table} LIMIT :limit OFFSET :offset"), {"limit": limit, "offset": offset}).fetchall()
            total = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            users = [_row_to_dict(row, all_cols) for row in rows]
            return {"users": users, "total": total, "limit": limit, "offset": offset}

    data = await asyncio.to_thread(_run)
    return AgentResult(
        success=True,
        message=f"Found {data['total']} user(s).",
        data=data,
    )


@agent_function
async def get_user(
    email_or_id: str | int,
    project_root: Path | None = None,
) -> AgentResult:
    """Fetch a single user by email or primary-key ID.

    Args:
        email_or_id: Email address (``str``) or integer primary key.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        <columns> (Any): the matched row keyed by column name, ``password``
            removed

    Notes:
        - A missing user table fails with ``error_code="no_user_table"``;
          a not-found user with ``error_code="user_not_found"``.
    """
    root = project_root

    def _run() -> dict[str, Any]:
        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.connect() as conn:
            inspector = sa_inspect(engine)
            table = _require_user_table(inspector)
            all_cols = [c["name"] for c in inspector.get_columns(table)]
            if isinstance(email_or_id, str):
                row = conn.execute(text(f"SELECT * FROM {table} WHERE email = :v"), {"v": email_or_id}).fetchone()
            else:
                row = conn.execute(text(f"SELECT * FROM {table} WHERE id = :v"), {"v": email_or_id}).fetchone()
            if row is None:
                raise AgentError(
                    f"User '{email_or_id}' not found.",
                    code="user_not_found",
                )
            return _row_to_dict(row, all_cols)

    user = await asyncio.to_thread(_run)
    return AgentResult(success=True, message="User found.", data=user)


@agent_function
async def update_user(
    email_or_id: str | int,
    changes: dict[str, Any],
    project_root: Path | None = None,
) -> AgentResult:
    """Update fields on an existing user.

    ``password`` in *changes* is **ignored** — use :func:`set_user_password` instead.

    Args:
        email_or_id: Email address (``str``) or integer primary key.
        changes: Dict of column → new value.  ``password`` is silently removed.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        <columns> (Any): the updated row keyed by column name, ``password``
            removed

    Notes:
        - If *changes* contains only ``password`` (or is empty), returns
          ``success=False`` with ``data=None`` and nothing is updated.
        - A missing user table fails with ``error_code="no_user_table"``;
          *changes* with no columns that exist on the table fails with
          ``error_code="invalid_input"`` and the available ``columns`` in
          ``data``.
    """
    root = project_root
    safe_changes = {k: v for k, v in changes.items() if k != "password"}
    if not safe_changes:
        return AgentResult(success=False, message="No valid fields to update (password must use set_user_password).")

    def _run() -> dict[str, Any]:
        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.begin() as conn:
            inspector = sa_inspect(engine)
            table = _require_user_table(inspector)
            all_cols = {c["name"] for c in inspector.get_columns(table)}
            update_data = {k: v for k, v in safe_changes.items() if k in all_cols}
            if not update_data:
                raise AgentError(
                    f"None of the provided columns exist in {table}. "
                    f"Available columns: {', '.join(sorted(all_cols))}",
                    code="invalid_input",
                    columns=sorted(all_cols),
                )
            set_clause = ", ".join(f"{k} = :{k}" for k in update_data)
            if isinstance(email_or_id, str):
                where = "email = :_where_val"
            else:
                where = "id = :_where_val"
            update_data["_where_val"] = email_or_id
            conn.execute(text(f"UPDATE {table} SET {set_clause} WHERE {where}"), update_data)
            col_list = list(all_cols)
            row = conn.execute(text(f"SELECT * FROM {table} WHERE {'email' if isinstance(email_or_id, str) else 'id'} = :v"), {"v": email_or_id}).fetchone()
            return _row_to_dict(row, col_list)

    user = await asyncio.to_thread(_run)
    return AgentResult(success=True, message="User updated.", data=user)


@agent_function
async def delete_user(
    email_or_id: str | int,
    project_root: Path | None = None,
) -> AgentResult:
    """Delete a user by email or primary-key ID.

    Args:
        email_or_id: Email address (``str``) or integer primary key.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        deleted (int): number of rows deleted (always ``>= 1`` on success)

    Notes:
        - If no row matched (rowcount 0), fails with
          ``error_code="user_not_found"``.
        - A missing user table fails with ``error_code="no_user_table"``.
    """
    root = project_root

    def _run() -> int:
        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.begin() as conn:
            inspector = sa_inspect(engine)
            table = _require_user_table(inspector)
            if isinstance(email_or_id, str):
                result = conn.execute(text(f"DELETE FROM {table} WHERE email = :v"), {"v": email_or_id})
            else:
                result = conn.execute(text(f"DELETE FROM {table} WHERE id = :v"), {"v": email_or_id})
            return result.rowcount

    rowcount = await asyncio.to_thread(_run)
    if rowcount == 0:
        return fail(f"User '{email_or_id}' not found.", code="user_not_found")
    return AgentResult(
        success=True,
        message=f"User '{email_or_id}' deleted.",
        data={"deleted": rowcount},
    )


@agent_function
async def set_user_password(
    email_or_id: str | int,
    new_password: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Set a new password for an existing user (hashes it automatically).

    Args:
        email_or_id: Email address (``str``) or integer primary key.
        new_password: New plain-text password.
        project_root: Auto-detected if ``None``.

    Returns data (always):
        None — this function carries its result only in ``success``/``message``.

    Notes:
        - If no row matched, fails with ``error_code="user_not_found"``.
        - A missing user table fails with ``error_code="no_user_table"``.
    """
    root = project_root
    hashed_pw = await asyncio.to_thread(_hash_password, new_password)

    def _run() -> int:
        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.begin() as conn:
            inspector = sa_inspect(engine)
            table = _require_user_table(inspector)
            if isinstance(email_or_id, str):
                result = conn.execute(
                    text(f"UPDATE {table} SET password = :pw WHERE email = :v"),
                    {"pw": hashed_pw, "v": email_or_id},
                )
            else:
                result = conn.execute(
                    text(f"UPDATE {table} SET password = :pw WHERE id = :v"),
                    {"pw": hashed_pw, "v": email_or_id},
                )
            return result.rowcount

    rowcount = await asyncio.to_thread(_run)
    if rowcount == 0:
        return fail(f"User '{email_or_id}' not found.", code="user_not_found")
    return AgentResult(
        success=True,
        message=f"Password updated for '{email_or_id}'.",
    )
