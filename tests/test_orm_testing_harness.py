"""Tests for ``zeeb_orm.testing`` — the real-database test harness.

Each test here asserts something a mocked queryset gets wrong, because that is
the harness's entire reason to exist: if these pass against a stub too, the
harness is buying nothing.
"""

import asyncio
import os

import pytest

from zeeb_orm import Model, fields
from zeeb_orm.testing import (
    DEFAULT_TEST_DATABASE_URL,
    TEST_DATABASE_URL_ENV,
    is_postgres,
    resolve_database_url,
    temporary_database,
)


class HarnessQuota(Model):
    tenant = fields.CharField(max_length=50)
    used = fields.IntegerField(default=0)
    ceiling = fields.IntegerField(default=0)

    class Meta:
        table_name = "harness_quota"


class HarnessNote(Model):
    tenant = fields.CharField(max_length=50)
    body = fields.CharField(max_length=200)

    class Meta:
        table_name = "harness_note"


@pytest.fixture
async def db():
    async with temporary_database(HarnessQuota, HarnessNote) as database:
        yield database


class TestUrlResolution:
    def test_defaults_to_in_memory_sqlite(self, monkeypatch):
        monkeypatch.delenv(TEST_DATABASE_URL_ENV, raising=False)
        assert resolve_database_url() == DEFAULT_TEST_DATABASE_URL
        assert is_postgres() is False

    def test_environment_overrides_the_default(self, monkeypatch):
        monkeypatch.setenv(TEST_DATABASE_URL_ENV, "postgresql+asyncpg://localhost/t")
        assert resolve_database_url() == "postgresql+asyncpg://localhost/t"
        assert is_postgres() is True

    def test_explicit_argument_wins_over_environment(self, monkeypatch):
        monkeypatch.setenv(TEST_DATABASE_URL_ENV, "postgresql+asyncpg://localhost/t")
        assert resolve_database_url(DEFAULT_TEST_DATABASE_URL) == DEFAULT_TEST_DATABASE_URL


class TestTheDatabaseDecides:
    """The behaviours a stubbed queryset reports incorrectly."""

    @pytest.mark.asyncio
    async def test_filter_actually_filters(self, db):
        await HarnessQuota.objects.create(tenant="a", used=1, ceiling=10)
        await HarnessQuota.objects.create(tenant="b", used=2, ceiling=10)

        assert await HarnessQuota.objects.filter(tenant="a").count() == 1
        # A stub returning `self` reports 2 here, and a missing tenant scope
        # looks identical to a correct one.
        assert await HarnessQuota.objects.filter(tenant="absent").count() == 0

    @pytest.mark.asyncio
    async def test_conditional_update_is_refused_when_the_predicate_fails(self, db):
        """The atomic-reservation pattern, which a stub cannot express.

        A stub's ``update()`` returns a row count derived from the list it was
        seeded with, so it answers the same whether the predicate matched or
        not — and the row count *is* the admission decision.
        """
        await HarnessQuota.objects.create(tenant="full", used=10, ceiling=10)
        await HarnessQuota.objects.create(tenant="room", used=4, ceiling=10)

        refused = await HarnessQuota.objects.filter(tenant="full", used__lt=10).update(used=11)
        granted = await HarnessQuota.objects.filter(tenant="room", used__lt=10).update(used=5)

        assert refused == 0
        assert granted == 1
        assert (await HarnessQuota.objects.get(tenant="full")).used == 10

    @pytest.mark.asyncio
    async def test_lookup_operators_are_interpreted(self, db):
        for n in range(1, 6):
            await HarnessQuota.objects.create(tenant=f"t{n}", used=n, ceiling=10)

        assert await HarnessQuota.objects.filter(used__gte=4).count() == 2
        assert await HarnessQuota.objects.filter(used__in=[1, 2]).count() == 2
        # A wrong lookup name is a real bug a stub silently accepts.
        with pytest.raises(Exception):
            await HarnessQuota.objects.filter(used__nonsense=1).count()

    @pytest.mark.asyncio
    async def test_order_by_actually_orders(self, db):
        await HarnessQuota.objects.create(tenant="low", used=1, ceiling=10)
        await HarnessQuota.objects.create(tenant="high", used=9, ceiling=10)

        rows = await HarnessQuota.objects.order_by("-used")
        assert [r.tenant for r in rows] == ["high", "low"]

    @pytest.mark.asyncio
    async def test_delete_only_removes_matching_rows(self, db):
        await HarnessNote.objects.create(tenant="keep", body="x")
        await HarnessNote.objects.create(tenant="drop", body="y")

        removed = await HarnessNote.objects.filter(tenant="drop").delete()

        assert removed == 1
        assert await HarnessNote.objects.count() == 1


