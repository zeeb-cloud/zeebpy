"""On SQLite, a savepoint opened before anything was written is still inside
the outer transaction.

The sqlite3/aiosqlite drivers only send BEGIN in front of a write, so a nested
``atomic()`` that is the first thing an outer block does would run outside any
transaction, and releasing its savepoint would commit: the outer block's
rollback could not undo it. ``atomic()`` begins the outer transaction
explicitly before such a savepoint.
"""

from __future__ import annotations

import pytest

from zeeb_orm import Model, fields
from zeeb_orm.db.connection import atomic
from zeeb_orm.testing import temporary_database


class SqtRow(Model):
    name = fields.CharField(max_length=20)

    class Meta:
        table_name = "sqt_rows"


@pytest.fixture
async def db():
    async with temporary_database(SqtRow) as database:
        yield database


async def test_an_outer_rollback_undoes_a_released_first_savepoint(db):
    with pytest.raises(RuntimeError):
        async with atomic():
            async with atomic():
                await SqtRow.objects.create(name="inner")
            raise RuntimeError("outer fails")
    assert await SqtRow.objects.count() == 0


async def test_an_outer_commit_keeps_the_savepoint_work(db):
    async with atomic():
        async with atomic():
            await SqtRow.objects.create(name="inner")
        await SqtRow.objects.create(name="outer")
    assert await SqtRow.objects.count() == 2


async def test_an_inner_rollback_keeps_the_outer_work(db):
    async with atomic():
        await SqtRow.objects.create(name="outer")
        with pytest.raises(RuntimeError):
            async with atomic():
                await SqtRow.objects.create(name="inner")
                raise RuntimeError("inner fails")
    assert [r.name for r in await SqtRow.objects.all()] == ["outer"]


async def test_overlapping_sessions_on_one_in_memory_connection_still_work(db):
    """The fix is scoped to savepoints: sessions sharing the single in-memory
    connection must not each send their own BEGIN."""
    parent = await SqtRow.objects.create(name="p")
    async with atomic():
        await SqtRow.objects.create(name="q")
        assert await SqtRow.objects.filter(name=parent.name).count() == 1
    assert await SqtRow.objects.count() == 2
