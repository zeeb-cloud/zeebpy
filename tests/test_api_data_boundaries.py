"""Regression tests for the data boundaries of the API layer.

- The ``/query`` and ``?ordering=`` allow-lists come from what the serializer
  *returns*: a ``SerializerMethodField`` named like a column used to make that
  column filterable and orderable (an oracle for the value it masks), and a
  declared field with ``source=`` put the column under the wrong name.
- A ``PrimaryKeyRelatedField`` declared on a ``ModelSerializer`` was ignored:
  ``read_only=True`` left ``<fk>_id`` writable, ``queryset=`` checked nothing.
- ``read_only_fields`` / ``extra_kwargs`` naming a FK's request key
  (``author_id``) matched nothing; unknown names were ignored silently.
- Huge page numbers / offsets reached the driver and failed with a 500.
- A prefix nested under another (``posts/archive`` under ``posts``) was
  captured by the outer viewset's ``/posts/{id}``.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.exceptions import ImproperlyConfigured
from zeeb_api.filters import OrderingFilter
from zeeb_api.pagination import LimitOffsetPagination, PageNumberPagination
from zeeb_api.query import FieldPathError, check_field_path, serializer_field_paths
from zeeb_api.routers import DefaultRouter, SimpleRouter
from zeeb_api.serializers import (
    CharField,
    ModelSerializer,
    NestedSerializer,
    PrimaryKeyRelatedField,
    SerializerMethodField,
    SlugRelatedField,
)
from zeeb_api.viewsets import ModelViewSet
from zeeb_orm import Model, fields
from zeeb_orm.testing import temporary_database


class BdAccount(Model):
    id = fields.AutoField()
    name = fields.CharField(max_length=50)
    email = fields.CharField(max_length=100)
    active = fields.BooleanField(default=True)

    class Meta:
        table_name = "bd_accounts"


class BdPost(Model):
    id = fields.AutoField()
    title = fields.CharField(max_length=50)
    author = fields.ForeignKey(BdAccount, on_delete="CASCADE", related_name="bd_posts")

    class Meta:
        table_name = "bd_posts"


# --- serializers -------------------------------------------------------------


class MaskedAccountSerializer(ModelSerializer):
    email = SerializerMethodField()

    class Meta:
        model = BdAccount
        fields = ["id", "name", "email"]

    def get_email(self, obj) -> str:
        return obj.email[0] + "***"


class AliasedAccountSerializer(ModelSerializer):
    """``email`` returns the *name* column; the email column is not returned."""

    email = CharField(source="name", read_only=True)

    class Meta:
        model = BdAccount
        fields = ["id", "email"]


class AccountSerializer(ModelSerializer):
    class Meta:
        model = BdAccount
        fields = ["id", "name"]


class NamelessAccountSerializer(ModelSerializer):
    class Meta:
        model = BdAccount
        fields = ["name"]


class PostSerializer(ModelSerializer):
    class Meta:
        model = BdPost
        fields = ["id", "title", "author"]


class AuthorNameSerializer(ModelSerializer):
    author_name = CharField(source="author.name", read_only=True)

    class Meta:
        model = BdPost
        fields = ["id", "title", "author_name"]


class ReadOnlyAuthorSerializer(ModelSerializer):
    author = PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = BdPost
        fields = ["id", "title", "author"]


class ActiveAuthorSerializer(ModelSerializer):
    author = PrimaryKeyRelatedField(queryset=BdAccount.objects.filter(active=True))

    class Meta:
        model = BdPost
        fields = ["id", "title", "author"]


class SlugAuthorSerializer(ModelSerializer):
    author = SlugRelatedField(slug_field="name", queryset=BdAccount.objects.all())

    class Meta:
        model = BdPost
        fields = ["id", "title", "author"]


class RoIdSerializer(ModelSerializer):
    class Meta:
        model = BdPost
        fields = ["id", "title", "author"]
        read_only_fields = ["author_id"]


class ExtraRoIdSerializer(ModelSerializer):
    class Meta:
        model = BdPost
        fields = ["id", "title", "author"]
        extra_kwargs = {"author_id": {"read_only": True}}


def _viewset(serializer, **attrs):
    return type(
        f"{serializer.__name__}ViewSet",
        (ModelViewSet,),
        {
            "queryset": serializer.Meta.model.objects,
            "serializer_class": serializer,
            **attrs,
        },
    )


def _client(router) -> AsyncClient:
    app = FastAPI()
    install_exception_handlers(app)
    for r in router.get_urls():
        app.include_router(r)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _simple(**viewsets) -> AsyncClient:
    router = SimpleRouter()
    for prefix, vs in viewsets.items():
        router.register(prefix, vs)
    return _client(router)


@pytest.fixture
async def db():
    async with temporary_database(BdAccount, BdPost):
        alice = await BdAccount.objects.create(name="alice", email="zz@secret.io")
        bob = await BdAccount.objects.create(name="bob", email="aa@other.io", active=False)
        post = await BdPost.objects.create(title="hello", author_id=alice.id)
        yield alice, bob, post


def _messages(response) -> str:
    return " ".join(d["message"] for d in response.json()["error"]["details"])


# --- 1. allow-lists follow the serializer's output ---------------------------


def test_field_paths_leave_out_method_fields_and_follow_sources():
    assert serializer_field_paths(MaskedAccountSerializer) == {"id": "id", "name": "name"}
    assert serializer_field_paths(AliasedAccountSerializer) == {"id": "id", "email": "name"}
    assert serializer_field_paths(AuthorNameSerializer)["author_name"] == "author__name"


def test_check_field_path_translates_through_a_mapping():
    mapping = {"id": "id", "email": "name", "author": "author"}
    assert check_field_path(BdAccount, "-email__icontains", mapping) == "-name__icontains"
    assert check_field_path(BdAccount, "pk", mapping) == "pk"
    assert check_field_path(BdPost, "author_id__in", {"author": "author"}) == "author_id__in"
    with pytest.raises(FieldPathError):
        check_field_path(BdAccount, "name", mapping)  # only reachable as "email"
    with pytest.raises(FieldPathError):
        check_field_path(BdPost, "author__email", mapping)


async def test_method_field_shadowing_a_column_is_not_filterable(db):
    async with _simple(accounts=_viewset(MaskedAccountSerializer)) as c:
        r = await c.post("/accounts/query", json={"filter": "Q(email__startswith='zz@sec')"})
        o = await c.post("/accounts/query", json={"order_by": ["email"]})
    assert r.status_code == 400 and "email__startswith" in _messages(r)
    assert o.status_code == 400


async def test_method_field_shadowing_a_column_is_not_orderable(db):
    vs = _viewset(MaskedAccountSerializer, filter_backends=[OrderingFilter])
    async with _simple(accounts=vs) as c:
        up = await c.get("/accounts?ordering=email")
        down = await c.get("/accounts?ordering=-email")
        by_name = await c.get("/accounts?ordering=-name")
    # The term is dropped: both come back in the default order.
    assert [a["name"] for a in up.json()] == [a["name"] for a in down.json()]
    assert [a["name"] for a in by_name.json()] == ["bob", "alice"]


async def test_source_field_is_queried_by_its_output_name(db):
    vs = _viewset(AliasedAccountSerializer, filter_backends=[OrderingFilter])
    async with _simple(accounts=vs) as c:
        hit = await c.post("/accounts/query", json={"filter": "Q(email='alice')"})
        ordered = await c.post("/accounts/query", json={"order_by": ["-email"]})
        by_column = await c.post("/accounts/query", json={"filter": "Q(name='alice')"})
        hidden = await c.post("/accounts/query", json={"filter": "Q(email__startswith='zz')"})
        get_ordered = await c.get("/accounts?ordering=-email")
    assert hit.status_code == 200 and hit.json()["count"] == 1
    assert [a["email"] for a in ordered.json()["results"]] == ["bob", "alice"]
    assert [a["email"] for a in get_ordered.json()] == ["bob", "alice"]
    # The column behind it is not reachable under its own name ...
    assert by_column.status_code == 400
    # ... and the hidden email column stays out of reach entirely.
    assert hidden.status_code == 200 and hidden.json()["count"] == 0


async def test_dotted_source_filters_through_the_relation(db):
    async with _simple(posts=_viewset(AuthorNameSerializer)) as c:
        hit = await c.post("/posts/query", json={"filter": "Q(author_name='alice')"})
        miss = await c.post("/posts/query", json={"filter": "Q(author_name='bob')"})
    assert hit.json()["count"] == 1 and miss.json()["count"] == 0


async def test_related_traversal_keeps_only_the_related_pk(db):
    alice, _, _ = db
    async with _simple(posts=_viewset(PostSerializer)) as c:
        by_pk = await c.post("/posts/query", json={"filter": f"Q(author__id={alice.id})"})
        by_id = await c.post("/posts/query", json={"filter": f"Q(author_id={alice.id})"})
        hidden = await c.post("/posts/query", json={"filter": "Q(author__email__startswith='z')"})
    assert by_pk.json()["count"] == 1 and by_id.json()["count"] == 1
    assert hidden.status_code == 400


def test_nested_relation_is_queryable_only_when_it_returns_the_pk():
    class WithPk(ModelSerializer):
        author = NestedSerializer(AccountSerializer)

        class Meta:
            model = BdPost
            fields = ["id", "author"]

    class WithoutPk(ModelSerializer):
        author = NestedSerializer(NamelessAccountSerializer)

        class Meta:
            model = BdPost
            fields = ["id", "author"]

    assert "author" in serializer_field_paths(WithPk)
    assert "author" not in serializer_field_paths(WithoutPk)


# --- 2. declared PrimaryKeyRelatedField / SlugRelatedField -------------------


async def test_read_only_pk_related_field_is_not_writable(db):
    alice, bob, post = db
    assert "author_id" not in ReadOnlyAuthorSerializer.RequestSchema.model_fields
    async with _simple(posts=_viewset(ReadOnlyAuthorSerializer)) as c:
        r = await c.patch(f"/posts/{post.id}", json={"author_id": bob.id})
        r2 = await c.patch(f"/posts/{post.id}", json={"author": bob.id})
    assert r.status_code == 200 and r.json()["author"] == alice.id
    assert r2.status_code == 200
    assert (await BdPost.objects.get(id=post.id)).author_id == alice.id


async def test_pk_related_field_checks_its_queryset(db):
    alice, bob, post = db
    async with _simple(posts=_viewset(ActiveAuthorSerializer)) as c:
        outside = await c.patch(f"/posts/{post.id}", json={"author_id": bob.id})
        dangling = await c.post("/posts", json={"title": "x", "author": 999999})
        null = await c.patch(f"/posts/{post.id}", json={"author_id": None})
        ok = await c.post("/posts", json={"title": "y", "author": alice.id})
    assert outside.status_code == 400
    assert outside.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "does not exist" in _messages(outside)
    assert dangling.status_code == 400
    assert null.status_code == 400
    assert ok.status_code == 201 and ok.json()["author"] == alice.id
    assert (await BdPost.objects.get(id=post.id)).author_id == alice.id


async def test_pk_related_field_needs_ais_valid(db):
    alice, _, _ = db
    serializer = ActiveAuthorSerializer(data={"title": "x", "author_id": alice.id})
    with pytest.raises(TypeError, match="ais_valid"):
        serializer.is_valid()
    assert await serializer.ais_valid()
    assert serializer.validated_data["author_id"] == alice.id


async def test_slug_related_field_reads_and_writes_the_slug(db):
    alice, bob, post = db
    async with _simple(posts=_viewset(SlugAuthorSerializer)) as c:
        shown = await c.get(f"/posts/{post.id}")
        moved = await c.patch(f"/posts/{post.id}", json={"author": "bob"})
        unknown = await c.patch(f"/posts/{post.id}", json={"author": "carol"})
        by_slug = await c.post("/posts/query", json={"filter": "Q(author='bob')"})
    assert shown.json()["author"] == "alice"
    assert moved.status_code == 200 and moved.json()["author"] == "bob"
    assert (await BdPost.objects.get(id=post.id)).author_id == bob.id
    assert unknown.status_code == 400 and "carol" in _messages(unknown)
    assert by_slug.json()["count"] == 1


def test_misdeclared_related_fields_raise_at_class_definition():
    with pytest.raises(ImproperlyConfigured, match="ForeignKey"):

        class OnAColumn(ModelSerializer):
            title = PrimaryKeyRelatedField()

            class Meta:
                model = BdPost
                fields = ["id", "title"]

    with pytest.raises(ImproperlyConfigured, match="slug_field"):

        class BadSlug(ModelSerializer):
            author = SlugRelatedField(slug_field="nope")

            class Meta:
                model = BdPost
                fields = ["id", "author"]

    with pytest.raises(ImproperlyConfigured, match="nested"):

        class WritableNested(ModelSerializer):
            author = NestedSerializer(AccountSerializer, read_only=False)

            class Meta:
                model = BdPost
                fields = ["id", "author"]


# --- 3. read_only_fields / extra_kwargs spellings -----------------------------


@pytest.mark.parametrize("serializer", [RoIdSerializer, ExtraRoIdSerializer])
async def test_fk_request_key_in_read_only_settings(db, serializer):
    alice, bob, post = db
    assert "author_id" not in serializer.RequestSchema.model_fields
    async with _simple(posts=_viewset(serializer)) as c:
        r = await c.patch(f"/posts/{post.id}", json={"author_id": bob.id})
    assert r.status_code == 200
    assert (await BdPost.objects.get(id=post.id)).author_id == alice.id


def test_unknown_names_in_read_only_settings_raise():
    with pytest.raises(ImproperlyConfigured, match="autor"):

        class Typo(ModelSerializer):
            class Meta:
                model = BdPost
                fields = ["id", "title", "author"]
                read_only_fields = ["autor"]

    with pytest.raises(ImproperlyConfigured, match="titel"):

        class ExtraTypo(ModelSerializer):
            class Meta:
                model = BdPost
                fields = ["id", "title"]
                extra_kwargs = {"titel": {"required": False}}

    # A real model field that Meta.fields leaves out is moot, not an error.
    class Unlisted(ModelSerializer):
        class Meta:
            model = BdPost
            fields = ["id", "title"]
            read_only_fields = ["id", "author"]

    assert "title" in Unlisted.RequestSchema.model_fields


# --- 4. huge offsets ----------------------------------------------------------


async def test_huge_offsets_never_reach_the_driver(db):
    paged = _viewset(PostSerializer, pagination_class=PageNumberPagination)
    lo = _viewset(PostSerializer, pagination_class=LimitOffsetPagination)
    async with _simple(paged=paged, lo=lo, posts=_viewset(PostSerializer)) as c:
        page = await c.get("/paged?page=99999999999999999999")
        offset = await c.get("/lo?offset=99999999999999999999")
        query = await c.post("/posts/query", json={"offset": 10**20})
        past_end = await c.post("/posts/query", json={"offset": 2**63 - 1})
    assert page.status_code == 200 and page.json()["results"] == []
    assert offset.status_code == 200 and offset.json()["results"] == []
    assert query.status_code == 422
    assert query.json()["error"]["code"] == "VALIDATION_ERROR"
    assert past_end.status_code == 200 and past_end.json()["results"] == []


# --- 5. nested prefixes -------------------------------------------------------


class SlugPostViewSet(ModelViewSet):
    queryset = BdPost.objects
    serializer_class = PostSerializer
    lookup_field = "title"


class ArchiveViewSet(ModelViewSet):
    queryset = BdAccount.objects
    serializer_class = AccountSerializer


@pytest.mark.parametrize("outer_first", [True, False])
async def test_nested_prefix_is_not_shadowed(db, outer_first):
    router = SimpleRouter()
    entries = [("posts", SlugPostViewSet), ("posts/archive", ArchiveViewSet)]
    for prefix, vs in entries if outer_first else reversed(entries):
        router.register(prefix, vs)
    async with _client(router) as c:
        archive = await c.get("/posts/archive")
        detail = await c.get("/posts/hello")
    assert archive.status_code == 200 and {a["name"] for a in archive.json()} == {"alice", "bob"}
    assert detail.status_code == 200 and detail.json()["title"] == "hello"


async def test_nested_prefix_through_include(db):
    blog = SimpleRouter()
    blog.register("posts", SlugPostViewSet)
    archive = SimpleRouter()
    archive.register("archive", ArchiveViewSet)
    router = DefaultRouter()
    router.include(blog, prefix="blog")
    router.include(archive, prefix="blog/posts")
    async with _client(router) as c:
        r = await c.get("/blog/posts/archive")
        d = await c.get("/blog/posts/hello")
    assert r.status_code == 200 and len(r.json()) == 2
    assert d.status_code == 200
