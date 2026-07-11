"""Foreign keys accept the bare relationship name on write.

Generated CRUD request schemas require a FK as ``<name>_id`` (e.g. ``author_id``)
while responses expose the bare ``<name>``. Clients — especially coding agents —
routinely echo a GET response straight back into a POST/PUT/PATCH and used to hit
a spurious ``FIELD_REQUIRED`` for ``<name>_id``. We now accept the bare name as an
alias for the canonical ``<name>_id`` (canonical still wins when both are sent),
surface a hint when a required field looks like a near-miss, and keep OpenAPI
canonical (``<name>_id`` only). These tests pin all of that end to end.
"""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import (
    _field_required_hint,
    _parse_pydantic_errors,
    install_exception_handlers,
)
from zeeb_api.exceptions import ErrorCode
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
# Models / serializers / viewset
# --------------------------------------------------------------------------- #


class FKAuthor(Model):
    name = fields.CharField(max_length=100)

    class Meta:
        table_name = "fkalias_authors"


class FKArticle(Model):
    title = fields.CharField(max_length=100)
    author = fields.ForeignKey(FKAuthor, related_name="articles")

    class Meta:
        table_name = "fkalias_articles"


class ArticleSerializer(ModelSerializer):
    class Meta:
        model = FKArticle
        fields = "__all__"


class ArticleIdSpellingSerializer(ModelSerializer):
    """Same model, but the FK is spelled ``author_id`` in Meta.fields."""

    class Meta:
        model = FKArticle
        fields = ["id", "title", "author_id"]


class ArticleViewSet(ModelViewSet):
    queryset = FKArticle.objects
    serializer_class = ArticleSerializer
    lookup_field = "id"


MODELS = (FKAuthor, FKArticle)


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
# Request schema (create / PUT)
# --------------------------------------------------------------------------- #


def test_request_schema_accepts_bare_fk_name():
    uid = uuid.uuid4()
    obj = ArticleSerializer.RequestSchema.model_validate(
        {"title": "Hi", "author": str(uid)}
    )
    assert obj.model_dump() == {"title": "Hi", "author_id": uid}


def test_request_schema_accepts_canonical_name():
    uid = uuid.uuid4()
    obj = ArticleSerializer.RequestSchema.model_validate(
        {"title": "Hi", "author_id": str(uid)}
    )
    assert obj.model_dump()["author_id"] == uid


def test_canonical_wins_when_both_present():
    bare, canonical = uuid.uuid4(), uuid.uuid4()
    obj = ArticleSerializer.RequestSchema.model_validate(
        {"title": "Hi", "author": str(bare), "author_id": str(canonical)}
    )
    assert obj.model_dump()["author_id"] == canonical


def test_openapi_request_schema_is_canonical_only():
    # Both alias-rendered and field-name-rendered schemas must publish the
    # canonical "<name>_id" only, never the bare alias.
    for by_alias in (True, False):
        props = ArticleSerializer.RequestSchema.model_json_schema(by_alias=by_alias)[
            "properties"
        ]
        assert "author_id" in props
        assert "author" not in props


def test_missing_fk_reports_canonical_loc():
    with pytest.raises(Exception) as exc:
        ArticleSerializer.RequestSchema.model_validate({"title": "Hi"})
    locs = [e["loc"] for e in exc.value.errors()]
    assert ("author_id",) in locs


def test_meta_fields_id_spelling_behaves_identically():
    uid = uuid.uuid4()
    # bare name still accepted even when Meta.fields spelled it "author_id"
    obj = ArticleIdSpellingSerializer.RequestSchema.model_validate(
        {"title": "Hi", "author": str(uid)}
    )
    assert obj.model_dump()["author_id"] == uid
    props = ArticleIdSpellingSerializer.RequestSchema.model_json_schema()["properties"]
    assert "author_id" in props and "author" not in props


# --------------------------------------------------------------------------- #
# Partial request schema (PATCH)
# --------------------------------------------------------------------------- #


