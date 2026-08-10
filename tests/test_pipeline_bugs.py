"""Pipeline correctness fixes: B4 (get_object errors), B5 (object-perm 401 vs
403), B7 (cursor typing), B8 (query field allow-list), B9 (create defaults)."""

import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.permissions.base import BasePermission
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


class Widget(Model):
    name = fields.CharField(max_length=100)
    # A column the API does NOT expose — must not be filterable/orderable.
    secret = fields.CharField(max_length=100, null=True)
    status = fields.CharField(max_length=20, default="active")

    class Meta:
        table_name = "pb_widgets"


MODELS = (Widget,)


class WidgetSerializer(ModelSerializer):
    class Meta:
        model = Widget
        fields = ["id", "name", "status"]  # 'secret' intentionally excluded


class WidgetViewSet(ModelViewSet):
    queryset = Widget.objects
    serializer_class = WidgetSerializer
    lookup_field = "id"


class DenyObjectPermission(BasePermission):
    """Passes the collection check, denies every object-level check."""

    async def has_permission(self, request, view):
        return True

    async def has_object_permission(self, request, view, obj):
        return False


class GuardedWidgetViewSet(WidgetViewSet):
    permission_classes = [DenyObjectPermission]


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
    for name in [m._meta.db_table for m in MODELS]:
        table = metadata.tables.get(name)
        if table is not None:
            metadata.remove(table)
    for model in MODELS:
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


class _FakeUser:
    def __init__(self, uid="u1"):
        self.id = uid
        self.is_authenticated = True


def _client(router: SimpleRouter, authenticated: bool = False) -> AsyncClient:
    app = FastAPI()
    install_exception_handlers(app)

    @app.middleware("http")
    async def _inject_user(request, call_next):
        request.state.user = _FakeUser() if authenticated else None
        return await call_next(request)

    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# B5: object-level permission denial → 401 for anonymous, 403 for authenticated
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_object_permission_denial_is_401_for_anonymous(db):
    widget = await Widget.objects.create(name="w")
    router = SimpleRouter()
    router.register("widgets", GuardedWidgetViewSet)

    async with _client(router, authenticated=False) as client:
        resp = await client.get(f"/widgets/{widget.id}")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_object_permission_denial_is_403_for_authenticated(db):
    widget = await Widget.objects.create(name="w")
    router = SimpleRouter()
    router.register("widgets", GuardedWidgetViewSet)

    async with _client(router, authenticated=True) as client:
        resp = await client.get(f"/widgets/{widget.id}")
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------------- #
# B8: query endpoint restricts filter/order_by to serializer-exposed fields
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_query_allows_exposed_fields(db):
    await Widget.objects.create(name="alpha")
    router = SimpleRouter()
    router.register("widgets", WidgetViewSet)

    async with _client(router, authenticated=True) as client:
        resp = await client.post(
            "/widgets/query",
            json={"filter": "Q(name__icontains='alp')", "order_by": ["name"]},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 1


@pytest.mark.asyncio
async def test_query_rejects_unexposed_filter_field(db):
    router = SimpleRouter()
    router.register("widgets", WidgetViewSet)

    async with _client(router, authenticated=True) as client:
        resp = await client.post(
            "/widgets/query", json={"filter": "Q(secret__icontains='x')"}
        )
    assert resp.status_code == 400, resp.text
    assert "secret" in resp.text


@pytest.mark.asyncio
async def test_query_rejects_unexposed_order_field(db):
    router = SimpleRouter()
    router.register("widgets", WidgetViewSet)

    async with _client(router, authenticated=True) as client:
        resp = await client.post("/widgets/query", json={"order_by": ["-secret"]})
    assert resp.status_code == 400, resp.text
    assert "secret" in resp.text


# --------------------------------------------------------------------------- #
# B9: create does not overwrite a model default with an explicit None
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_omitted_field_uses_model_default(db):
    router = SimpleRouter()
    router.register("widgets", WidgetViewSet)

    async with _client(router, authenticated=True) as client:
        resp = await client.post("/widgets", json={"name": "w"})
    assert resp.status_code == 201, resp.text
    row = await Widget.objects.get(name="w")
    # status was omitted; the model default must apply, not an explicit None.
    assert row.status == "active"


def test_create_validated_data_excludes_omitted_fields(db):
    ser = WidgetSerializer(data={"name": "w"})
    assert ser.is_valid(), ser.errors
    # 'status' was not provided and must not appear as an explicit None.
    assert "status" not in ser.validated_data


# --------------------------------------------------------------------------- #
# B7: cursor pagination round-trips typed values
# --------------------------------------------------------------------------- #


def test_cursor_encode_decode_preserves_type():
    from zeeb_api.pagination import CursorPagination

    p = CursorPagination()
    cases = [
        123,
        3.5,
        "abc",
        True,
        datetime.datetime(2020, 1, 2, 3, 4, 5),
        datetime.date(2021, 6, 7),
    ]
    for value in cases:
        decoded = p._decode_cursor(p._encode_cursor(value))
        assert decoded == value
        assert type(decoded) is type(value)


# --------------------------------------------------------------------------- #
# B4: get_object distinguishes not-found (404), bad value (400), db error (500)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_object_not_found_is_404(db):
    import uuid

    from zeeb_api.exceptions import NotFound

    vs = WidgetViewSet()
    vs.kwargs = {"id": uuid.uuid4()}
    vs.request = None
    with pytest.raises(NotFound):
        await vs.get_object()


@pytest.mark.asyncio
async def test_get_object_does_not_mask_db_error(db):
    """A real error from the queryset must propagate, not become a 404."""
    from zeeb_api.exceptions import NotFound

    class _BoomQS:
        def filter(self, **kwargs):
            raise RuntimeError("db is down")

    vs = WidgetViewSet()
    vs.kwargs = {"id": "whatever"}
    vs.request = None
    vs.get_queryset = lambda: _BoomQS()

    with pytest.raises(RuntimeError):
        await vs.get_object()
    # And specifically NOT swallowed into a NotFound.
    try:
        await vs.get_object()
    except NotFound:
        pytest.fail("DB error was masked as NotFound (404)")
    except RuntimeError:
        pass
