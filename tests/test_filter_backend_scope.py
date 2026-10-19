"""A filter backend that scopes rows scopes every route, not only the list.

``filter_queryset()`` used to run in ``ListModelMixin.list`` alone. With the
documented ``OwnedByCurrentUser`` backend, ``GET /articles`` showed the
caller's rows while ``POST /articles/query`` returned everyone's, and
``GET``/``PATCH``/``DELETE /articles/{id}`` acted on another user's row —
``get_object()`` skipped the backends.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.filters import BaseFilter, OrderingFilter
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, fields
from zeeb_orm.testing import temporary_database


class ScopedNote(Model):
    id = fields.AutoField()
    owner = fields.CharField(max_length=20)
    title = fields.CharField(max_length=50)

    class Meta:
        table_name = "scoped_notes"


class ScopedNoteSerializer(ModelSerializer):
    class Meta:
        model = ScopedNote
        fields = ["id", "owner", "title"]


class OwnedByCaller(BaseFilter):
    """Rows owned by the caller named in the X-User header (a stand-in for auth)."""

    def filter_queryset(self, request, queryset, view):
        return queryset.filter(owner=request.headers.get("X-User", ""))


class NoteViewSet(ModelViewSet):
    queryset = ScopedNote.objects
    serializer_class = ScopedNoteSerializer
    filter_backends = [OwnedByCaller, OrderingFilter]
    ordering = ["-title"]


@pytest.fixture
async def notes():
    async with temporary_database(ScopedNote):
        ann = await ScopedNote.objects.create(owner="ann", title="a1")
        await ScopedNote.objects.create(owner="ann", title="a2")
        bob = await ScopedNote.objects.create(owner="bob", title="b1")
        yield ann, bob


def _client(user: str) -> AsyncClient:
    router = SimpleRouter()
    router.register("notes", NoteViewSet)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-User": user}
    )


async def test_list_and_query_agree(notes):
    async with _client("ann") as client:
        listed = await client.get("/notes")
        queried = await client.post("/notes/query", json={})
    assert sorted(n["title"] for n in listed.json()) == ["a1", "a2"]
    assert queried.json()["count"] == 2
    assert sorted(n["title"] for n in queried.json()["results"]) == ["a1", "a2"]


async def test_query_cannot_reach_another_callers_rows(notes):
    _, bob = notes
    async with _client("ann") as client:
        response = await client.post("/notes/query", json={"filter": f"Q(id={bob.id})"})
    assert response.status_code == 200
    assert response.json()["count"] == 0


async def test_query_order_by_replaces_the_backend_ordering(notes):
    async with _client("ann") as client:
        default = await client.post("/notes/query", json={})
        explicit = await client.post("/notes/query", json={"order_by": ["title"]})
    assert [n["title"] for n in default.json()["results"]] == ["a2", "a1"]
    assert [n["title"] for n in explicit.json()["results"]] == ["a1", "a2"]


@pytest.mark.parametrize(
    ("method", "body"), [("GET", None), ("PATCH", {"title": "pwned"}), ("DELETE", None)]
)
async def test_detail_routes_cannot_reach_another_callers_row(notes, method, body):
    _, bob = notes
    async with _client("ann") as client:
        response = await client.request(method, f"/notes/{bob.id}", json=body)
    assert response.status_code == 404
    row = await ScopedNote.objects.get(id=bob.id)
    assert row.title == "b1"


async def test_detail_routes_still_serve_the_callers_own_row(notes):
    ann, _ = notes
    async with _client("ann") as client:
        fetched = await client.get(f"/notes/{ann.id}")
        patched = await client.patch(f"/notes/{ann.id}", json={"title": "renamed"})
    assert fetched.status_code == 200
    assert patched.status_code == 200
    assert (await ScopedNote.objects.get(id=ann.id)).title == "renamed"
