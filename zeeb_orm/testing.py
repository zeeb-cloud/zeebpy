"""Run tests against a real database instead of a mocked queryset.

Mocking the ORM is the obvious way to test code that talks to it, and it hides
exactly the bugs worth finding. A stub whose ``filter()`` returns ``self``
reports success for a query with the wrong lookup, the wrong field, or no tenant
scoping at all; a stub whose ``update()`` returns a fixed row count cannot tell a
conditional update that matched from one the database refused. Those are the
defects that reach production, and no amount of asserting on call arguments
finds them, because the assertion and the code under test share the same
misunderstanding.

This module gives a real database cheaply enough that there is no excuse::

    from zeeb_orm.testing import temporary_database

    @pytest.fixture
    async def db():
        async with temporary_database(Tenant, UsageEvent) as db:
            yield db

    async def test_conditional_update_is_refused_at_the_limit():
        await Quota.objects.create(tenant_id=tid, used=10, limit=10)
        changed = await Quota.objects.filter(tenant_id=tid, used__lt=10).update(used=11)
        assert changed == 0          # the database arbitrated, not a mock

The default is in-memory SQLite, which needs no service and adds milliseconds.
Set ``ZEEB_TEST_DATABASE_URL`` to run the same tests against PostgreSQL — worth
doing in CI, because SQLite will not reproduce dialect-specific behaviour:
partial and expression indexes, ``ON CONFLICT`` semantics, advisory locks,
``SELECT FOR UPDATE``, or real concurrent transactions. Tests that depend on any
of those should say so with :func:`requires_postgres`.

**What this does not cover.** Tables are built from the model definitions, so
anything a migration adds and the models do not declare — a raw ``RunSQL``
index, a check constraint, a trigger — does not exist here. If a constraint is
load-bearing, declare it on the model's ``Meta`` so it is part of the schema
this harness creates and the migration merely materialises it.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from zeeb_orm.conf.settings import Settings
from zeeb_orm.db.connection import (
    Database,
    close_all_connections,
    register_database,
)

#: Needs no running service and costs milliseconds, so a real-database test is
#: cheap enough to be the default choice rather than a special occasion.
DEFAULT_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

#: Point the whole suite at another engine, e.g.
#: ``postgresql+asyncpg://postgres:postgres@localhost/test``.
TEST_DATABASE_URL_ENV = "ZEEB_TEST_DATABASE_URL"


def resolve_database_url(url: str | None = None) -> str:
    """Resolve the URL to test against: explicit, then env, then in-memory."""
    return url or os.environ.get(TEST_DATABASE_URL_ENV) or DEFAULT_TEST_DATABASE_URL


def is_postgres(url: str | None = None) -> bool:
    """Whether the configured test database is PostgreSQL."""
    resolved = resolve_database_url(url).lower()
    return resolved.startswith("postgres") or "postgresql" in resolved


def requires_postgres(url: str | None = None) -> Any:
    """``pytest.mark.skipif`` for behaviour SQLite cannot reproduce.

    Use it for partial indexes, ``ON CONFLICT``, advisory locks, row locking and
    genuine transaction concurrency. Marking such a test is honest; quietly
    letting it pass on SQLite is how a test ends up asserting nothing.
    """
    import pytest

    return pytest.mark.skipif(
        not is_postgres(url),
        reason=(
            f"needs PostgreSQL semantics; set {TEST_DATABASE_URL_ENV} to a "
            "postgresql+asyncpg:// URL to run this test"
        ),
    )


def _model_tables(models: Sequence[type]) -> list[Any]:
    """Resolve each model's SQLAlchemy table, reusing what already exists.

    ``build_table()`` memoises on ``Model._sa_table`` and, when that is empty,
    constructs a fresh ``Table`` against the process-global metadata. Clearing
    the cache to "start clean" therefore fails the second time round with
    *Table is already defined for this MetaData instance*. It also breaks a test
    suite that re-imports a models module (to isolate same-named ``apps``
    packages, say): the new class has no cache, but the old class's table is
    still registered under the same name.

    Adopting the registered table covers both: the schema lives in the metadata
    for the life of the process, while this context owns only the DDL.
    """
    from zeeb_orm.models.base import metadata

    tables = []
    for model in models:
        if model._sa_table is None:
            existing = metadata.tables.get(model._meta.db_table)
            if existing is not None:
                model._sa_table = existing
        tables.append(model._get_table())
    return tables


@asynccontextmanager
async def temporary_database(
    *models: type,
    url: str | None = None,
    create_tables: bool = True,
) -> AsyncIterator[Database]:
    """Yield a connected :class:`Database` with ``models``' tables created.

    Pass every model whose table is involved, including the targets of foreign
    keys: only the tables named here are created, so a missing referent fails at
    DDL time on a backend that enforces them.

    Global ORM state — the settings singleton, the connection registry and the
    per-model table cache — is restored on exit, so one test cannot leave a
    half-configured ORM behind for the next. Only the tables this context
    created are dropped; anything already in the shared metadata is untouched.
    """
    resolved_url = resolve_database_url(url)

    settings_snapshot = Settings._instance if hasattr(Settings, "_instance") else None
    Settings.reset()

    from zeeb_orm.conf.settings import configure

    configure(database={"url": resolved_url})

    db = Database(resolved_url)
    await db.connect()
    register_database(db)

    tables = _model_tables(models)
    created = False
    try:
        if create_tables and tables:
            engine = db._async_engine
            if engine is None:  # pragma: no cover - sync engines are not used here
                raise RuntimeError(
                    f"{resolved_url} resolved to a synchronous engine; the test "
                    "harness needs an async driver (aiosqlite, asyncpg)."
                )
            async with engine.begin() as conn:
                # tables=... rather than a bare create_all: the metadata is
                # process-global and may hold models from elsewhere in the
                # suite, which are none of this context's business.
                await conn.run_sync(_create, tables)
            created = True
        yield db
    finally:
        try:
            if created and db._async_engine is not None:
                async with db._async_engine.begin() as conn:
                    await conn.run_sync(_drop, tables)
        finally:
            await close_all_connections()
            # Deliberately leaves _sa_table intact: the table definition is
            # process-global and shared, and dropping the cache here is what
            # makes a second context collide on the same table name.
            Settings.reset()
            if settings_snapshot is not None and hasattr(Settings, "_instance"):
                Settings._instance = settings_snapshot


def _create(conn: Any, tables: list[Any]) -> None:
    from zeeb_orm.models.base import metadata

    metadata.create_all(conn, tables=tables, checkfirst=True)


def _drop(conn: Any, tables: list[Any]) -> None:
    from zeeb_orm.models.base import metadata

    metadata.drop_all(conn, tables=list(reversed(tables)), checkfirst=True)
