"""Connections, transactions and SQLite integrity.

Each test pins a defect that used to pass silently:

- async ``on_commit`` callbacks were scheduled and forgotten (errors lost),
  and a callback raising after the commit made ``atomic()`` roll back an
  already-committed session
- ``asyncio.gather`` inside ``atomic()`` ran concurrent statements on one
  ``AsyncSession``
- re-registering an alias leaked the old engine, ``configure()`` kept serving
  a stale default, ``temporary_database`` closed every connection, driver
  detection searched the URL text, and ``iterator()`` broke on a sync URL
- SQLite never enforced foreign keys, and deleting a row left its
  many-to-many join rows behind
"""

from __future__ import annotations

import asyncio
import warnings

import pytest

from zeeb_orm import Database, Model, atomic, fields, register_database
from zeeb_orm.db import connection as connection_module
from zeeb_orm.db.connection import get_connection, get_database, setup_database
from zeeb_orm.db.transaction import on_commit
from zeeb_orm.exceptions import IntegrityError
from zeeb_orm.testing import temporary_database


class DlParent(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "dl_parents"


class DlChild(Model):
    name = fields.CharField(max_length=50)
    parent = fields.ForeignKey(DlParent, on_delete="DO_NOTHING", related_name="dl_children")

    class Meta:
        table_name = "dl_children"


class DlLabel(Model):
    text = fields.CharField(max_length=50)
    parents = fields.ManyToMany(DlParent, related_name="dl_labels")

    class Meta:
        table_name = "dl_labels"


MODELS = (DlParent, DlChild, DlLabel)


@pytest.fixture
async def db():
    async with temporary_database(*MODELS) as database:
        yield database


async def _scalar(sql: str):
    from sqlalchemy import text

    database = await get_connection()
    async with database.session() as session:
        return (await session.execute(text(sql))).scalar()


# ---------------------------------------------------------------------------
# 12. on_commit: awaited, after the commit, failures never un-commit
# ---------------------------------------------------------------------------


class TestOnCommit:
    async def test_an_async_callback_is_awaited_before_atomic_returns(self, db):
        ran: list[str] = []

        async def callback():
            await asyncio.sleep(0.05)  # a scheduled-and-forgotten task is still asleep
            ran.append("done")

        async with atomic():
            await DlParent.objects.create(name="p")
            on_commit(callback)

        assert ran == ["done"]

    async def test_a_callback_runs_after_the_commit_outside_the_transaction(self, db):
        seen: list[int] = []

        async def callback():
            # A fresh session: the transaction is committed and released.
            seen.append(await DlParent.objects.count())
            await DlParent.objects.create(name="from-callback")

        async with atomic():
            await DlParent.objects.create(name="p")
            on_commit(callback)

        assert seen == [1]
        assert await DlParent.objects.count() == 2

    async def test_a_failing_callback_cannot_roll_back_the_commit(self, db):
        async def write():
            await DlParent.objects.create(name="written by a callback")

        def boom():
            raise RuntimeError("callback failed")

        with pytest.raises(RuntimeError, match="callback failed"):
            async with atomic():
                await DlParent.objects.create(name="kept")
                on_commit(write)
                on_commit(boom)

        # Neither the transaction nor the earlier callback's own write was
        # rolled back by the failure that came after the commit.
        assert await DlParent.objects.count() == 2

    async def test_a_robust_callback_is_logged_and_the_next_one_runs(self, db, caplog):
        ran: list[str] = []

        async def boom():
            raise RuntimeError("async callback failed")

        async with atomic():
            await DlParent.objects.create(name="p")
            on_commit(boom, robust=True)
            on_commit(lambda: ran.append("second"))

        assert ran == ["second"]
        assert "async callback failed" in caplog.text


# ---------------------------------------------------------------------------
# 13. One session per transaction, used by one statement at a time
# ---------------------------------------------------------------------------


class TestGatherInsideAtomic:
    async def test_statements_on_the_shared_session_never_overlap(self, db, monkeypatch):
        """SQLAlchemy forbids concurrent operations on one AsyncSession.

        aiosqlite happens to tolerate it; asyncpg fails with "another operation
        is in progress". Measure the overlap directly instead of relying on the
        driver to notice.
        """
        from sqlalchemy.ext.asyncio import AsyncSession

        original = AsyncSession.execute
        running = 0
        peak = 0

        async def execute(self, *args, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            try:
                await asyncio.sleep(0)  # give a concurrent caller its chance
                return await original(self, *args, **kwargs)
            finally:
                running -= 1

        monkeypatch.setattr(AsyncSession, "execute", execute)
        async with atomic():
            await asyncio.gather(
                *(DlParent.objects.create(name=f"p{i}") for i in range(5)),
                *(DlParent.objects.count() for _ in range(5)),
            )
        assert peak == 1

    async def test_gathered_queries_share_the_transaction_safely(self, db):
        async with atomic():
            await asyncio.gather(
                *(DlParent.objects.create(name=f"p{i}") for i in range(10)),
                *(DlParent.objects.count() for _ in range(10)),
            )
            assert await DlParent.objects.count() == 10

        assert await DlParent.objects.count() == 10

    async def test_gathered_writes_roll_back_together(self, db):
        with pytest.raises(RuntimeError):
            async with atomic():
                await asyncio.gather(*(DlParent.objects.create(name=f"p{i}") for i in range(5)))
                raise RuntimeError("abort")

        assert await DlParent.objects.count() == 0


# ---------------------------------------------------------------------------
# 14. Connection registry and driver detection
# ---------------------------------------------------------------------------


class TestConnectionRegistry:
    async def test_setup_database_disposes_the_default_it_replaces(self):
        first = await setup_database("sqlite+aiosqlite:///:memory:")
        try:
            second = await setup_database("sqlite+aiosqlite:///:memory:")
            assert get_database() is second
            assert first._async_engine is None and not first._connected
        finally:
            await connection_module.close_all_connections()

    async def test_register_database_disposes_the_replaced_alias(self):
        first = Database("sqlite+aiosqlite:///:memory:")
        await first.connect()
        register_database(first, "dl_other")
        second = Database("sqlite+aiosqlite:///:memory:")
        await second.connect()
        register_database(second, "dl_other")
        await asyncio.gather(*connection_module._pending_disposals)
        try:
            assert not first._connected
        finally:
            await connection_module._connections.pop("dl_other").disconnect()

    async def test_configure_drops_a_default_built_from_old_settings(self, tmp_path):
        from zeeb_orm.conf.settings import Settings, configure

        snapshot = Settings._instance
        try:
            configure(database={"url": "sqlite+aiosqlite:///:memory:"})
            lazy = await get_connection()
            configure(database={"url": f"sqlite+aiosqlite:///{tmp_path / 'new.sqlite3'}"})
            fresh = await get_connection()
            assert fresh is not lazy
            assert fresh.url.endswith("new.sqlite3")
            await asyncio.gather(*connection_module._pending_disposals)
            assert not lazy._connected
        finally:
            await connection_module.close_all_connections()
            Settings._instance = snapshot

    async def test_temporary_database_leaves_other_aliases_open(self):
        other = Database("sqlite+aiosqlite:///:memory:")
        await other.connect()
        register_database(other, "dl_keep")
        try:
            async with temporary_database(DlParent):
                pass
            assert get_database("dl_keep") is other and other._connected
        finally:
            await connection_module._connections.pop("dl_keep").disconnect()

    @pytest.mark.parametrize(
        ("url", "is_async", "is_sqlite"),
        [
            ("sqlite+aiosqlite:///db.sqlite3", True, True),
            ("postgresql+asyncpg://u:p@h/db", True, False),
            ("mysql+aiomysql://u:p@h/db", True, False),
            ("mysql+asyncmy://u:p@h/db", True, False),
            ("postgresql://u:p@h/db", False, False),
            ("sqlite:///db.sqlite3", False, True),
            # A database *named* after an async driver is not an async URL.
            ("postgresql://u:p@h/aiosqlite_asyncpg", False, False),
            ("postgresql://u:p@sqlitehost/db", False, False),
        ],
    )
    def test_driver_detection_parses_the_url(self, url, is_async, is_sqlite):
        database = Database(url)
        assert database.is_async is is_async
        assert database.is_sqlite is is_sqlite

    async def test_a_sync_url_warns_and_iterator_still_works(self, tmp_path):
        from zeeb_orm.models.base import metadata

        url = f"sqlite:///{tmp_path / 'sync.sqlite3'}"
        with pytest.warns(RuntimeWarning, match="block the event loop"):
            database = await setup_database(url)
        try:
            table = DlParent._get_table()
            with database._sync_engine.begin() as conn:
                metadata.create_all(conn, tables=[table])
            for i in range(5):
                await DlParent.objects.create(name=f"p{i}")

            names = [p.name async for p in DlParent.objects.order_by("name").iterator(2)]
            assert names == [f"p{i}" for i in range(5)]

            async with atomic():
                streamed = [p async for p in DlParent.objects.iterator(chunk_size=2)]
            assert len(streamed) == 5
        finally:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                await connection_module.close_all_connections()


# ---------------------------------------------------------------------------
# 16. SQLite enforces foreign keys; deletes leave no join rows behind
# ---------------------------------------------------------------------------


class TestSqliteIntegrity:
    async def test_foreign_keys_are_enforced(self, db):
        assert await _scalar("PRAGMA foreign_keys") == 1

        import uuid

        with pytest.raises(IntegrityError):
            await DlChild.objects.create(name="orphan", parent_id=uuid.uuid4())

    async def test_deleting_a_row_removes_its_m2m_links(self, db):
        parent = await DlParent.objects.create(name="p")
        label = await DlLabel.objects.create(text="l")
        await label.parents.add(parent)
        assert await _scalar("SELECT COUNT(*) FROM dl_labels_parents") == 1

        await parent.delete()
        assert await _scalar("SELECT COUNT(*) FROM dl_labels_parents") == 0

        parent2 = await DlParent.objects.create(name="p2")
        await label.parents.add(parent2)
        await DlLabel.objects.filter(pk=label.pk).delete()
        assert await _scalar("SELECT COUNT(*) FROM dl_labels_parents") == 0
        assert await DlParent.objects.count() == 1

    async def test_the_collector_removes_join_rows_even_with_enforcement_off(self, db):
        """The Python side does not rely on the database's ON DELETE CASCADE."""
        from sqlalchemy import text

        parent = await DlParent.objects.create(name="p")
        label = await DlLabel.objects.create(text="l")
        await label.parents.add(parent)

        # The in-memory database has one pooled connection; switch its
        # enforcement off (a PRAGMA is ignored inside a transaction).
        database = await get_connection()
        async with database._async_engine.connect() as conn:
            await conn.execute(text("PRAGMA foreign_keys=OFF"))
            await conn.commit()
        assert await _scalar("PRAGMA foreign_keys") == 0

        await label.delete()
        assert await _scalar("SELECT COUNT(*) FROM dl_labels_parents") == 0
