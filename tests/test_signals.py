"""Tests for zeeb_orm signals."""

from __future__ import annotations

import asyncio

import pytest

from zeeb_orm import (
    Model,
    Signal,
    configure,
    close_all_connections,
    fields,
    post_delete,
    post_save,
    pre_delete,
    pre_save,
    receiver,
    setup_database,
)
from zeeb_orm.conf.settings import Settings
from zeeb_orm.models.base import metadata


class Article(Model):
    """Test model for signal tests."""

    title = fields.CharField(max_length=200)

    class Meta:
        table_name = "signal_articles"


@pytest.fixture(autouse=True)
async def db_setup():
    Article._sa_table = None
    Article._sa_model = None
    metadata.clear()
    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")
    Article._get_table()
    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    Settings.reset()


@pytest.fixture(autouse=True)
def clear_signals():
    """Disconnect all test receivers after each test."""
    yield
    for sig in (pre_save, post_save, pre_delete, post_delete):
        sig._receivers.clear()


# ---------------------------------------------------------------------------
# Signal class unit tests
# ---------------------------------------------------------------------------


class TestSignalConnect:
    def test_connect_and_disconnect(self):
        sig = Signal()
        calls = []

        async def handler(sender, **kwargs):
            calls.append(kwargs)

        sig.connect(handler)
        assert len(sig._receivers) == 1

        disconnected = sig.disconnect(handler)
        assert disconnected is True
        assert len(sig._receivers) == 0

    def test_disconnect_returns_false_when_not_connected(self):
        sig = Signal()

        async def handler(sender, **kwargs):
            pass

        assert sig.disconnect(handler) is False

    def test_dispatch_uid_prevents_duplicates(self):
        sig = Signal()

        async def handler(sender, **kwargs):
            pass

        sig.connect(handler, dispatch_uid="my-uid")
        sig.connect(handler, dispatch_uid="my-uid")
        assert len(sig._receivers) == 1

    def test_sender_filtering(self):
        sig = Signal()
        calls = []

        async def handler(sender, **kwargs):
            calls.append(sender)

        sig.connect(handler, sender=Article)
        assert len(sig._live_receivers(Article)) == 1
        assert len(sig._live_receivers(str)) == 0


class TestSignalSend:
    @pytest.mark.asyncio
    async def test_send_calls_async_receiver(self):
        sig = Signal()
        received = []

        async def handler(sender, **kwargs):
            received.append((sender, kwargs))

        sig.connect(handler)
        results = await sig.send(sender=Article, value=42)
        assert len(results) == 1
        assert received[0] == (Article, {"value": 42})

    @pytest.mark.asyncio
    async def test_send_calls_sync_receiver(self):
        sig = Signal()
        received = []

        def handler(sender, **kwargs):
            received.append((sender, kwargs))

        sig.connect(handler)
        results = await sig.send(sender=Article, msg="hi")
        assert len(results) == 1
        assert received[0] == (Article, {"msg": "hi"})

    @pytest.mark.asyncio
    async def test_send_raises_on_receiver_exception(self):
        sig = Signal()

        async def bad_handler(sender, **kwargs):
            raise ValueError("boom")

        sig.connect(bad_handler)
        with pytest.raises(ValueError, match="boom"):
            await sig.send(sender=Article)

    @pytest.mark.asyncio
    async def test_send_robust_catches_exceptions(self):
        sig = Signal()

        async def bad(sender, **kwargs):
            raise RuntimeError("oops")

        sig.connect(bad)
        results = await sig.send_robust(sender=Article)
        assert len(results) == 1
        _fn, exc = results[0]
        assert isinstance(exc, RuntimeError)

    @pytest.mark.asyncio
    async def test_send_no_receivers(self):
        sig = Signal()
        results = await sig.send(sender=Article)
        assert results == []

    @pytest.mark.asyncio
    async def test_sender_filtering_in_send(self):
        sig = Signal()
        received = []

        async def handler(sender, **kwargs):
            received.append(sender)

        sig.connect(handler, sender=Article)
        await sig.send(sender=str)  # different sender — should NOT call handler
        assert received == []

        await sig.send(sender=Article)
        assert received == [Article]


# ---------------------------------------------------------------------------
# receiver() decorator
# ---------------------------------------------------------------------------


