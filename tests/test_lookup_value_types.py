"""Lookup values are converted to their column's type, or refused with a 400.

Filter values from a client are JSON or text. Before, the outcome depended on
the driver:

- on SQLite, ``Q(id='<uuid>')`` — the default primary key — failed in
  SQLAlchemy's ``Uuid`` bind processor: a 500 from ``POST /query``, and a
  foreign key to such a model could not be filtered by its id at all;
- on PostgreSQL (asyncpg), ``Q(views='3')``, ``Q(active=1)``, ``Q(title=5)``,
  ``Q(score='1.5')``... were refused at execute time: a 500;
- everywhere, ``Q(created__gte='garbage')`` raised ``ValueError``: a 500.

Run with ``ZEEB_TEST_DATABASE_URL`` pointing at PostgreSQL to cover asyncpg.
"""

import decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.routers.default import SimpleRouter
from zeeb_api.serializers import ModelSerializer
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, fields
from zeeb_orm.exceptions import ValidationError
from zeeb_orm.testing import temporary_database


class TypedAuthor(Model):
    name = fields.CharField(max_length=50)

    class Meta:
        table_name = "typed_authors"


class TypedItem(Model):
    title = fields.CharField(max_length=50)
    views = fields.IntegerField(default=0)
    score = fields.FloatField(default=0)
    price = fields.DecimalField(max_digits=8, decimal_places=2, default=decimal.Decimal("0"))
    active = fields.BooleanField(default=True)
    created = fields.DateTimeField(auto_now_add=True)
    author = fields.ForeignKey(TypedAuthor, on_delete="CASCADE", related_name="items")

    class Meta:
        table_name = "typed_items"


class TypedItemSerializer(ModelSerializer):
    class Meta:
        model = TypedItem
        fields = ["id", "title", "views", "score", "price", "active", "created", "author"]


class TypedItemViewSet(ModelViewSet):
    queryset = TypedItem.objects
    serializer_class = TypedItemSerializer


@pytest.fixture
async def db():
    async with temporary_database(TypedAuthor, TypedItem) as database:
        yield database


@pytest.fixture
async def item(db):
    author = await TypedAuthor.objects.create(name="ann")
    return await TypedItem.objects.create(
        title="5",
        views=3,
        score=1.5,
        price=decimal.Decimal("9.99"),
        active=True,
        author_id=author.id,
    )


def _client() -> AsyncClient:
    router = SimpleRouter()
    router.register("items", TypedItemViewSet)
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    )


# --------------------------------------------------------------------------- #
# The ORM
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "lookups",
    [
        {"views": "3"},
        {"views__gte": " 2 "},
        {"views__in": ["3", 4]},
        {"views__range": ["1", "9"]},
        {"views__lt": "3.5"},
        {"score": "1.5"},
        {"price": "9.99"},
        {"active": 1},
        {"active": "true"},
        {"active": "YES"},
        {"title": 5},
        {"title__in": [5, "x"]},
        {"created__gte": "2020-01-01"},
        {"created__year__gte": "2020"},
    ],
)
async def test_strings_and_numbers_are_converted(item, lookups):
    assert await TypedItem.objects.filter(**lookups).count() == 1


@pytest.mark.parametrize(
    ("lookups", "expected"),
    [
        # views == 3. A fractional bound is rounded so it admits the same
        # integers on every database (asyncpg would truncate it to 3).
        ({"views__gt": "2.5"}, 1),
        ({"views__gt": 3.5}, 0),
        ({"views__gte": "3.5"}, 0),
        ({"views__gte": decimal.Decimal("2.5")}, 1),
        ({"views__lt": 3.5}, 1),
        ({"views__lt": "2.5"}, 0),
        ({"views__lte": "3.5"}, 1),
        ({"views__lte": "2.5"}, 0),
        ({"views__gt": "-0.5"}, 1),
        ({"views__range": ["2.5", "3.5"]}, 1),
        ({"views__range": [3.2, 3.8]}, 0),
        ({"views": "3.0"}, 1),
        ({"views": "3e0"}, 1),
    ],
)
async def test_fractional_bounds_on_an_integer_column_are_rounded(item, lookups, expected):
    assert await TypedItem.objects.filter(**lookups).count() == expected


async def test_uuid_strings_match_the_primary_and_foreign_key(item):
    assert await TypedItem.objects.filter(id=str(item.id)).count() == 1
    assert await TypedItem.objects.filter(id__in=[str(item.id)]).count() == 1
    assert await TypedItem.objects.filter(author=str(item.author_id)).count() == 1
    assert await TypedItem.objects.filter(author_id=str(item.author_id)).count() == 1


@pytest.mark.parametrize(
    ("lookups", "message"),
    [
        ({"views": "abc"}, "'abc' is not a valid integer."),
        ({"views__in": ["1", "two"]}, "'two' is not a valid integer."),
        ({"views": "3.5"}, "'3.5' is not a valid integer."),
        ({"views": 3.5}, "3.5 is not a valid integer."),
        ({"views__in": [3, 3.5]}, "3.5 is not a valid integer."),
        ({"views__gt": "nan"}, "'nan' is not a valid integer."),
        ({"views__lt": float("inf")}, "inf is not a valid integer."),
        ({"views__gte": "1e999999999"}, "'1e999999999' is not a valid integer."),
        ({"score": "x"}, "'x' is not a valid number."),
        ({"price__gt": "cheap"}, "'cheap' is not a valid number."),
        ({"active": "nope"}, "'nope' is not a valid boolean."),
        ({"active": 2}, "2 is not a valid boolean."),
        ({"title": True}, "True is not a valid string."),
        ({"id": "not-a-uuid"}, "'not-a-uuid' is not a valid UUID."),
        ({"id": 5}, "5 is not a valid UUID."),
        ({"created__gte": "garbage"}, "'garbage' is not a valid date, time or datetime."),
    ],
)
async def test_unconvertible_values_are_a_validation_error(item, lookups, message):
    with pytest.raises(ValidationError) as excinfo:
        await TypedItem.objects.filter(**lookups).count()
    (key,) = lookups
    assert excinfo.value.message_dict == {key: [message]}


async def test_none_and_expressions_pass_through(item):
    from zeeb_orm import F

    assert await TypedItem.objects.filter(title=None).count() == 0
    assert await TypedItem.objects.filter(views__lte=F("views")).count() == 1
    assert await TypedItem.objects.filter(title__icontains=5).count() == 1


# --------------------------------------------------------------------------- #
# POST /query
# --------------------------------------------------------------------------- #


async def test_query_converts_client_values(item):
    expr = (
        f"Q(id='{item.id}') & Q(author='{item.author_id}') & Q(views__gte='2') "
        "& Q(views__gt=-1) & Q(active=1) & Q(title=5) & Q(created__gte='2020-01-01')"
    )
    async with _client() as client:
        response = await client.post("/items/query", json={"filter": expr})
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1


@pytest.mark.parametrize(
    "expr",
    [
        "Q(views='abc')",
        "Q(score='x')",
        "Q(active='nope')",
        "Q(id='not-a-uuid')",
        "Q(id=5)",
        "Q(created__gte='garbage')",
    ],
)
async def test_query_refuses_unconvertible_values_with_a_400(item, expr):
    async with _client() as client:
        response = await client.post("/items/query", json={"filter": expr})
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "is not a valid" in error["details"][0]["message"]
