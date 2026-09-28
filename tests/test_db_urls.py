"""A synchronous engine names its driver instead of taking SQLAlchemy's default.

SQLAlchemy 2.1 made psycopg (v3) the default driver for a bare
``postgresql://``. zeebpy installs psycopg2, and every sync engine it built —
the migrator, ``Database`` on a sync URL, the agent layer's inspection — passed
exactly such a URL, so a fresh install failed with ``No module named 'psycopg'``
the first time it migrated.
"""

from __future__ import annotations

import pytest
import sqlalchemy
from sqlalchemy.engine import make_url

import zeeb_agents.database
import zeeb_agents.health
import zeeb_agents.users
from zeeb_orm.db import Database, sync_database_url
from zeeb_orm.db import connection as connection_module
from zeeb_orm.migrations import executor


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5432/db", "postgresql+psycopg2://u:p@h:5432/db"),
        ("postgresql+asyncpg://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgres://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("mysql://u:p@h/db", "mysql+pymysql://u:p@h/db"),
        ("mysql+aiomysql://u:p@h/db", "mysql+pymysql://u:p@h/db"),
        ("sqlite+aiosqlite:///db.sqlite3", "sqlite:///db.sqlite3"),
        ("sqlite:///:memory:", "sqlite:///:memory:"),
        # A driver named explicitly is the caller's choice and is kept.
        ("postgresql+psycopg2://u:p@h/db", "postgresql+psycopg2://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("not a url", "not a url"),
    ],
)
def test_every_sync_url_names_the_driver_zeebpy_installs(url, expected):
    assert sync_database_url(url) == expected


def test_only_the_scheme_changes():
    """Credentials are never re-parsed, so an escaped password survives as written."""
    url = "postgresql+asyncpg://role:p%40ss%2Fw0rd@db.internal:6543/app?sslmode=require"

    assert sync_database_url(url) == "postgresql+psycopg2://role:p%40ss%2Fw0rd@db.internal:6543/app?sslmode=require"


def test_a_bare_postgres_url_resolves_to_psycopg2_whatever_the_default():
    assert make_url(sync_database_url("postgresql://u:p@h/db")).get_dialect().driver == "psycopg2"


class _EngineRequestedError(Exception):
    pass


def _capturing(seen: list[str]):
    def create_engine(url, *args, **kwargs):
        seen.append(str(url))
        raise _EngineRequestedError

    return create_engine


@pytest.mark.parametrize("command", [executor.migrate, executor.showmigrations])
def test_the_migrator_connects_with_a_named_driver(command, monkeypatch, tmp_path):
    seen: list[str] = []
    monkeypatch.setattr(sqlalchemy, "create_engine", _capturing(seen))

    with pytest.raises(_EngineRequestedError):
        command(database_url="postgresql+asyncpg://u:p@h/db", project_root=tmp_path)

    assert seen == ["postgresql+psycopg2://u:p@h/db"]


async def test_a_database_on_a_sync_url_connects_with_a_named_driver(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(connection_module, "create_engine", _capturing(seen))
    database = Database("postgresql://u:p@h/db")

    with pytest.raises(_EngineRequestedError):
        await database.connect()

    assert seen == ["postgresql+psycopg2://u:p@h/db"]
    assert database.url == "postgresql://u:p@h/db", "the configured URL itself is left as given"


@pytest.mark.parametrize("module", [zeeb_agents.database, zeeb_agents.health, zeeb_agents.users])
def test_the_agent_layer_inspects_with_a_named_driver(module, monkeypatch, tmp_path):
    monkeypatch.setattr(module, "load_project_settings", lambda root: {})
    monkeypatch.setattr(module, "resolve_db_url", lambda settings, root: "postgresql+asyncpg://u:p@h/db")

    assert module._sync_db_url(tmp_path) == "postgresql+psycopg2://u:p@h/db"