def test_partial_request_schema_all_optional_and_canonical():
    schema = ArticleSerializer.PartialRequestSchema.model_json_schema()
    assert not schema.get("required")
    assert set(schema["properties"]) >= {"title", "author_id"}
    assert "author" not in schema["properties"]


def test_partial_schema_bare_fk_dumps_only_provided_key():
    uid = uuid.uuid4()
    obj = ArticleSerializer.PartialRequestSchema.model_validate({"author": str(uid)})
    assert obj.model_dump(exclude_unset=True) == {"author_id": uid}


# --------------------------------------------------------------------------- #
# Serializer.is_valid (PUT full / PATCH partial)
# --------------------------------------------------------------------------- #


def test_is_valid_put_accepts_bare_fk():
    uid = uuid.uuid4()
    ser = ArticleSerializer(data={"title": "Hi", "author": str(uid)}, partial=False)
    assert ser.is_valid(), ser.errors
    assert ser.validated_data["author_id"] == uid


def test_is_valid_patch_accepts_bare_fk():
    # Regression: the partial path used to filter bare FK keys out silently, so
    # a PATCH of {"author": ...} quietly did nothing. The partial path does not
    # coerce types (model_construct), so the value stays as sent — the point is
    # that it now lands under the canonical key instead of being dropped.
    uid = uuid.uuid4()
    ser = ArticleSerializer(data={"author": str(uid)}, partial=True)
    assert ser.is_valid(), ser.errors
    assert ser.validated_data.get("author_id") == str(uid)
    assert "author" not in ser.validated_data


# --------------------------------------------------------------------------- #
# Error hint
# --------------------------------------------------------------------------- #


def test_field_required_hint_for_bare_fk():
    hint = _field_required_hint("author_id", {"author": "x", "title": "y"})
    assert hint is not None and "author" in hint and "author_id" in hint


def test_field_required_hint_near_miss():
    hint = _field_required_hint("email", {"emial": "a@b.c"})
    assert hint is not None and "email" in hint


def test_field_required_hint_none_when_no_signal():
    assert _field_required_hint("author_id", {"unrelated": 1}) is None
    assert _field_required_hint("items.0.author_id", {"author": "x"}) is None


def test_parse_pydantic_errors_injects_hint():
    errors = [
        {"type": "missing", "loc": ("body", "author_id"), "msg": "Field required"}
    ]
    details = _parse_pydantic_errors(errors, body={"author": "x", "title": "y"})
    assert details[0].code == ErrorCode.FIELD_REQUIRED.value
    assert details[0].meta and "author" in details[0].meta["hint"]


# --------------------------------------------------------------------------- #
# End-to-end over HTTP
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_accepts_bare_fk_over_http(db):
    author = await FKAuthor.objects.create(name="Ada")
    router = SimpleRouter()
    router.register("articles", ArticleViewSet)

    async with _client(router) as client:
        resp = await client.post(
            "/articles", json={"title": "Hello", "author": str(author.pk)}
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Hello"
    assert body["author"] == str(author.pk)


@pytest.mark.asyncio
async def test_patch_bare_fk_actually_updates_over_http(db):
    a1 = await FKAuthor.objects.create(name="Ada")
    a2 = await FKAuthor.objects.create(name="Alan")
    article = await FKArticle.objects.create(title="Draft", author=a1)

    router = SimpleRouter()
    router.register("articles", ArticleViewSet)

    async with _client(router) as client:
        resp = await client.patch(
            f"/articles/{article.pk}", json={"author": str(a2.pk)}
        )
    assert resp.status_code == 200, resp.text

    refreshed = await FKArticle.objects.get(pk=article.pk)
    assert refreshed.author_id == a2.pk


@pytest.mark.asyncio
async def test_create_missing_fk_reports_canonical_field(db):
    router = SimpleRouter()
    router.register("articles", ArticleViewSet)

    async with _client(router) as client:
        resp = await client.post("/articles", json={"title": "Hello"})
    assert resp.status_code == 422, resp.text
    fields_in_error = {d["field"] for d in resp.json()["error"]["details"]}
    assert "author_id" in fields_in_error
