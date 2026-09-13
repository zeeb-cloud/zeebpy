"""GET collection works for both viewset ``queryset`` declaration styles.

Regression tests for the 2026-07-11 production 500: generated viewsets declare
``queryset = Model.objects`` (a Manager, which is not awaitable) while the
unpaginated list path awaited the class attribute directly ->
``TypeError: 'Manager' object can't be awaited``. ``get_queryset()`` now
normalizes via ``.all()``, which also pins the two sibling failure modes:

* ``queryset = Model.objects.all()`` (a QuerySet) 500'd with AttributeError
  before the 2026-07-10 audit fix (F8) because the list path called the then
  non-existent ``QuerySet.all()``, and
* a shared class-level QuerySet cached its results after the first request
  and served stale rows to every later request.
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
# Model / serializer / both viewset declaration styles
# --------------------------------------------------------------------------- #


class NormItem(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "norm_items"


class NormItemSerializer(ModelSerializer):
    class Meta:
        model = NormItem
        fields = "__all__"


class ManagerAttrViewSet(ModelViewSet):
    """The form the code generator emits: a bare Manager."""

    queryset = NormItem.objects
    serializer_class = NormItemSerializer
    lookup_field = "id"


class QuerySetAttrViewSet(ModelViewSet):
    """A class-level QuerySet; must keep working and stay fresh per request."""

    queryset = NormItem.objects.all()
    serializer_class = NormItemSerializer
    lookup_field = "id"


MODELS = (NormItem,)


@pytest.fixture
async def db():
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
    for name in [m._meta.db_table for m in MODELS]:
        table = metadata.tables.get(name)
        if table is not None:
            metadata.remove(table)
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


def _client(router: SimpleRouter) -> AsyncClient:
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# GET collection with both declaration styles
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("viewset", [ManagerAttrViewSet, QuerySetAttrViewSet])
async def test_get_collection_returns_200(db, viewset):
    await NormItem.objects.create(name="one")

    router = SimpleRouter()
    router.register("items", viewset)
    async with _client(router) as client:
        response = await client.get("/items")

    assert response.status_code == 200, response.text
    assert [item["name"] for item in response.json()] == ["one"]


@pytest.mark.parametrize("viewset", [ManagerAttrViewSet, QuerySetAttrViewSet])
async def test_get_collection_is_fresh_per_request(db, viewset):
    router = SimpleRouter()
    router.register("items", viewset)
    async with _client(router) as client:
        first = await client.get("/items")
        assert first.status_code == 200, first.text
        assert first.json() == []

        await NormItem.objects.create(name="created-later")

        second = await client.get("/items")
        assert second.status_code == 200, second.text
        assert [item["name"] for item in second.json()] == ["created-later"]


# --------------------------------------------------------------------------- #
# Unit level
# --------------------------------------------------------------------------- #


def test_queryset_all_returns_fresh_uncached_copy():
    qs = NormItem.objects.filter(name="x")
    qs._result_cache = ["sentinel"]

    copy = qs.all()

    assert copy is not qs
    assert copy._filters == qs._filters
    assert copy._result_cache is None


def test_get_queryset_normalizes_manager_to_awaitable():
    viewset = ManagerAttrViewSet()

    queryset = viewset.get_queryset()

    assert queryset is not ManagerAttrViewSet.queryset
    assert hasattr(queryset, "__await__")


def test_get_queryset_clones_class_level_queryset():
    viewset = QuerySetAttrViewSet()

    queryset = viewset.get_queryset()

    assert queryset is not QuerySetAttrViewSet.queryset
    assert hasattr(queryset, "__await__")
