"""Agent functions for live database introspection and querying."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError, close_matches, fail
from zeeb_agents._utils.process import validate_timeout
from zeeb_agents._utils.project import (
    ensure_settings_loaded,
    load_project_settings,
    resolve_db_url,
)
from zeeb_orm.db.urls import sync_database_url

_SELECT_KEYWORDS = frozenset({"select", "with", "explain"})

# Keywords that mutate state or move data out, anywhere in the statement.
# Checked with word boundaries on the statement's *code* (string literals
# blanked) so column names such as ``created_at`` / ``updated_at`` never
# false-positive. ``into`` covers ``SELECT … INTO OUTFILE/DUMPFILE`` (MySQL)
# and ``SELECT … INTO new_table`` (PostgreSQL), which write despite starting
# with SELECT; ``merge`` a data-modifying CTE. Statement-only commands
# (``SET``, ``COMMIT``, …) need no entry: the single-statement and first-keyword
# rules already exclude them.
_FORBIDDEN_KEYWORD_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|replace|grant|revoke"
    r"|attach|detach|pragma|vacuum|into|copy|merge|upsert)\b",
    re.IGNORECASE,
)

# Functions with side effects outside the (always rolled back) transaction —
# files, large objects, other connections, sleeping, sequences, server
# control — or that execute a query string of their own. Matched as calls.
_FORBIDDEN_FUNCTION_RE = re.compile(
    r"\b(pg_sleep\w*|pg_read_\w+|pg_ls_\w+|pg_stat_file|pg_file_\w+|lo_\w+|loread|lowrite"
    r"|dblink\w*|set_config|pg_terminate_backend|pg_cancel_backend|pg_reload_conf"
    r"|pg_rotate_logfile|pg_switch_wal|pg_create_restore_point|pg_promote"
    r"|pg_\w*advisory\w*|pg_logical_emit_message|pg_notify|nextval|setval"
    r"|query_to_xml\w*|cursor_to_xml\w*|xmlparse_query|sleep|benchmark|load_file"
    r"|get_lock|release_lock|release_all_locks|sys_exec|sys_eval|load_extension"
    r"|readfile|writefile|edit|fts3_tokenizer)\s*\(",
    re.IGNORECASE,
)

_DOLLAR_TAG_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")

# Every lexing a supported database might apply: backslash escapes inside
# quotes (MySQL, PostgreSQL E'' strings), ``#`` line comments (MySQL) and
# dollar quoting (PostgreSQL). A statement has to pass the gate under all of
# them — a string that ends earlier under one dialect than under another is
# exactly how code hides inside what looks like a literal.
_LEXING_MODES = tuple(
    (backslash, hash_comments, dollar)
    for backslash in (False, True)
    for hash_comments in (False, True)
    for dollar in (False, True)
)


class _UnlexableSQLError(ValueError):
    """The statement cannot be split into code and literals unambiguously."""


def _code_only(sql: str, *, backslash: bool, hash_comments: bool, dollar: bool) -> str:
    """*sql* with comments removed and quoted literals blanked, under one lexing.

    Single-quoted and dollar-quoted strings become ``''`` (their contents are
    data); double-quoted and backtick-quoted names keep their text, because a
    quoted identifier can still name a function (``"pg_sleep"(10)``).
    Raises :class:`_UnlexableSQLError` for an unterminated literal/comment or a MySQL
    ``/*! … */`` comment, whose contents MySQL *executes*.
    """
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i) or (hash_comments and ch == "#"):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            out.append(" ")
            continue
        if sql.startswith("/*", i):
            if sql.startswith("/*!", i):
                raise _UnlexableSQLError("MySQL executable comments (/*! … */) are not allowed")
            end = sql.find("*/", i + 2)
            if end < 0:
                raise _UnlexableSQLError("Unterminated /* comment")
            out.append(" ")
            i = end + 2
            continue
        if ch in "'\"`":
            j = i + 1
            while True:
                if j >= n:
                    raise _UnlexableSQLError("Unterminated quoted literal")
                if backslash and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            out.append("''" if ch == "'" else sql[i + 1 : j])
            i = j + 1
            continue
        if dollar and ch == "$":
            tag = _DOLLAR_TAG_RE.match(sql, i)
            if tag:
                end = sql.find(tag.group(0), tag.end())
                if end < 0:
                    raise _UnlexableSQLError("Unterminated dollar-quoted literal")
                out.append("''")
                i = end + len(tag.group(0))
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` block comments (standard lexing)."""
    try:
        return _code_only(sql, backslash=False, hash_comments=False, dollar=False).strip()
    except _UnlexableSQLError:
        return sql.strip()


