"""perform_create / perform_update hooks: both save styles work, no double-save.

A ViewSet hook may inject server-owned values (e.g. the authenticated user) in
one of two ways:

  (a) mutate ``serializer.validated_data`` and let the framework save, or
  (b) call ``await serializer.save(...)`` itself.

Style (b) is the reflex a DRF-trained agent reaches for. It used to double-save:
the create mixin unconditionally called ``serializer.save()`` again after the
hook, so a NOT NULL column set only via a ``save(**kwargs)`` in the hook was
dropped on the second insert ("<field> cannot be null") and an orphan row was
left behind. These tests pin that both styles now persist exactly one row, that
FK kwargs are normalized like request input, and that the authenticated user can
be auto-assigned from ``request.state.user``.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import (
    Model,
    close_all_connections,
    configure,
    fields,
    setup_database,
)

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class HookUser(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "hook_users"


class HookArticle(Model):
    title = fields.CharField(max_length=100)
    # NOT NULL FK: the double-insert bug surfaced precisely as "author cannot be
    # null" on the framework's second save.
    author = fields.ForeignKey(HookUser, related_name="articles")
    note = fields.CharField(max_length=100, null=True)

    class Meta:
        table_name = "hook_articles"


MODELS = (HookUser, HookArticle)

# Counts calls into the DB-level update, to prove a hook that calls save() does
# not cause a second UPDATE.
_update_calls = {"n": 0}


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #


class AutoAssignSerializer(ModelSerializer):
    """``author`` is not a request field — it is injected server-side."""

    class Meta:
        model = HookArticle
        fields = ["id", "title", "note"]


class ExplicitSerializer(ModelSerializer):
    """``author`` is part of the request payload (as ``author_id``)."""

    class Meta:
        model = HookArticle
        fields = ["id", "title", "author_id", "note"]

    async def update(self, instance, validated_data):
        _update_calls["n"] += 1
        return await super().update(instance, validated_data)


# --------------------------------------------------------------------------- #
# ViewSets
# --------------------------------------------------------------------------- #


class MutateCreateViewSet(ModelViewSet):
    queryset = HookArticle.objects
    serializer_class = AutoAssignSerializer
    lookup_field = "id"

    async def perform_create(self, serializer):
        serializer.validated_data["author_id"] = self.request.state.user.id


class SaveCreateViewSet(ModelViewSet):
    queryset = HookArticle.objects
    serializer_class = AutoAssignSerializer
    lookup_field = "id"

    async def perform_create(self, serializer):
        # DRF reflex: save from the hook. Must not double-insert.
        await serializer.save(author_id=self.request.state.user.id)


class MutateUpdateViewSet(ModelViewSet):
    queryset = HookArticle.objects
    serializer_class = ExplicitSerializer
    lookup_field = "id"

    async def perform_update(self, serializer):
        serializer.validated_data["note"] = "mutated-by-hook"


class SaveUpdateViewSet(ModelViewSet):
    queryset = HookArticle.objects
    serializer_class = ExplicitSerializer
    lookup_field = "id"

    async def perform_update(self, serializer):
        await serializer.save(note="saved-by-hook")


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
async def db():
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    _update_calls["n"] = 0
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
    for name in [m._meta.db_table for m in MODELS]:
        table = metadata.tables.get(name)
        if table is not None:
            metadata.remove(table)
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


class _FakeUser:
    def __init__(self, user_id):
        self.id = user_id
        self.is_authenticated = True


def _client(router: SimpleRouter, user_id=None) -> AsyncClient:
    app = FastAPI()
    install_exception_handlers(app)
    if user_id is not None:

        @app.middleware("http")
        async def _inject_user(request, call_next):
            request.state.user = _FakeUser(user_id)
            return await call_next(request)

    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# Serializer-level unit behavior
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_save_sets_instance_and_saved_flag(db):
    user = await HookUser.objects.create(name="Ada")
    ser = AutoAssignSerializer(data={"title": "X"})
    assert ser.is_valid(), ser.errors
    assert ser._saved is False

    instance = await ser.save(author_id=user.id)

    assert ser._saved is True
    assert ser.instance is instance
    assert instance.id is not None
    assert await HookArticle.objects.count() == 1


@pytest.mark.asyncio
async def test_second_save_updates_same_row(db):
    user = await HookUser.objects.create(name="Ada")
    ser = AutoAssignSerializer(data={"title": "X"})
    assert ser.is_valid(), ser.errors

    first = await ser.save(author_id=user.id)
    second = await ser.save()  # no kwargs — must not insert a duplicate

    assert await HookArticle.objects.count() == 1
    assert second.id == first.id


@pytest.mark.asyncio
async def test_save_normalizes_fk_kwargs(db):
    seed = await HookUser.objects.create(name="Seed")
    target = await HookUser.objects.create(name="Target")

    # bare name + model instance
    s1 = ExplicitSerializer(data={"title": "A", "author_id": str(seed.id)})
    assert s1.is_valid(), s1.errors
    i1 = await s1.save(author=target)
    assert i1.author_id == target.id

    # bare name + primary key
    s2 = ExplicitSerializer(data={"title": "B", "author_id": str(seed.id)})
    assert s2.is_valid(), s2.errors
    i2 = await s2.save(author=target.id)
    assert i2.author_id == target.id

    # canonical name + model instance
    s3 = ExplicitSerializer(data={"title": "C", "author_id": str(seed.id)})
    assert s3.is_valid(), s3.errors
    i3 = await s3.save(author_id=target)
    assert i3.author_id == target.id


# --------------------------------------------------------------------------- #
# perform_create over HTTP — both styles, exactly one row
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_perform_create_mutate_validated_data(db):
    user = await HookUser.objects.create(name="Ada")
    router = SimpleRouter()
    router.register("articles", MutateCreateViewSet)

    async with _client(router, user_id=user.id) as client:
        resp = await client.post("/articles", json={"title": "Hello"})

    assert resp.status_code == 201, resp.text
    assert await HookArticle.objects.count() == 1
    row = await HookArticle.objects.get(title="Hello")
    assert row.author_id == user.id


@pytest.mark.asyncio
async def test_perform_create_save_in_hook_no_double_insert(db):
    """The regression: a save() in the hook must not trigger a second insert."""
    user = await HookUser.objects.create(name="Ada")
    router = SimpleRouter()
    router.register("articles", SaveCreateViewSet)

    async with _client(router, user_id=user.id) as client:
        resp = await client.post("/articles", json={"title": "Hello"})

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Hello"
    assert body["id"] is not None  # response serializes the saved instance
    # Exactly one row, and it has the auto-assigned author (no orphan, no null).
    assert await HookArticle.objects.count() == 1
    row = await HookArticle.objects.get(title="Hello")
    assert row.author_id == user.id


# --------------------------------------------------------------------------- #
# perform_update over HTTP — both styles, single UPDATE
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_perform_update_mutate_validated_data(db):
    user = await HookUser.objects.create(name="Ada")
    article = await HookArticle.objects.create(title="Draft", author=user)
    router = SimpleRouter()
    router.register("articles", MutateUpdateViewSet)

    async with _client(router) as client:
        resp = await client.put(
            f"/articles/{article.id}",
            json={"title": "Final", "author_id": str(user.id)},
        )

    assert resp.status_code == 200, resp.text
    refreshed = await HookArticle.objects.get(id=article.id)
    assert refreshed.title == "Final"
    assert refreshed.note == "mutated-by-hook"
    assert _update_calls["n"] == 1


@pytest.mark.asyncio
async def test_perform_update_save_in_hook_single_update(db):
    user = await HookUser.objects.create(name="Ada")
    article = await HookArticle.objects.create(title="Draft", author=user)
    router = SimpleRouter()
    router.register("articles", SaveUpdateViewSet)

    async with _client(router) as client:
        resp = await client.put(
            f"/articles/{article.id}",
            json={"title": "Final", "author_id": str(user.id)},
        )

    assert resp.status_code == 200, resp.text
    refreshed = await HookArticle.objects.get(id=article.id)
    assert refreshed.title == "Final"
    assert refreshed.note == "saved-by-hook"
    # Exactly one UPDATE despite the hook saving and the mixin's skip logic.
    assert _update_calls["n"] == 1


@pytest.mark.asyncio
async def test_partial_update_invokes_perform_update(db):
    user = await HookUser.objects.create(name="Ada")
    article = await HookArticle.objects.create(title="Draft", author=user)
    router = SimpleRouter()
    router.register("articles", MutateUpdateViewSet)

    async with _client(router) as client:
        resp = await client.patch(f"/articles/{article.id}", json={"title": "Patched"})

    assert resp.status_code == 200, resp.text
    refreshed = await HookArticle.objects.get(id=article.id)
    assert refreshed.title == "Patched"
    assert refreshed.note == "mutated-by-hook"  # hook ran on partial_update too