class TestIsolation:
    @pytest.mark.asyncio
    async def test_each_context_starts_empty(self):
        """No state leaks between contexts, so tests cannot order-depend."""
        async with temporary_database(HarnessNote):
            await HarnessNote.objects.create(tenant="t", body="first")
            assert await HarnessNote.objects.count() == 1

        async with temporary_database(HarnessNote):
            assert await HarnessNote.objects.count() == 0

    @pytest.mark.asyncio
    async def test_unrelated_tables_are_left_alone(self):
        """Only the tables a context created are dropped.

        The metadata is process-global; a context that cleared it would break
        every other model registered in the same interpreter.
        """
        from zeeb_orm.models.base import metadata

        async with temporary_database(HarnessQuota):
            pass

        # HarnessNote was never handed to that context, so whatever the shared
        # metadata knows about it must survive untouched.
        HarnessNote._sa_table = None
        assert HarnessNote._get_table() is metadata.tables[HarnessNote._meta.db_table]

    @pytest.mark.asyncio
    async def test_settings_are_restored_after_the_context(self):
        """The caller's ORM configuration survives, whatever it was.

        The context repoints the settings singleton at its own database; leaving
        that in place would silently redirect every later test in the process.
        """
        from zeeb_orm.conf.settings import Settings

        before = Settings._instance
        async with temporary_database(HarnessNote):
            pass
        assert Settings._instance is before

    @pytest.mark.asyncio
    async def test_concurrent_writes_are_serialized_by_the_database(self, db):
        """Several coroutines contending for the last unit; one wins.

        This is the shape of a quota admission check. On SQLite the contention
        is resolved by the file lock rather than true parallelism, so it proves
        the query is conditional, not that the isolation level is right — that
        distinction is why `requires_postgres` exists.
        """
        await HarnessQuota.objects.create(tenant="one-left", used=9, ceiling=10)

        async def claim():
            return await HarnessQuota.objects.filter(tenant="one-left", used__lt=10).update(used=10)

        results = await asyncio.gather(*(claim() for _ in range(5)))

        assert sum(results) == 1, "exactly one caller may take the last unit"


class TestPostgresGate:
    def test_marker_skips_when_not_on_postgres(self, monkeypatch):
        """The gate must not let a Postgres-only assertion pass on SQLite."""
        from zeeb_orm.testing import requires_postgres

        monkeypatch.delenv(TEST_DATABASE_URL_ENV, raising=False)
        marker = requires_postgres()
        assert marker.args[0] is True  # condition: skip
        assert TEST_DATABASE_URL_ENV in marker.kwargs["reason"]

    def test_marker_runs_when_pointed_at_postgres(self, monkeypatch):
        from zeeb_orm.testing import requires_postgres

        monkeypatch.setenv(TEST_DATABASE_URL_ENV, "postgresql+asyncpg://localhost/t")
        assert requires_postgres().args[0] is False


def test_harness_does_not_require_a_running_service():
    """The default must stay zero-setup or nobody will use it."""
    assert DEFAULT_TEST_DATABASE_URL.startswith("sqlite")
    assert os.environ.get(TEST_DATABASE_URL_ENV) in (None, "") or is_postgres()
