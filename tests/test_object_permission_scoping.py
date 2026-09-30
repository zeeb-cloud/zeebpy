"""``use_object_permissions`` scopes every action, not only the CRUD six.

``GenericViewSet._action_permission_map`` knew list/retrieve/create/update/
partial_update/destroy and nothing else, so for ``POST /query`` and every
custom ``@action`` the permission type came back ``None`` and the row-level
scoping was skipped: a client that could list only the public rows could read
all of them through ``/query``, and a detail action reached objects its caller
may neither read nor change. These tests pin the repaired mapping.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet, action
from zeeb_orm import Model, close_all_connections, configure, fields, setup_database
from zeeb_orm.permissions import Rule


class ScopedNote(Model):
    title = fields.CharField(max_length=100)
    is_public = fields.BooleanField(default=False)
    is_locked = fields.BooleanField(default=False)

    read_permission = Rule.Q(is_public=True)
    change_permission = Rule.Q(is_locked=False)
    delete_permission = Rule.Q(is_locked=False)

    class Meta:
        table_name = "scoped_notes"


class ScopedNoteSerializer(ModelSerializer):
    class Meta:
        model = ScopedNote
        fields = ["id", "title", "is_public", "is_locked"]


class ScopedNoteViewSet(ModelViewSet):
    queryset = ScopedNote.objects
    serializer_class = ScopedNoteSerializer
    use_object_permissions = True

    @action(detail=False, methods=["get"])
    async def everything(self, request):
        """A list-type action: must see only what the caller may read."""
        rows = await self.get_queryset()
        return await self.get_serializer(instance=rows, many=True).adata()

    @action(detail=True, methods=["get"])
    async def peek(self, request, pk=None):
        """A read-only detail action."""
        return await self.get_serializer(instance=await self.get_object()).adata()

    @action(detail=True, methods=["post"])
    async def rename(self, request, pk=None):
        """A writing detail action: needs change permission on the object."""
        note = await self.get_object()
        note.title = "renamed"
        await note.save()
        return {"title": note.title}

    @action(detail=True, methods=["post"], permission_type="read")
    async def bookmark(self, request, pk=None):
        """A POST that only reads — declared as such."""
        note = await self.get_object()
        return {"bookmarked": str(note.id)}


MODELS = (ScopedNote,)


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


def _client() -> AsyncClient:
    router = SimpleRouter()
    router.register("notes", ScopedNoteViewSet)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed():
    public = await ScopedNote.objects.create(title="public", is_public=True)
    private = await ScopedNote.objects.create(title="private", is_public=False)
    locked = await ScopedNote.objects.create(title="locked", is_public=True, is_locked=True)
    return public, private, locked


async def test_query_is_scoped_like_list(db):
    await _seed()
    async with _client() as client:
        listed = await client.get("/notes")
        queried = await client.post("/notes/query", json={})

    assert listed.status_code == 200, listed.text
    assert queried.status_code == 200, queried.text
    listed_titles = sorted(n["title"] for n in listed.json())
    queried_titles = sorted(n["title"] for n in queried.json()["results"])
    assert queried_titles == listed_titles == ["locked", "public"]
    assert queried.json()["count"] == 2


async def test_query_filter_cannot_reach_unreadable_rows(db):
    await _seed()
    async with _client() as client:
        response = await client.post("/notes/query", json={"filter": "Q(title='private')"})
    assert response.status_code == 200, response.text
    assert response.json()["results"] == []
    assert response.json()["count"] == 0


async def test_list_type_custom_action_is_scoped(db):
    await _seed()
    async with _client() as client:
        response = await client.get("/notes/everything")
    assert response.status_code == 200, response.text
    assert sorted(n["title"] for n in response.json()) == ["locked", "public"]


async def test_read_detail_action_hides_unreadable_object(db):
    public, private, _ = await _seed()
    async with _client() as client:
        ok = await client.get(f"/notes/{public.id}/peek")
        hidden = await client.get(f"/notes/{private.id}/peek")
    assert ok.status_code == 200, ok.text
    assert hidden.status_code == 404, hidden.text


async def test_writing_detail_action_needs_change_permission(db):
    public, _, locked = await _seed()
    async with _client() as client:
        allowed = await client.post(f"/notes/{public.id}/rename")
        refused = await client.post(f"/notes/{locked.id}/rename")
    assert allowed.status_code == 200, allowed.text
    assert refused.status_code in (403, 404), refused.text
    reloaded = await ScopedNote.objects.get(id=locked.id)
    assert reloaded.title == "locked"


async def test_action_can_declare_its_permission_type(db):
    _, private, locked = await _seed()
    async with _client() as client:
        # A read-typed POST reaches a readable-but-locked object...
        readable = await client.post(f"/notes/{locked.id}/bookmark")
        # ...and still cannot reach an unreadable one.
        unreadable = await client.post(f"/notes/{private.id}/bookmark")
    assert readable.status_code == 200, readable.text
    assert unreadable.status_code == 404, unreadable.text


def test_permission_type_mapping_covers_query_and_actions():
    from starlette.requests import Request

    def view_for(action_name: str, method: str):
        scope = {"type": "http", "method": method, "path": "/", "headers": []}
        view = ScopedNoteViewSet(request=Request(scope))
        view.action = action_name
        return view

    assert view_for("query", "POST")._get_permission_type_for_action() == "read"
    assert view_for("everything", "GET")._get_permission_type_for_action() == "read"
    assert view_for("rename", "POST")._get_permission_type_for_action() == "change"
    assert view_for("bookmark", "POST")._get_permission_type_for_action() == "read"
    # An action name nothing declares falls back to the request method, never None.
    assert view_for("unknown", "DELETE")._get_permission_type_for_action() == "delete"
    assert view_for("unknown", "PATCH")._get_permission_type_for_action() == "change"
    assert view_for("unknown", "GET")._get_permission_type_for_action() == "read"
