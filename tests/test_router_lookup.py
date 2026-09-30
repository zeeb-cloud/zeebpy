"""Router detail routes honour lookup_field's type and lookup_url_kwarg.

The detail path parameter was always typed with the primary key's type, so a
``lookup_field = "slug"`` viewset on a UUID-keyed model answered 422 for every
slug. ``lookup_url_kwarg`` was ignored when building the route, while
``get_object()`` read the value back under that name - every detail request
404'd. Registering after ``.routes`` had been read did not invalidate the
cached routes, and a failing ``get_serializer_class()`` was swallowed at route
build time without a trace.
"""

import logging

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.routers.default import DefaultRouter, SimpleRouter, served_routes
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, close_all_connections, configure, fields, setup_database


class SlugItem(Model):
    slug = fields.SlugField(max_length=50, unique=True)
    title = fields.CharField(max_length=100)
    rank = fields.IntegerField(default=0)

    class Meta:
        table_name = "slug_items"


class SlugItemSerializer(ModelSerializer):
    class Meta:
        model = SlugItem
        fields = ["id", "slug", "title", "rank"]


class BySlug(ModelViewSet):
    queryset = SlugItem.objects
    serializer_class = SlugItemSerializer
    lookup_field = "slug"


class BySlugKwarg(BySlug):
    lookup_url_kwarg = "item_slug"


class ByRank(BySlug):
    lookup_field = "rank"


MODELS = (SlugItem,)


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
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    for model in MODELS:
        model._get_table()
    await database.create_all()
    yield database
    await database.drop_all()
    await close_all_connections()
    for model in MODELS:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


def _app(viewset) -> FastAPI:
    router = SimpleRouter()
    router.register("items", viewset)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_slug_lookup_on_uuid_keyed_model(db):
    await SlugItem.objects.create(slug="hello-world", title="Hello")
    async with _client(_app(BySlug)) as client:
        found = await client.get("/items/hello-world")
        patched = await client.patch("/items/hello-world", json={"title": "Hi"})
        missing = await client.get("/items/nope")
    assert found.status_code == 200, found.text
    assert found.json()["title"] == "Hello"
    assert patched.status_code == 200, patched.text
    assert missing.status_code == 404


async def test_lookup_url_kwarg_names_the_path_parameter(db):
    await SlugItem.objects.create(slug="kw", title="Keyword")
    app = _app(BySlugKwarg)
    async with _client(app) as client:
        found = await client.get("/items/kw")
        deleted = await client.delete("/items/kw")
    assert found.status_code == 200, found.text
    assert deleted.status_code == 204, deleted.text
    paths = app.openapi()["paths"]
    assert "/items/{item_slug}" in paths


async def test_lookup_type_follows_the_lookup_field(db):
    await SlugItem.objects.create(slug="ranked", title="R", rank=7)
    app = _app(ByRank)
    async with _client(app) as client:
        found = await client.get("/items/7")
        bad = await client.get("/items/seven")
    assert found.status_code == 200, found.text
    assert bad.status_code == 422
    params = app.openapi()["paths"]["/items/{rank}"]["get"]["parameters"]
    assert params[0]["schema"]["type"] == "integer"


def test_registering_after_routes_were_read_is_visible():
    router = DefaultRouter()
    router.register("first", BySlug)
    before = router.routes
    router.register("second", BySlugKwarg)
    after = router.routes
    assert after is not before
    paths = {r.path for api_router in after for r in served_routes(api_router.routes)}
    assert "/second/{item_slug}" in paths


def test_failing_serializer_discovery_is_logged_not_swallowed(caplog):
    class PerUser(BySlug):
        def get_serializer_class(self):
            # Needs the request, which does not exist while routes are built.
            return self.request.state.serializer

    with caplog.at_level(logging.WARNING, logger="zeeb_api.routers.default"):
        router = SimpleRouter()
        router.register("per-user", PerUser)
        router.get_urls()
    messages = [r.getMessage() for r in caplog.records]
    assert any("PerUser.get_serializer_class()" in m for m in messages), messages


def test_broken_serializer_in_builtin_discovery_raises():
    class Broken(BySlug):
        serializer_class = property(lambda self: 1 / 0)  # type: ignore[assignment]

    router = SimpleRouter()
    router.register("broken", Broken)
    with pytest.raises(ZeroDivisionError):
        router.get_urls()


def test_a_viewset_without_a_model_keeps_uuid_or_its_annotation():
    """No model to read the lookup field from: the detail method's annotation
    decides, and without one the historical UUID default holds — a model-less
    viewset keyed by ``project_id`` must not start receiving plain strings."""
    import uuid

    from zeeb_api.viewsets import ViewSet

    class Plain(ViewSet):
        lookup_field = "project_id"

        async def retrieve(self, request, project_id):
            return {"id": str(project_id)}

    class Annotated(ViewSet):
        lookup_field = "name"

        async def retrieve(self, request, name: str):
            return {"name": name}

    router = SimpleRouter()
    assert router._get_lookup_type(Plain) is uuid.UUID
    assert router._get_lookup_type(Annotated) is str
