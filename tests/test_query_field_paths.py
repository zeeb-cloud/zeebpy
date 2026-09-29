"""Filter and ordering allow-lists check the whole field path.

The ``/query`` allow-list and ``OrderingFilter`` compared only the first
segment of a path, so wherever ``author`` was exposed a client could filter by
``author__password__startswith='$2b$'`` or order by ``author__password`` — a
blind oracle over every column of the related model. ``regex`` lookups were
allowed too; on SQLite they run Python's ``re`` inside the database.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.filters import OrderingFilter
from zeeb_api.query import FieldPathError, check_field_path, extract_q_paths
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, close_all_connections, configure, fields, setup_database


class PathAuthor(Model):
    # An integer key: /query filter values are JSON literals, and the ORM does
    # not coerce a UUID string for a foreign-key comparison.
    id = fields.AutoField()
    name = fields.CharField(max_length=50)
    password = fields.CharField(max_length=128)

    class Meta:
        table_name = "path_authors"


class PathPost(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(PathAuthor, on_delete="CASCADE", related_name="posts")

    class Meta:
        table_name = "path_posts"


class PathPostSerializer(ModelSerializer):
    class Meta:
        model = PathPost
        fields = ["id", "title", "author"]


class PostViewSet(ModelViewSet):
    queryset = PathPost.objects
    serializer_class = PathPostSerializer
    filter_backends = [OrderingFilter]


class ListedPathViewSet(PostViewSet):
    query_fields = ["id", "title", "author", "author__name"]
    ordering_fields = ["title", "author__name"]


class RegexViewSet(PostViewSet):
    query_allow_regex = True


class AllOrderingViewSet(PostViewSet):
    ordering_fields = "__all__"


MODELS = (PathAuthor, PathPost)


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


def _client(viewset) -> AsyncClient:
    router = SimpleRouter()
    router.register("posts", viewset)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed():
    alice = await PathAuthor.objects.create(name="alice", password="$2b$zzz")
    bob = await PathAuthor.objects.create(name="bob", password="$2b$aaa")
    await PathPost.objects.create(title="a-post", author_id=alice.id)
    await PathPost.objects.create(title="b-post", author_id=bob.id)
    return alice, bob


# --------------------------------------------------------------------------- #
# POST /query
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "expr",
    [
        "Q(author__password__startswith='$2b$')",
        "Q(title='x') | Q(author__password='$2b$zzz')",
        "~Q(author__name='alice')",
        "Q(Q(author__password__gt='$'))",
        "Q(posts__title='x')",  # not a field of PathPost at all
    ],
)
async def test_query_refuses_paths_through_relations(db, expr):
    await _seed()
    async with _client(PostViewSet) as client:
        response = await client.post("/posts/query", json={"filter": expr})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"][0]["field"] == "filter"


async def test_query_order_by_refuses_related_columns(db):
    await _seed()
    async with _client(PostViewSet) as client:
        response = await client.post("/posts/query", json={"order_by": ["author__password"]})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["details"][0]["field"] == "order_by"


async def test_query_still_filters_by_exposed_fields_and_the_relation_itself(db):
    alice, _ = await _seed()
    async with _client(PostViewSet) as client:
        for expr in (
            "Q(title__icontains='a-')",
            f"Q(author={alice.id})",
            f"Q(author_id={alice.id})",
            f"Q(author__id={alice.id})",
            f"Q(author__in=[{alice.id}])",
        ):
            response = await client.post("/posts/query", json={"filter": expr})
            assert response.status_code == 200, (expr, response.text)
            assert [p["title"] for p in response.json()["results"]] == ["a-post"], expr


async def test_listed_relation_path_is_allowed(db):
    await _seed()
    async with _client(ListedPathViewSet) as client:
        allowed = await client.post(
            "/posts/query",
            json={"filter": "Q(author__name='bob')", "order_by": ["author__name"]},
        )
        refused = await client.post(
            "/posts/query", json={"filter": "Q(author__password__startswith='$')"}
        )
    assert allowed.status_code == 200, allowed.text
    assert [p["title"] for p in allowed.json()["results"]] == ["b-post"]
    assert refused.status_code == 400


async def test_regex_lookups_are_refused_unless_allowed(db):
    await _seed()
    body = {"filter": "Q(title__regex='^a')"}
    async with _client(PostViewSet) as client:
        refused = await client.post("/posts/query", json=body)
        irefused = await client.post("/posts/query", json={"filter": "Q(title__iregex='^A')"})
    async with _client(RegexViewSet) as client:
        allowed = await client.post("/posts/query", json=body)
    assert refused.status_code == 400, refused.text
    assert irefused.status_code == 400, irefused.text
    assert allowed.status_code == 200, allowed.text
    assert [p["title"] for p in allowed.json()["results"]] == ["a-post"]


# --------------------------------------------------------------------------- #
# ?ordering=
# --------------------------------------------------------------------------- #


async def _titles(viewset, ordering: str) -> list[str]:
    async with _client(viewset) as client:
        response = await client.get("/posts", params={"ordering": ordering})
    assert response.status_code == 200, response.text
    return [p["title"] for p in response.json()]


async def test_ordering_ignores_related_columns(db):
    await _seed()
    # alice's hash sorts after bob's, so a working oracle would put b-post first.
    assert await _titles(PostViewSet, "author__password") == await _titles(PostViewSet, "")
    assert await _titles(PostViewSet, "-title") == ["b-post", "a-post"]


async def test_ordering_listed_path_and_all(db):
    await _seed()
    assert await _titles(ListedPathViewSet, "-author__name") == ["b-post", "a-post"]
    # "__all__" admits the model's own fields, not a path through a relation.
    assert await _titles(AllOrderingViewSet, "-title") == ["b-post", "a-post"]
    unordered = await _titles(AllOrderingViewSet, "")
    assert await _titles(AllOrderingViewSet, "-author__name") == unordered


# --------------------------------------------------------------------------- #
# Unit level
# --------------------------------------------------------------------------- #


def test_extract_q_paths_returns_full_paths():
    expr = "Q(author__name__icontains='x') | ~Q(Q(title='y'))"
    assert extract_q_paths(expr) == {"author__name__icontains", "title"}


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("title", {"title"}),
        ("-title", {"title"}),
        ("title__icontains", {"title"}),
        ("author", {"author"}),
        ("author_id__in", {"author"}),
        ("author__pk", {"author"}),
        ("author__name", {"author__name"}),
        ("author__name__startswith", {"author__name"}),
        ("title", "__all__"),
    ],
)
def test_check_field_path_allows(path, allowed):
    check_field_path(PathPost, path, allowed)


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("secret", {"title"}),
        ("author__name", {"author"}),
        ("author__password__startswith", {"author", "author__name"}),
        ("author__name", "__all__"),
    ],
)
def test_check_field_path_refuses(path, allowed):
    with pytest.raises(FieldPathError):
        check_field_path(PathPost, path, allowed)


def test_check_field_path_regex_rule_applies_even_unrestricted():
    with pytest.raises(FieldPathError):
        check_field_path(PathPost, "title__regex", None, allow_regex=False)
    check_field_path(PathPost, "title__regex", None, allow_regex=True)
