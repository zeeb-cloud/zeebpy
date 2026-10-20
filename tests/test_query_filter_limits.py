"""A crafted ``/query`` filter is a 400 or 422, never a stalled worker or a 500.

The parser had no bound on its input and checked the tree recursively:

- a 2 MB ``Q(id__in=[1, 1, ...])`` kept the event loop busy for tens of
  seconds (parsed twice), with no size limit anywhere in front of it;
- ``~~~...Q(a=1)`` or a long ``Q(...) | Q(...) | ...`` chain raised
  ``RecursionError`` in ``_validate_ast`` — a 500;
- ``Q(title__in='x')``, ``Q(id__range=[0])`` and ``order_by=["--id"]`` reached
  the ORM and failed there — 500s too.

None of them was an injection: values are bound parameters and names resolve
against the model. These tests pin the fixes, including the classic
injection payloads, which stay inert.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.filters import OrderingFilter
from zeeb_api.query import QFilterError, check_field_path, parse_q_filter, parse_q_filter_with_paths
from zeeb_api.query.parser import DEFAULT_MAX_LENGTH, MAX_FILTER_DEPTH
from zeeb_api.query.paths import FieldPathError
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, close_all_connections, configure, fields, setup_database


class LimitAuthor(Model):
    id = fields.AutoField()
    name = fields.CharField(max_length=50)
    password = fields.CharField(max_length=128)

    class Meta:
        table_name = "limit_authors"


class LimitPost(Model):
    id = fields.AutoField()
    title = fields.CharField(max_length=100)
    views = fields.IntegerField(default=0)
    author = fields.ForeignKey(LimitAuthor, on_delete="CASCADE", related_name="posts")

    class Meta:
        table_name = "limit_posts"


class LimitPostSerializer(ModelSerializer):
    class Meta:
        model = LimitPost
        fields = ["id", "title", "views", "author"]


class ScopedPostViewSet(ModelViewSet):
    """Scoped in get_queryset: the caller sees the posts whose title starts with 'a'."""

    serializer_class = LimitPostSerializer
    filter_backends = [OrderingFilter]

    def get_queryset(self):
        return LimitPost.objects.filter(title__startswith="a")


MODELS = (LimitAuthor, LimitPost)


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
    router.register("posts", ScopedPostViewSet)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


async def _seed() -> None:
    alice = await LimitAuthor.objects.create(name="alice", password="$2b$secret")
    bob = await LimitAuthor.objects.create(name="bob", password="$2b$other")
    await LimitPost.objects.create(title="a-post", views=1, author_id=alice.id)
    await LimitPost.objects.create(title="a%post", views=2, author_id=alice.id)
    await LimitPost.objects.create(title="b-post", views=3, author_id=bob.id)


def _titles(response) -> list[str]:
    return sorted(item["title"] for item in response.json()["results"])


# --------------------------------------------------------------------------- #
# The parser
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "expr",
    [
        "Q(age=9**9**9**9)",
        "Q(age=1<<10**9)",
        "Q(name='a'*10**9)",
        "Q(title=Q)",
        "Q(title=[Q(id=1)])",
        "Q(id=[1])",
        "Q(id__in=[[1]])",
        "Q(title__in='abc')",
        "Q(id__range=[0])",
        "Q(id__range=[0, 1, 2])",
        "Q(id__range=5)",
        "Q(_negated=True, title='x')",
        "Q(_connector='OR', title='x')",
        "Q(title='x\x00')",
    ],
)
def test_malformed_filters_are_a_qfiltererror(expr):
    with pytest.raises(QFilterError):
        parse_q_filter(expr)


def test_an_oversized_filter_is_refused_before_it_is_parsed():
    expr = "Q(id__in=[" + ",".join(["1"] * 1_000_000) + "])"
    with pytest.raises(QFilterError, match="longer than"):
        parse_q_filter(expr)


def test_deep_nesting_is_a_qfiltererror_not_a_recursionerror():
    deepest = "~" * (MAX_FILTER_DEPTH - 1) + "Q(a=1)"
    parse_q_filter(deepest)
    with pytest.raises(QFilterError, match="nested deeper"):
        parse_q_filter("~" + deepest)
    with pytest.raises(QFilterError, match="nested deeper"):
        parse_q_filter("~(" * MAX_FILTER_DEPTH + "Q(a=1)" + ")" * MAX_FILTER_DEPTH)


def test_a_long_or_chain_is_one_level_and_one_flat_q():
    q, paths = parse_q_filter_with_paths(" | ".join(f"Q(title='t{i}')" for i in range(200)))
    assert paths == {"title"}
    assert len(q.children) == 200


def test_sequence_lookups_take_their_lists():
    q, paths = parse_q_filter_with_paths("Q(id__in=[1, 2]) & Q(views__range=(0, 5))")
    assert paths == {"id__in", "views__range"}
    assert [child.children for child in q.children] == [
        [("id__in", [1, 2])],
        [("views__range", (0, 5))],
    ]


def test_ordering_strips_one_sign_only():
    check_field_path(None, "-title", ["title"])
    with pytest.raises(FieldPathError):
        check_field_path(None, "--title", ["title"])


# --------------------------------------------------------------------------- #
# POST /query
# --------------------------------------------------------------------------- #


async def test_an_oversized_filter_body_is_a_422(db):
    await _seed()
    expr = "Q(id__in=[" + ",".join(["1"] * 5000) + "])"
    async with _client() as client:
        response = await client.post("/posts/query", json={"filter": expr})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_surrounding_whitespace_does_not_count_against_the_limit(db):
    await _seed()
    expr = "Q(title__in=[" + ",".join(["'x'"] * 1000) + "])"
    assert len(expr) <= DEFAULT_MAX_LENGTH < len(expr) + 200
    async with _client() as client:
        response = await client.post("/posts/query", json={"filter": f"\n{expr}" + " " * 200})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "body",
    [
        {"filter": "Q(title__in='abc')"},
        {"filter": "Q(id__range=[0])"},
        {"filter": "Q(id=[1])"},
        {"filter": "~" * 40 + "Q(id=1)"},
        {"order_by": ["--id"]},
    ],
)
async def test_malformed_requests_are_a_400(db, body):
    await _seed()
    async with _client() as client:
        response = await client.post("/posts/query", json=body)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_the_deepest_accepted_filter_runs(db):
    """MAX_FILTER_DEPTH is shallow enough for the ORM and SQLAlchemy to compile."""
    await _seed()
    levels = (MAX_FILTER_DEPTH - 1) // 3  # each level is ~, | and ~; +1 for the innermost Q
    expr = "Q(views__gte=0)"
    for _ in range(levels):
        expr = f"~(~({expr}) | Q(views=99))"
    async with _client() as client:
        response = await client.post("/posts/query", json={"filter": expr})
    assert response.status_code == 200, response.text
    assert _titles(response) == ["a%post", "a-post"]


async def test_the_longest_or_chain_within_the_limit_runs(db):
    """As one flat OR: folded pairwise it nested a level per term, and the
    ORM's WHERE builder recursed through every one."""
    await _seed()
    expr = "|".join(["Q(title='a-post')"] * (4096 // len("Q(title='a-post')|")))
    async with _client() as client:
        response = await client.post("/posts/query", json={"filter": expr})
    assert response.status_code == 200, response.text
    assert _titles(response) == ["a-post"]


@pytest.mark.parametrize(
    "expr",
    [
        "Q(title=\"x' OR '1'='1\")",
        "Q(title=\"' OR 1=1 --\")",
        "Q(title=\"a-post'; DROP TABLE limit_posts; --\")",
        "Q(title__icontains=\"%' OR '1'='1\")",
        "Q(title__startswith='_')",
    ],
)
async def test_injection_payloads_are_bound_values(db, expr):
    await _seed()
    async with _client() as client:
        response = await client.post("/posts/query", json={"filter": expr})
        assert response.status_code == 200, response.text
        assert response.json()["results"] == []
    assert await LimitPost.objects.count() == 3


@pytest.mark.parametrize(
    "expr",
    ["~Q(title='nonexistent')", "Q(views__gte=0) | ~Q(views__gte=0)", "Q(title='b-post') | Q()"],
)
async def test_tautologies_stay_inside_the_scoped_queryset(db, expr):
    await _seed()
    async with _client() as client:
        response = await client.post("/posts/query", json={"filter": expr})
    assert response.status_code == 200, response.text
    assert "b-post" not in _titles(response)


@pytest.mark.parametrize(
    "expr",
    [
        "1=1",
        "Q(id=1) or 1==1",
        "Q(title='x'); DROP TABLE limit_posts; --",
        # Against a typed column the string is not even sent: it is no integer
        "Q(id='1 OR 1=1')",
        "Q(views__gte='0 OR 1=1')",
    ],
)
async def test_sql_shaped_filters_are_a_400(db, expr):
    await _seed()
    async with _client() as client:
        response = await client.post("/posts/query", json={"filter": expr})
    assert response.status_code == 400
    assert await LimitPost.objects.count() == 3


@pytest.mark.parametrize(
    "term", ["id; DROP TABLE limit_posts", "(SELECT 1)", "id desc", "--id", "?", "1"]
)
async def test_ordering_payloads(db, term):
    """Refused in /query (400), dropped by ?ordering= (default order, 200)."""
    await _seed()
    async with _client() as client:
        response = await client.post("/posts/query", json={"order_by": [term]})
        assert response.status_code == 400
        response = await client.get("/posts", params={"ordering": term})
        assert response.status_code == 200
    assert await LimitPost.objects.count() == 3


@pytest.mark.parametrize(
    ("expr", "value"),
    [("Q(views=-1)", -1), ("Q(views__gt=-1.5)", -1.5), ("Q(views__in=[-1, 2])", [-1, 2])],
)
def test_negative_numbers_are_literals(expr, value):
    q = parse_q_filter(expr)
    assert q.children[0][1] == value


@pytest.mark.parametrize("expr", ["-Q(views=1)", "Q(views=--1)", "Q(views=-True)", "Q(views=-'x')"])
def test_minus_applies_to_numbers_only(expr):
    with pytest.raises(QFilterError):
        parse_q_filter(expr)
