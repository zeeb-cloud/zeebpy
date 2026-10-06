"""The driver of a database URL, named rather than defaulted."""

from __future__ import annotations

import importlib.util

# The sync drivers zeebpy installs: psycopg2 with the ``postgresql`` extra,
# pymysql with ``mysql``, and the standard library's sqlite3.
_SYNC_SCHEMES: dict[str, str] = {
    "postgresql": "postgresql+psycopg2",
    "postgres": "postgresql+psycopg2",
    "postgresql+asyncpg": "postgresql+psycopg2",
    "mysql": "mysql+pymysql",
    "mysql+aiomysql": "mysql+pymysql",
    "mysql+asyncmy": "mysql+pymysql",
    "sqlite+aiosqlite": "sqlite",
}


#: asyncpg's ``ssl`` values mapped to libpq's ``sslmode``.
_ASYNCPG_SSL_TO_SSLMODE: dict[str, str] = {
    "true": "require",
    "1": "require",
    "on": "require",
    "false": "disable",
    "0": "disable",
    "off": "disable",
    "disable": "disable",
    "allow": "allow",
    "prefer": "prefer",
    "require": "require",
    "verify-ca": "verify-ca",
    "verify_ca": "verify-ca",
    "verify-full": "verify-full",
    "verify_full": "verify-full",
}

#: Query parameters only the asyncpg driver (or SQLAlchemy's asyncpg dialect)
#: understands. psycopg2 hands unknown ones to libpq, which refuses to connect.
_ASYNCPG_ONLY_PARAMS = frozenset(
    {
        "prepared_statement_cache_size",
        "prepared_statement_name_func",
        "statement_cache_size",
        "max_cached_statement_lifetime",
        "max_cacheable_statement_size",
        "command_timeout",
        "server_settings",
        "direct_tls",
    }
)

#: Query parameters aiomysql/asyncmy accept that pymysql.connect() does not.
_ASYNC_MYSQL_ONLY_PARAMS = frozenset({"echo", "loop", "auth_plugin"})


def _translate_query(driver: str, query: str) -> str:
    """Rewrite the async driver's query parameters for its sync counterpart.

    Each ``key=value`` segment is kept byte for byte unless it has to change:
    asyncpg's ``ssl`` becomes libpq's ``sslmode`` and its ``timeout`` becomes
    ``connect_timeout``; parameters only the async driver understands are
    dropped. Everything else — escaping included — passes through untouched.
    """
    if not query:
        return query
    kept: list[str] = []
    for segment in query.split("&"):
        key, eq, value = segment.partition("=")
        if driver == "asyncpg":
            if key in _ASYNCPG_ONLY_PARAMS:
                continue
            if key == "ssl":
                mode = _ASYNCPG_SSL_TO_SSLMODE.get(value.lower())
                if mode is not None:
                    kept.append(f"sslmode={mode}")
                continue
            if key == "timeout":
                kept.append(f"connect_timeout{eq}{value}")
                continue
        elif driver in ("aiomysql", "asyncmy"):
            if key in _ASYNC_MYSQL_ONLY_PARAMS:
                continue
        kept.append(segment)
    return "&".join(kept)


def sync_database_url(url: str) -> str:
    """Return the URL a *synchronous* SQLAlchemy engine should connect with.

    A URL that leaves the driver out gets whatever the installed SQLAlchemy
    defaults to, and that default moves: SQLAlchemy 2.1 made psycopg (v3) the
    driver for a bare ``postgresql://``, which zeebpy never installs, so every
    sync engine — the migrator, ``Database`` on a sync URL, the agent layer's
    inspection — failed with ``No module named 'psycopg'``. Async drivers are
    mapped to their sync pair, a bare scheme gets the driver zeebpy ships
    (``mysql://`` would otherwise need mysqlclient, also never installed),
    and a URL that already names a driver is returned unchanged.

    When an async driver is swapped out, its query parameters are translated
    too: an asyncpg URL's ``?ssl=require`` would otherwise reach libpq, which
    has no ``ssl`` option and refuses the connection (see
    :func:`_translate_query`). Credentials, host and every other parameter are
    passed through byte for byte, never re-parsed or re-escaped.
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    sync_scheme = _SYNC_SCHEMES.get(scheme.lower(), scheme)
    if sync_scheme != scheme and "+" in scheme:
        driver = scheme.lower().partition("+")[2]
        location, question, query = rest.partition("?")
        if question:
            query = _translate_query(driver, query)
            rest = f"{location}?{query}" if query else location
    return f"{sync_scheme}://{rest}"


#: Bare schemes that name a backend but no driver, mapped to the asyncio driver
#: zeebpy installs for it.
_ASYNC_SCHEMES: dict[str, tuple[str, str]] = {
    "postgresql": ("postgresql+asyncpg", "asyncpg"),
    "postgres": ("postgresql+asyncpg", "asyncpg"),
}


def _driver_installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def async_database_url(url: str) -> str:
    """Return the URL an *asyncio* server should connect with.

    A platform or a ``DATABASE_URL`` written for libpq names the backend and
    leaves the driver out (``postgresql://…``). Read literally that means
    psycopg2, a synchronous driver: every query an async server makes would
    block its event loop. A bare scheme names no choice, so it gets the async
    driver zeebpy ships when that driver is installed.

    Only a URL with nothing a driver swap could break is upgraded. A URL that
    already names a driver is the author's choice and is returned unchanged;
    so is one whose query carries anything but ``sslmode``, which is
    translated to asyncpg's ``ssl`` — libpq's other options have no asyncpg
    equivalent, and passing them on would refuse the connection.
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    target = _ASYNC_SCHEMES.get(scheme.lower())
    if target is None:
        return url
    async_scheme, module = target
    if not _driver_installed(module):
        return url
    location, question, query = rest.partition("?")
    if question and query:
        kept: list[str] = []
        for segment in query.split("&"):
            key, eq, value = segment.partition("=")
            if key != "sslmode":
                return url
            kept.append(f"ssl{eq}{value}")
        rest = f"{location}?{'&'.join(kept)}"
    else:
        rest = location
    return f"{async_scheme}://{rest}"


__all__ = ["async_database_url", "sync_database_url"]