class TestReceiverDecorator:
    @pytest.mark.asyncio
    async def test_receiver_decorator_connects(self):
        sig = Signal()
        received = []

        @receiver(sig)
        async def handler(sender, **kwargs):
            received.append(True)

        await sig.send(sender=Article)
        assert received == [True]
        sig.disconnect(handler)

    @pytest.mark.asyncio
    async def test_receiver_decorator_with_sender(self):
        sig = Signal()
        received = []

        @receiver(sig, sender=Article)
        async def handler(sender, **kwargs):
            received.append(True)

        await sig.send(sender=Article)
        assert len(received) == 1
        await sig.send(sender=str)
        assert len(received) == 1  # not called for str sender
        sig.disconnect(handler)


# ---------------------------------------------------------------------------
# Model lifecycle hooks — pre_save / post_save
# ---------------------------------------------------------------------------


class TestModelSaveSignals:
    @pytest.mark.asyncio
    async def test_pre_save_fires_before_insert(self):
        order = []

        @receiver(pre_save, sender=Article)
        async def on_pre(sender, instance, created, **kwargs):
            order.append(("pre_save", created, instance.pk))

        @receiver(post_save, sender=Article)
        async def on_post(sender, instance, created, **kwargs):
            order.append(("post_save", created, instance.pk))

        art = Article(title="Hello")
        await art.save()

        assert order[0][0] == "pre_save"
        assert order[0][1] is True  # created=True for insert
        assert order[1][0] == "post_save"
        assert order[1][1] is True

    @pytest.mark.asyncio
    async def test_post_save_created_false_on_update(self):
        received = []

        @receiver(post_save, sender=Article)
        async def on_post(sender, instance, created, **kwargs):
            received.append(created)

        art = Article(title="A")
        await art.save()
        art.title = "B"
        await art.save()

        assert received[0] is True   # insert
        assert received[1] is False  # update

    @pytest.mark.asyncio
    async def test_pre_save_exception_aborts_save(self):
        @receiver(pre_save, sender=Article)
        async def on_pre(sender, **kwargs):
            raise ValueError("blocked by signal")

        art = Article(title="X")
        with pytest.raises(ValueError, match="blocked by signal"):
            await art.save()

        # Nothing was persisted
        count = await Article.objects.count()
        assert count == 0


# ---------------------------------------------------------------------------
# Model lifecycle hooks — pre_delete / post_delete
# ---------------------------------------------------------------------------


class TestModelDeleteSignals:
    @pytest.mark.asyncio
    async def test_pre_and_post_delete_fire(self):
        order = []

        @receiver(pre_delete, sender=Article)
        async def on_pre(sender, instance, **kwargs):
            order.append(("pre_delete", instance.pk))

        @receiver(post_delete, sender=Article)
        async def on_post(sender, instance, **kwargs):
            order.append(("post_delete", instance.pk))

        art = Article(title="ToDelete")
        await art.save()
        pk = art.pk
        await art.delete()

        assert order[0] == ("pre_delete", pk)
        assert order[1] == ("post_delete", pk)

    @pytest.mark.asyncio
    async def test_pre_delete_exception_aborts_delete(self):
        @receiver(pre_delete, sender=Article)
        async def on_pre(sender, **kwargs):
            raise RuntimeError("blocked")

        art = Article(title="Safe")
        await art.save()

        with pytest.raises(RuntimeError, match="blocked"):
            await art.delete()

        count = await Article.objects.count()
        assert count == 1

    @pytest.mark.asyncio
    async def test_post_delete_instance_still_has_pk(self):
        captured = []

        @receiver(post_delete, sender=Article)
        async def on_post(sender, instance, **kwargs):
            captured.append(instance.pk)

        art = Article(title="PK check")
        await art.save()
        pk = art.pk
        await art.delete()

        assert captured == [pk]


# ---------------------------------------------------------------------------
# QuerySet write paths
# ---------------------------------------------------------------------------