def _check_code(code: str) -> str | None:
    """The gate for one lexing of the statement; an error message or ``None``."""
    stripped = code.strip()
    if not stripped:
        return "Empty SQL statement"
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        return "Multiple SQL statements are not allowed"
    first_word = stripped.split()[0].lower().lstrip("(")
    if first_word not in _SELECT_KEYWORDS:
        return "Only SELECT / WITH / EXPLAIN queries are allowed for safety"
    forbidden = _FORBIDDEN_KEYWORD_RE.search(stripped)
    if forbidden:
        return (
            f"Disallowed SQL keyword '{forbidden.group(1).upper()}' — "
            "only read-only SELECT / WITH / EXPLAIN queries are allowed for safety"
        )
    function = _FORBIDDEN_FUNCTION_RE.search(stripped)
    if function:
        return (
            f"Disallowed SQL function '{function.group(1).lower()}()' — it has side "
            "effects outside the query (files, other connections, sleeping, "
            "sequences, server control) or runs a query of its own"
        )
    return None


def _validate_read_only_sql(sql: str) -> str | None:
    """Return an error message if *sql* is not a safe read-only query, else None.

    Rules, applied to the statement's code under every lexing in
    :data:`_LEXING_MODES` (comments removed, string literals blanked):

    - no unterminated literal or comment, no MySQL ``/*! … */``;
    - exactly one statement (a single trailing ``;`` is tolerated);
    - the first keyword must be ``SELECT`` / ``WITH`` / ``EXPLAIN``;
    - no mutating / data-moving / transaction-control keyword anywhere
      (:data:`_FORBIDDEN_KEYWORD_RE`, incl. ``INTO``), so ``WITH … DELETE``,
      ``EXPLAIN ANALYZE DELETE`` and ``SELECT … INTO OUTFILE`` are rejected;
    - no call to a side-effecting function (:data:`_FORBIDDEN_FUNCTION_RE`).
    """
    if not isinstance(sql, str):
        return "sql must be a string"
    for backslash, hash_comments, dollar in _LEXING_MODES:
        try:
            code = _code_only(
                sql, backslash=backslash, hash_comments=hash_comments, dollar=dollar
            )
        except _UnlexableSQLError as exc:
            return f"{exc} — the statement cannot be checked unambiguously"
        error = _check_code(code)
        if error:
            return error
    return None


def _sync_db_url(root: Path) -> str:
    """Return a *synchronous* SQLAlchemy DB URL, its driver named.

    Fails with ``settings_error`` when ``settings.py`` does not load, rather
    than falling back to a default sqlite file nobody configured.
    """
    return sync_database_url(
        resolve_db_url(ensure_settings_loaded(load_project_settings(root)), root)
    )


@agent_function
async def list_tables(project_root: Path | None = None) -> AgentResult:
    """Return the names of all tables in the project database.

    Args:
        project_id: The host-assigned project id (required).

    Returns data (on success):
        tables (list[str]): sorted table names.
        count (int): len(tables).
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
        data={"tables": sorted(tables), "count": len(tables)},
    )


@agent_function
async def describe_table(
    table_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Return column names and types for a database table.

    Args:
        table_name: Exact table name as stored in the database.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        table (str): the table name that was inspected.
        columns (list[dict]): each ``{"name": str, "type": str,
            "nullable": bool, "default": str | None}``.

    Notes:
        - A missing table fails with ``error_code="table_not_found"``,
          close-match ``suggestions``, and the existing ``tables`` in ``data``.
    """
    root = project_root

    def _run() -> list[dict]:
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy.exc import NoSuchTableError

        engine = create_engine(_sync_db_url(root))
        inspector = sa_inspect(engine)
        try:
            cols = inspector.get_columns(table_name)
        except NoSuchTableError:
            cols = []
        if not cols:
            tables = inspector.get_table_names()
            suggestions = close_matches(table_name, tables)
            hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
            raise AgentError(
                f"Table '{table_name}' not found.{hint} "
                f"Existing tables: {', '.join(sorted(tables)) or '(none)'}",
                code="table_not_found",
                suggestions=suggestions,
                tables=sorted(tables),
            )
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


#: Default ``run_query`` row cap: enough to inspect data, small enough that a
#: ``SELECT *`` over a large table cannot push the result envelope out of any
#: MCP message budget.
DEFAULT_MAX_ROWS = 1000

#: Default ``run_query`` statement timeout, in seconds.
DEFAULT_QUERY_TIMEOUT = 30.0


