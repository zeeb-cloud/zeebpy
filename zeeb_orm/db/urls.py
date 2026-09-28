"""The synchronous driver of a database URL, named rather than defaulted."""

from __future__ import annotations

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

    Only the scheme is rewritten; the rest of the URL — credentials, host,
    query — is passed through byte for byte, never re-parsed or re-escaped.
    """
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    return f"{_SYNC_SCHEMES.get(scheme.lower(), scheme)}://{rest}"


__all__ = ["sync_database_url"]