class TestQuerySetWriteSignals:
    """``objects.create()`` used to build its own INSERT and fire nothing.

    Django routes ``create()`` through ``obj.save(force_insert=True)``; the
    bulk paths deliberately stay signal-free there and here.
    """

    @pytest.mark.asyncio
    async def test_create_fires_save_signals(self):
        order = []

        @receiver(pre_save, sender=Article)
        async def on_pre(sender, instance, created, **kwargs):
            order.append(("pre_save", created))

        @receiver(post_save, sender=Article)
        async def on_post(sender, instance, created, **kwargs):
            order.append(("post_save", created, instance.pk))

        art = await Article.objects.create(title="Created")

        assert order[0] == ("pre_save", True)
        assert order[1] == ("post_save", True, art.pk)
        assert art.pk is not None

    @pytest.mark.asyncio
    async def test_pre_save_exception_aborts_create(self):
        @receiver(pre_save, sender=Article)
        async def on_pre(sender, instance, **kwargs):
            raise ValueError("nope")

        with pytest.raises(ValueError, match="nope"):
            await Article.objects.create(title="Blocked")

        assert await Article.objects.count() == 0

    @pytest.mark.asyncio
    async def test_get_or_create_fires_only_when_creating(self):
        created_flags = []

        @receiver(post_save, sender=Article)
        async def on_post(sender, instance, created, **kwargs):
            created_flags.append(created)

        _, created = await Article.objects.get_or_create(title="Once")
        assert created is True
        assert created_flags == [True]

        _, created = await Article.objects.get_or_create(title="Once")
        assert created is False
        assert created_flags == [True]  # the get branch writes nothing

    @pytest.mark.asyncio
    async def test_update_or_create_fires_on_both_branches(self):
        seen = []

        @receiver(post_save, sender=Article)
        async def on_post(sender, instance, created, **kwargs):
            seen.append(created)

        await Article.objects.update_or_create(title="Twice")
        await Article.objects.update_or_create(title="Twice", defaults={"title": "Twice"})
        assert seen == [True, False]

    @pytest.mark.asyncio
    async def test_bulk_create_stays_signal_free(self):
        # Django parity: bulk_create does not send pre_save/post_save.
        fired = []

        @receiver(post_save, sender=Article)
        async def on_post(sender, instance, **kwargs):
            fired.append(instance.pk)

        await Article.objects.bulk_create(
            [Article(title="a"), Article(title="b")]
        )
        assert fired == []
        assert await Article.objects.count() == 2

    @pytest.mark.asyncio
    async def test_queryset_update_stays_signal_free(self):
        fired = []

        @receiver(post_save, sender=Article)
        async def on_post(sender, instance, **kwargs):
            fired.append(instance.pk)

        await Article.objects.create(title="before")
        fired.clear()
        await Article.objects.filter(title="before").update(title="after")
        assert fired == []


class TestQuerySetDeleteSignals:
    """A leaf model took a fast single-statement DELETE that fired nothing.

    Django's ``Collector.can_fast_delete()`` refuses the fast path whenever a
    delete receiver is connected, so a registered receiver always runs.
    """

    @pytest.mark.asyncio
    async def test_queryset_delete_fires_when_a_receiver_is_connected(self):
        order = []

        @receiver(pre_delete, sender=Article)
        async def on_pre(sender, instance, **kwargs):
            order.append(("pre_delete", instance.pk))

        @receiver(post_delete, sender=Article)
        async def on_post(sender, instance, **kwargs):
            order.append(("post_delete", instance.pk))

        art = await Article.objects.create(title="Doomed")
        pk = art.pk

        deleted = await Article.objects.filter(title="Doomed").delete()

        assert deleted == 1
        assert order == [("pre_delete", pk), ("post_delete", pk)]
        assert await Article.objects.count() == 0

    @pytest.mark.asyncio
    async def test_pre_delete_exception_aborts_queryset_delete(self):
        @receiver(pre_delete, sender=Article)
        async def on_pre(sender, instance, **kwargs):
            raise RuntimeError("blocked")

        await Article.objects.create(title="Safe")

        with pytest.raises(RuntimeError, match="blocked"):
            await Article.objects.filter(title="Safe").delete()

        assert await Article.objects.count() == 1

    @pytest.mark.asyncio
    async def test_fast_path_is_kept_without_receivers(self):
        # No receivers -> one statement, no per-instance fetch.
        await Article.objects.create(title="Gone")
        assert await Article.objects.filter(title="Gone").delete() == 1
        assert await Article.objects.count() == 0

    @pytest.mark.asyncio
    async def test_has_listeners_reports_per_sender(self):
        assert pre_delete.has_listeners(Article) is False

        @receiver(pre_delete, sender=Article)
        async def on_pre(sender, instance, **kwargs):
            pass

        assert pre_delete.has_listeners(Article) is True
        assert pre_delete.has_listeners(str) is False
