"""Agent functions for user management in zeeb_api projects."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError, fail
from zeeb_agents._utils.project import (
    ensure_settings_loaded,
    load_project_settings,
    resolve_db_url,
)
from zeeb_orm.db.urls import sync_database_url


def _sync_db_url(root: Path) -> str:
    """Return a synchronous SQLAlchemy DB URL, its driver named.

    Fails with ``settings_error`` when ``settings.py`` does not load, rather
    than falling back to a default sqlite file nobody configured.
    """
    return sync_database_url(
        resolve_db_url(ensure_settings_loaded(load_project_settings(root)), root)
    )


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


def _is_secret_column(name: str) -> bool:
    """Whether a column holds a credential that must never leave the tool."""
    return "password" in name.lower()


def _row_to_dict(row: Any, cols: list[str] | None = None) -> dict[str, Any]:
    """The row keyed by its own column names, credentials removed.

    Read off the row's mapping — never zipped against a separately obtained
    column list, whose order need not be the ``SELECT *`` order (``update_user``
    zipped against a *set*, so values landed under the wrong keys and the
    password hash could surface under another column's name). *cols* is
    accepted for the old call shape and ignored.
    """
    return {
        str(key): value
        for key, value in row._mapping.items()
        if not _is_secret_column(str(key))
    }


def _lookup_candidates(email_or_id: object) -> tuple[str, list[Any]]:
    """``(column, values to try)`` identifying a user.

    An ``int`` is a primary key. A string containing ``@`` is an email. Any
    other string that parses as a UUID is a primary key — tried as the 32-hex
    form SQLAlchemy's ``Uuid`` stores on SQLite/MySQL (and PostgreSQL accepts),
    then as the canonical dashed form — and an all-digit string is an integer
    key. Anything else is still looked up as an email, as before.
    """
    if isinstance(email_or_id, bool):
        raise AgentError(f"Invalid user reference {email_or_id!r}", code="invalid_input")
    if isinstance(email_or_id, int):
        return "id", [email_or_id]
    if isinstance(email_or_id, uuid.UUID):
        return "id", [email_or_id.hex, str(email_or_id)]
    text = str(email_or_id).strip()
    if "@" in text:
        return "email", [text]
    try:
        parsed = uuid.UUID(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        return "id", list(dict.fromkeys([parsed.hex, str(parsed), text]))
    if text.isdigit():
        return "id", [int(text), text]
    return "email", [text]


def _find_user(conn: Any, table: str, email_or_id: object) -> tuple[Any, str, Any] | None:
    """``(row, column, stored value)`` of the user, or ``None`` when absent.

    The stored value is the one that matched, so a follow-up ``UPDATE`` /
    ``DELETE`` addresses exactly the row that was found.
    """
    from sqlalchemy import text

    column, candidates = _lookup_candidates(email_or_id)
    for value in candidates:
        try:
            row = conn.execute(
                text(f"SELECT * FROM {table} WHERE {column} = :v"),  # noqa: S608
                {"v": value},
            ).fetchone()
        except Exception:  # noqa: BLE001 — e.g. a text value against an integer key
            continue
        if row is not None:
            return row, column, value
    return None


def _user_not_found(email_or_id: object) -> AgentError:
    return AgentError(f"User '{email_or_id}' not found.", code="user_not_found")


@agent_function
async def create_user(
    email: str,
    password: str,
    is_staff: bool = False,
    is_superuser: bool = False,
    project_root: Path | None = None,
) -> AgentResult:
    """Create a new user in the project's user table.

    Passwords are hashed with zeeb_api's bcrypt hasher before storage.

    Args:
        email: User email address (must be unique).
        password: Plain-text password.
        is_staff: Grant staff privileges.
        is_superuser: Grant superuser privileges.
        project_id: The host-assigned project id (required).

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
            row = conn.execute(
                text(f"SELECT * FROM {table} WHERE email = :email"), {"email": email}
            ).fetchone()
            result = _row_to_dict(row)
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
        project_id: The host-assigned project id (required).

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
            rows = conn.execute(
                text(f"SELECT * FROM {table} LIMIT :limit OFFSET :offset"),
                {"limit": limit, "offset": offset},
            ).fetchall()
            total = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            users = [_row_to_dict(row) for row in rows]
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
        email_or_id: Email address, or primary key — an ``int``, or a string
            holding a UUID (either spelling) or digits. A string with ``@`` is
            always an email.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        <columns> (Any): the matched row keyed by column name, every
            ``password`` column removed

    Notes:
        - A missing user table fails with ``error_code="no_user_table"``;
          a not-found user with ``error_code="user_not_found"``.
    """
    root = project_root

    def _run() -> dict[str, Any]:
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.connect() as conn:
            inspector = sa_inspect(engine)
            table = _require_user_table(inspector)
            found = _find_user(conn, table, email_or_id)
            if found is None:
                raise _user_not_found(email_or_id)
            return _row_to_dict(found[0])

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
        email_or_id: Email address, or primary key — an ``int``, or a string
            holding a UUID (either spelling) or digits. A string with ``@`` is
            always an email.
        changes: Dict of column → new value.  ``password`` is silently removed.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        <columns> (Any): the updated row keyed by column name, every
            ``password`` column removed

    Notes:
        - If *changes* contains only ``password`` (or is empty), fails with
          ``error_code="invalid_input"`` and nothing is updated.
        - A user that does not exist fails with ``error_code="user_not_found"``
          (it used to crash with a ``TypeError``).
        - A missing user table fails with ``error_code="no_user_table"``;
          *changes* with no columns that exist on the table fails with
          ``error_code="invalid_input"`` and the available ``columns`` in
          ``data``.
        - The row is re-read by the key it was found under (its primary key
          when it has one), so changing ``email`` returns the updated row.
    """
    root = project_root
    if not isinstance(changes, dict):
        return fail("changes must be a dict of column -> value", code="invalid_input")
    safe_changes = {k: v for k, v in changes.items() if not _is_secret_column(str(k))}
    if not safe_changes:
        return fail(
            "No valid fields to update (password must use set_user_password).",
            code="invalid_input",
        )

    def _run() -> dict[str, Any]:
        from sqlalchemy import create_engine, text
        from sqlalchemy import inspect as sa_inspect
        engine = create_engine(_sync_db_url(root))
        with engine.begin() as conn:
            inspector = sa_inspect(engine)
            table = _require_user_table(inspector)
            all_cols = {c["name"] for c in inspector.get_columns(table)}
            # Only real column names reach the SQL text; values stay bound.
            update_data = {k: v for k, v in safe_changes.items() if k in all_cols}
            if not update_data:
                raise AgentError(
                    f"None of the provided columns exist in {table}. "
                    f"Available columns: {', '.join(sorted(all_cols))}",
                    code="invalid_input",
                    columns=sorted(all_cols),
                )
            found = _find_user(conn, table, email_or_id)
            if found is None:
                raise _user_not_found(email_or_id)
            row, column, value = found
            current = row._mapping
            if "id" in current:
                column, value = "id", current["id"]
            set_clause = ", ".join(f"{k} = :{k}" for k in update_data)
            conn.execute(
                text(f"UPDATE {table} SET {set_clause} WHERE {column} = :_where_val"),
                {**update_data, "_where_val": value},
            )
            if column == "email" and "email" in update_data:
                value = update_data["email"]
            updated = conn.execute(
                text(f"SELECT * FROM {table} WHERE {column} = :v"), {"v": value}
            ).fetchone()
            if updated is None:
                raise _user_not_found(email_or_id)
            return _row_to_dict(updated)

    user = await asyncio.to_thread(_run)
    return AgentResult(success=True, message="User updated.", data=user)


@agent_function
async def delete_user(
    email_or_id: str | int,
    project_root: Path | None = None,
) -> AgentResult:
    """Delete a user by email or primary-key ID.

    Args:
        email_or_id: Email address, or primary key — an ``int``, or a string
            holding a UUID (either spelling) or digits.
        project_id: The host-assigned project id (required).

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
            found = _find_user(conn, table, email_or_id)
            if found is None:
                return 0
            _row, column, value = found
            result = conn.execute(text(f"DELETE FROM {table} WHERE {column} = :v"), {"v": value})
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
        email_or_id: Email address, or primary key — an ``int``, or a string
            holding a UUID (either spelling) or digits.
        new_password: New plain-text password.
        project_id: The host-assigned project id (required).

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
            found = _find_user(conn, table, email_or_id)
            if found is None:
                return 0
            _row, column, value = found
            result = conn.execute(
                text(f"UPDATE {table} SET password = :pw WHERE {column} = :v"),
                {"pw": hashed_pw, "v": value},
            )
            return result.rowcount

    rowcount = await asyncio.to_thread(_run)
    if rowcount == 0:
        return fail(f"User '{email_or_id}' not found.", code="user_not_found")
    return AgentResult(
        success=True,
        message=f"Password updated for '{email_or_id}'.",
    )
