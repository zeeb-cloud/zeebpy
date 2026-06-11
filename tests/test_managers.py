"""Tests for custom managers: Manager.from_queryset() and QuerySet.as_manager()."""

import pytest

from zeeb_orm import (
    Manager,
    Model,
    QuerySet,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# Custom QuerySets / Managers


class ArticleQuerySet(QuerySet):
    def published(self):
        return self.filter(published=True)

    def by_views(self):
        return self.order_by("-views")


class PublishedManager(Manager.from_queryset(ArticleQuerySet, "BasePublishedManager")):
    """Manager subclass overriding get_queryset()."""

    def get_queryset(self):
        return super().get_queryset().filter(published=True)


class NoteQuerySet(QuerySet):
    def pinned(self):
        return self.filter(pinned=True)


# Test Models


class MgrArticle(Model):
    title = fields.CharField(max_length=200)
    published = fields.BooleanField(default=False)
    views = fields.IntegerField(default=0)

    objects = Manager.from_queryset(ArticleQuerySet)()
    published_objects = PublishedManager()

    class Meta:
        table_name = "mgr_articles"


class MgrNote(Model):
    body = fields.CharField(max_length=200)
    pinned = fields.BooleanField(default=False)

    objects = NoteQuerySet.as_manager()

    class Meta:
        table_name = "mgr_notes"


MODELS = (MgrArticle, MgrNote)


@pytest.fixture
async def db():
    """Set up test database (pattern from tests/test_related_lookups.py)."""
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    Settings.reset()

    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    db = await setup_database("sqlite+aiosqlite:///:memory:")

    for model in MODELS:
        model._get_table()

    await db.create_all()
    yield db
    await db.drop_all()
    await close_all_connections()
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


@pytest.fixture
async def seeded(db):
    await MgrArticle.objects.create(title="Draft", published=False, views=10)
    await MgrArticle.objects.create(title="Hot", published=True, views=100)
    await MgrArticle.objects.create(title="Cold", published=True, views=1)
    await MgrNote.objects.create(body="todo", pinned=True)
    await MgrNote.objects.create(body="later", pinned=False)


class TestFromQueryset:
    @pytest.mark.asyncio
    async def test_custom_method_on_manager(self, seeded):
        articles = await MgrArticle.objects.published()
        assert {a.title for a in articles} == {"Hot", "Cold"}

    @pytest.mark.asyncio
    async def test_chainable_after_filter(self, seeded):
        qs = MgrArticle.objects.filter(views__gt=5)
        # filter() must preserve the custom QuerySet subclass
        assert isinstance(qs, ArticleQuerySet)
        articles = await qs.published()
        assert {a.title for a in articles} == {"Hot"}

    @pytest.mark.asyncio
    async def test_custom_methods_chain_together(self, seeded):
        articles = await MgrArticle.objects.published().by_views()
        assert [a.title for a in articles] == ["Hot", "Cold"]

    @pytest.mark.asyncio
    async def test_get_queryset_returns_custom_class(self, db):
        assert isinstance(MgrArticle.objects.get_queryset(), ArticleQuerySet)

    @pytest.mark.asyncio
    async def test_built_with_queryset_recorded(self, db):
        assert MgrArticle.objects._built_with_queryset is ArticleQuerySet
        manager_cls = Manager.from_queryset(ArticleQuerySet, "NamedManager")
        assert manager_cls.__name__ == "NamedManager"
        assert manager_cls._built_with_queryset is ArticleQuerySet

    @pytest.mark.asyncio
    async def test_manager_methods_not_overridden(self, db):
        # Methods already defined on Manager (filter, all, ...) must NOT be
        # replaced by queryset proxies; only the custom ones are copied.
        manager_cls = type(MgrArticle.objects)
        own_attrs = set()
        for cls in manager_cls.__mro__:
            if cls is Manager:
                break
            own_attrs.update(cls.__dict__)
        assert "published" in own_attrs
        assert "by_views" in own_attrs
        assert "filter" not in own_attrs
        assert "all" not in own_attrs

    @pytest.mark.asyncio
    async def test_zero_arg_constructible(self, db):
        manager = type(MgrArticle.objects)()
        assert manager.model is None  # unbound until descriptor access


class TestGetQuerysetOverride:
    @pytest.mark.asyncio
    async def test_override_applies_to_proxied_methods(self, seeded):
        articles = await MgrArticle.published_objects.by_views()
        # Draft is filtered out by the overridden get_queryset()
        assert [a.title for a in articles] == ["Hot", "Cold"]

    @pytest.mark.asyncio
    async def test_override_applies_to_standard_methods(self, seeded):
        assert await MgrArticle.published_objects.count() == 2
        articles = await MgrArticle.published_objects.all()
        assert {a.title for a in articles} == {"Hot", "Cold"}


class TestManagerRebinding:
    @pytest.mark.asyncio
    async def test_rebinding_on_model_access(self, db):
        m1 = MgrArticle.objects
        m2 = MgrArticle.objects
        assert m1 is not m2  # fresh manager per access (descriptor rebinding)
        assert m1.model is MgrArticle
        assert m2.model is MgrArticle
        assert type(m1) is type(m2)
        assert isinstance(m1, Manager)

    @pytest.mark.asyncio
    async def test_rebound_manager_keeps_custom_methods(self, seeded):
        manager = MgrArticle.objects
        articles = await manager.published()
        assert {a.title for a in articles} == {"Hot", "Cold"}


class TestAsManager:
    @pytest.mark.asyncio
    async def test_queryset_has_as_manager(self):
        assert hasattr(QuerySet, "as_manager")

    @pytest.mark.asyncio
    async def test_as_manager_custom_method(self, seeded):
        notes = await MgrNote.objects.pinned()
        assert [n.body for n in notes] == ["todo"]

    @pytest.mark.asyncio
    async def test_as_manager_chainable(self, seeded):
        qs = MgrNote.objects.filter(body__contains="o")
        assert isinstance(qs, NoteQuerySet)
        notes = await qs.pinned()
        assert [n.body for n in notes] == ["todo"]

    @pytest.mark.asyncio
    async def test_as_manager_standard_methods(self, seeded):
        assert await MgrNote.objects.count() == 2
        note = await MgrNote.objects.get(body="later")
        assert note.pinned is False
