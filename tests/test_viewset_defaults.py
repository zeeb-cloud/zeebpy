"""Project-wide viewset defaults, and a cursor order that is actually monotonic.

There was no ``DEFAULT_PAGINATION_CLASS`` / ``DEFAULT_PERMISSION_CLASSES`` /
``DEFAULT_FILTER_BACKENDS``: every viewset had to repeat them, and one that
forgot ``pagination_class`` returned its whole table. The settings default to
the old behaviour (no paginator, no permission checks, no filters); an
explicit class attribute — ``None``/``[]`` included — always wins.

``CursorPagination`` ordered by ``-id``: with the default UUID primary key that
is a random order, so pages skipped and repeated rows. It now follows
``created_at`` when the model has it, an integer key otherwise, and refuses a
model with neither.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.conf import settings
from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.exceptions import ImproperlyConfigured
from zeeb_api.pagination import CursorPagination
from zeeb_api.permissions import IsAuthenticated
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, close_all_connections, configure, fields, setup_database

DEFAULTS = ("DEFAULT_PAGINATION_CLASS", "DEFAULT_PERMISSION_CLASSES", "DEFAULT_FILTER_BACKENDS")


@pytest.fixture(autouse=True)
def restore_defaults():
    saved = {name: getattr(settings, name) for name in DEFAULTS}
    yield
    for name, value in saved.items():
        setattr(settings, name, value)


class DefNote(Model):
    title = fields.CharField(max_length=50)
    created_at = fields.DateTimeField(auto_now_add=True)

    class Meta:
        table_name = "def_notes"


class BareNote(Model):
    title = fields.CharField(max_length=50)

    class Meta:
        table_name = "bare_notes"


class IntNote(Model):
    id = fields.AutoField()
    title = fields.CharField(max_length=50)

    class Meta:
        table_name = "int_notes"


class DefNoteSerializer(ModelSerializer):
    class Meta:
        model = DefNote
        fields = ["id", "title"]


class Plain(ModelViewSet):
    queryset = DefNote.objects
    serializer_class = DefNoteSerializer


class NoPagination(Plain):
    pagination_class = None


class OpenDoor(Plain):
    permission_classes = []


MODELS = (DefNote, BareNote, IntNote)


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


async def _get(viewset, path="/notes", **params):
    router = SimpleRouter()
    router.register("notes", viewset)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path, params=params)


async def _seed(n=3):
    for i in range(n):
        await DefNote.objects.create(title=f"note-{i}")


async def test_no_settings_keeps_the_old_behaviour(db):
    await _seed()
    response = await _get(Plain)
    assert response.status_code == 200
    assert isinstance(response.json(), list) and len(response.json()) == 3


async def test_default_pagination_class_applies(db):
    await _seed()
    settings.DEFAULT_PAGINATION_CLASS = "zeeb_api.pagination.LimitOffsetPagination"
    paged = await _get(Plain, limit=2)
    unpaged = await _get(NoPagination)
    assert paged.json()["count"] == 3
    assert len(paged.json()["results"]) == 2
    assert isinstance(unpaged.json(), list)


async def test_default_permission_classes_apply(db):
    settings.DEFAULT_PERMISSION_CLASSES = ["zeeb_api.permissions.IsAuthenticated"]
    assert (await _get(Plain)).status_code == 401
    assert (await _get(OpenDoor)).status_code == 200
    assert [type(p) for p in Plain().get_permissions()] == [IsAuthenticated]


async def test_default_filter_backends_apply(db):
    await _seed()
    settings.DEFAULT_FILTER_BACKENDS = ["zeeb_api.filters.OrderingFilter"]
    response = await _get(Plain, ordering="-title")
    assert [n["title"] for n in response.json()] == ["note-2", "note-1", "note-0"]


# --------------------------------------------------------------------------- #
# CursorPagination ordering
# --------------------------------------------------------------------------- #


def test_cursor_orders_by_created_at_when_present():
    assert CursorPagination().get_ordering(DefNote.objects.all()) == "-created_at"


def test_cursor_orders_by_an_integer_key():
    assert CursorPagination().get_ordering(IntNote.objects.all()) == "-id"


def test_cursor_refuses_a_random_uuid_order():
    with pytest.raises(ImproperlyConfigured, match="ordering"):
        CursorPagination().get_ordering(BareNote.objects.all())


def test_cursor_explicit_ordering_wins():
    class ByTitle(CursorPagination):
        ordering = "title"

    assert ByTitle().get_ordering(BareNote.objects.all()) == "title"


async def test_cursor_pages_follow_creation_order(db):
    await _seed(5)

    class Cursor(Plain):
        pagination_class = type("TwoPerPage", (CursorPagination,), {"page_size": 2})

    seen: list[str] = []
    response = await _get(Cursor)
    for _ in range(10):  # bounded: a cursor that never advances must fail, not hang
        assert response.status_code == 200, response.text
        body = response.json()
        seen += [n["title"] for n in body["results"]]
        if not body["next"]:
            break
        from urllib.parse import parse_qs, urlparse

        cursor = parse_qs(urlparse(body["next"]).query)["cursor"][0]
        response = await _get(Cursor, cursor=cursor)
    assert seen == ["note-4", "note-3", "note-2", "note-1", "note-0"]


async def test_page_number_pagination_serves_pages(db):
    """The paginators called QuerySet.limit(), which does not exist: every
    paginated GET was a 500. They slice the queryset now."""
    from zeeb_api.pagination import PageNumberPagination

    await _seed(3)

    class Paged(Plain):
        pagination_class = type("TwoPerPage", (PageNumberPagination,), {"page_size": 2})

    first = await _get(Paged)
    second = await _get(Paged, page=2)
    assert first.status_code == 200, first.text
    assert first.json()["count"] == 3
    assert len(first.json()["results"]) == 2
    assert len(second.json()["results"]) == 1