def _apply_statement_timeout(conn, dialect: str, seconds: float):  # noqa: ANN001
    """Bound the statement's run time where the database can; return a cleanup.

    PostgreSQL: ``SET LOCAL statement_timeout`` (scoped to the transaction,
    which is always rolled back). MySQL: ``max_execution_time`` (MariaDB:
    ``max_statement_time``) for the session. SQLite: a progress handler that
    interrupts the statement once the deadline passes.
    """
    import time

    millis = max(1, int(seconds * 1000))
    if dialect == "postgresql":
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {millis}")
        return lambda: None
    if dialect in ("mysql", "mariadb"):
        for statement in (
            f"SET SESSION max_execution_time = {millis}",
            f"SET SESSION max_statement_time = {seconds:.3f}",
        ):
            try:
                conn.exec_driver_sql(statement)
            except Exception:  # noqa: BLE001 — the other server flavour's variable
                continue
        return lambda: None
    if dialect == "sqlite":
        raw = conn.connection.driver_connection
        deadline = time.monotonic() + seconds
        raw.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        return lambda: raw.set_progress_handler(None, 0)
    return lambda: None


def _is_timeout(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "interrupted",
            "statement timeout",
            "canceling statement",
            "max_execution_time",
            "max_statement_time",
            "query execution was interrupted",
        )
    )


@agent_function
async def run_query(
    sql: str,
    project_root: Path | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout: float | None = DEFAULT_QUERY_TIMEOUT,
) -> AgentResult:
    """Execute a read-only SQL query against the project database.

    Only ``SELECT``, ``WITH``, and ``EXPLAIN`` queries are allowed.

    Args:
        sql: A SQL query string.
        project_id: The host-assigned project id (required).
        max_rows: At most this many rows are returned (default 1000);
            ``truncated`` says whether there were more.
        timeout: Seconds the statement may run (default 30; ``None`` for no
            limit). Enforced by the database where it can be: PostgreSQL
            ``statement_timeout``, MySQL ``max_execution_time`` / MariaDB
            ``max_statement_time``, and an interrupting progress handler on
            SQLite.

    Returns data (on success):
        rows (list[dict]): result rows, each mapping column name to value.
        count (int): len(rows).
        truncated (bool): more rows existed than *max_rows*.
        max_rows (int): the cap that was applied.

    Notes:
        - The query is rejected (``success=False``,
          ``error_code="invalid_sql"``) when it is not a single read-only
          ``SELECT`` / ``WITH`` / ``EXPLAIN`` statement. The check runs on the
          statement's code with string literals blanked, under every quoting
          and comment convention of the supported databases, and also refuses
          ``SELECT … INTO`` (OUTFILE/DUMPFILE, a new table), transaction
          control, and calls with side effects outside the query
          (``pg_sleep``, ``pg_read_file``, ``lo_import``/``lo_export``,
          ``dblink``, ``set_config``, ``nextval``, ``load_extension``, …).
        - A statement that exceeds *timeout* is cancelled and fails with
          ``error_code="query_timeout"``.
        - Execution always runs inside a transaction that is rolled back, so
          nothing is ever committed even if a mutation slipped past the gate.
    """
    error = _validate_read_only_sql(sql)
    if error:
        return fail(error, code="invalid_sql")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        return fail(f"max_rows must be a positive integer, got {max_rows!r}", code="invalid_input")
    deadline = validate_timeout(timeout)
    root = project_root

    def _run() -> tuple[list[dict], bool]:
        from sqlalchemy import create_engine, text
        engine = create_engine(_sync_db_url(root))
        try:
            with engine.connect() as conn:
                # Defense in depth: run inside an explicit transaction that is
                # ALWAYS rolled back, so nothing that slips past the keyword
                # gate can ever be committed.
                trans = conn.begin()
                cleanup = lambda: None  # noqa: E731
                try:
                    if deadline is not None:
                        cleanup = _apply_statement_timeout(
                            conn, engine.dialect.name, deadline
                        )
                    try:
                        result = conn.execute(text(sql))
                        if not result.returns_rows:
                            return [], False
                        keys = list(result.keys())
                        fetched = result.fetchmany(max_rows + 1)
                    except Exception as exc:
                        if deadline is not None and _is_timeout(exc):
                            raise AgentError(
                                f"Query cancelled after {deadline:g}s (timeout).",
                                code="query_timeout",
                                timeout=deadline,
                            ) from exc
                        raise
                    finally:
                        cleanup()
                    truncated = len(fetched) > max_rows
                    return [dict(zip(keys, row)) for row in fetched[:max_rows]], truncated
                finally:
                    trans.rollback()
        finally:
            engine.dispose()

    rows, truncated = await asyncio.to_thread(_run)
    return AgentResult(
        success=True,
        message=(
            f"Query returned {len(rows)} row(s)"
            + (f" (truncated at max_rows={max_rows})" if truncated else "")
        ),
        data={"rows": rows, "count": len(rows), "truncated": truncated, "max_rows": max_rows},
    )
