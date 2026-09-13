"""App file templates for ``zeeb startapp``.

Rendered with ``str.format(app_name=..., app_class=..., app_title=...,
model_name=..., model_slug=..., table_name=..., route_prefix=...)``, so every
literal brace is doubled.

Two sets:

``MODELS_PY`` & friends are the default, written when no ``--model`` is given.
They carry the canonical example in the **module docstring** rather than as
commented-out code. A comment teaches nothing that survives: an agent asked to
add a model copies the shape it sees, and commented code neither lints nor
runs. The live code is a single import per file, so a user who wants a clean
slate has nothing to delete.

``SLICE_*`` are written by ``startapp <name> --model <Name>`` and mirror
``demo_blog/apps/post/*``: a complete, running resource plus a test that goes
green. That is the executable pattern — the strongest thing an agent can copy.

An embedded example must never contain a triple quote: it lives inside the
generated file's own docstring.
"""

MODELS_PY = '''"""{app_title} models.

A model is a class over a database table. The canonical shape:

    class {model_name}(Model):
        title = fields.CharField(max_length=200)
        slug = fields.SlugField(max_length=220, unique=True)
        body = fields.TextField(blank=True)
        published = fields.BooleanField(default=False)
        owner = fields.ForeignKey(
            "accounts.User", on_delete="CASCADE", related_name="{route_prefix}"
        )
        created_at = fields.DateTimeField(auto_now_add=True)
        updated_at = fields.DateTimeField(auto_now=True)

        class Meta:
            table_name = "{table_name}"
            ordering = ["-created_at"]

Point a foreign key at the project's user model by its labelled name,
"accounts.User". The framework ships a class of the same bare name, so a plain
"User" resolves by import order. Cross-app references use the same form,
"{app_name}.{model_name}".

Every model change needs a migration:

    python manage.py makemigrations
    python manage.py migrate

To scaffold model, serializer, viewset, route and a passing test in one step,
use `python manage.py startapp {app_name} --model {model_name}` on a new app.
"""

from zeeb_orm import Model, fields  # noqa: F401  (ready for your first model)
'''

SERIALIZERS_PY = '''"""{app_title} serializers.

A ModelSerializer derives request validation and the response shape from the
model, so the two cannot drift:

    from .models import {model_name}

    class {model_name}Serializer(serializers.ModelSerializer):
        class Meta:
            model = {model_name}
            fields = ["id", "title", "body", "owner", "created_at"]
            read_only_fields = ["id", "owner", "created_at"]

`read_only_fields` is the write boundary: such a field is returned but never
accepted from the request body — that is how server-owned values (an owner, a
timestamp, a status) stay server-owned.

Foreign keys are returned under the bare name ("owner") and accepted as either
"owner" or "owner_id". Use `fields = "__all__"` to expose every model field.
"""

from zeeb_api import serializers  # noqa: F401  (ready for your first serializer)
'''

VIEWS_PY = '''"""{app_title} views.

A ModelViewSet turns a model plus a serializer into list, retrieve, create,
update, partial_update, destroy and a POST /query endpoint:

    from .models import {model_name}
    from .serializers import {model_name}Serializer

    class {model_name}ViewSet(viewsets.ModelViewSet):
        queryset = {model_name}.objects.all()
        serializer_class = {model_name}Serializer
        permission_classes = [permissions.IsAuthenticatedOrReadOnly]

        async def perform_create(self, serializer: {model_name}Serializer) -> None:
            # Server-owned values are stamped here, never trusted from the body.
            await serializer.save(owner_id=self.request.state.user.id)

`queryset` must be a queryset, not the bare manager — write
`{model_name}.objects.all()`.

Permission classes: AllowAny, IsAuthenticated, IsAdminUser,
IsAuthenticatedOrReadOnly, IsOwner, IsOwnerOrReadOnly, ModelPermissions. They
are ANDed, so every listed class must admit the request.

Extra endpoints are methods decorated with
`@viewsets.action(detail=True, methods=["post"])`.

Raise the classes from `zeeb_api.exceptions` (NotFound, ValidationError,
PermissionDenied, ResourceConflictException) rather than HTTPException — that is
what keeps every response inside the project's standard error envelope.
"""

from zeeb_api import permissions, viewsets  # noqa: F401  (ready for your first viewset)
'''

URLS_PY = '''"""{app_title} URL configuration.

Registering a viewset here is what makes its endpoints exist:

    from .views import {model_name}ViewSet

    router.register("{route_prefix}", {model_name}ViewSet)

Use a bare segment. The project urls.py includes this router without a prefix,
and create_app() prepends settings.API_PREFIX (default /api/v1), so the example
above serves /api/v1/{route_prefix}. Both /{route_prefix} and /{route_prefix}/
are answered; generate clients from the slash-less form.
"""

from zeeb_api.routers import DefaultRouter

router = DefaultRouter()
'''

APP_INIT_PY = '''"""{app_title} app."""
'''

TESTS_PY = '''"""Tests for the {app_name} app.

Fixtures come from tests/conftest.py:
    db            schema created, every table emptied before each test
    client        anonymous httpx client against the app
    auth_client   client carrying an ordinary user's bearer token
    admin_client  client carrying a staff user's bearer token
    api_prefix    settings.API_PREFIX (default "/api/v1")

Run them with:  pytest -q
"""


async def test_the_app_boots_with_{app_name}_installed(client):
    """Guards the import chain.

    create_app() imports apps.{app_name}.urls, which imports views, which imports
    serializers and models. A typo in any of them fails here rather than at the
    first request in production.
    """
    response = await client.get("/openapi.json")
    assert response.status_code == 200, response.text
'''

# ---------------------------------------------------------------------------
# ``startapp <name> --model <Name>``: a complete, running resource.
# ---------------------------------------------------------------------------

SLICE_MODELS_PY = '''"""{app_title} models."""

from zeeb_orm import Model, fields


class {model_name}(Model):
    """A {model_slug}, owned by the user who created it."""

    title = fields.CharField(max_length=200)
    slug = fields.SlugField(max_length=220, unique=True)
    body = fields.TextField(blank=True)
    published = fields.BooleanField(default=False)
    owner = fields.ForeignKey(
        "accounts.User", on_delete="CASCADE", related_name="{route_prefix}", null=True
    )
    created_at = fields.DateTimeField(auto_now_add=True)
    updated_at = fields.DateTimeField(auto_now=True)

    class Meta:
        table_name = "{table_name}"
        ordering = ["-created_at"]
'''

SLICE_SERIALIZERS_PY = '''"""{app_title} serializers."""

from zeeb_api import serializers

from .models import {model_name}


class {model_name}Serializer(serializers.ModelSerializer):
    """Read and write shape of a {model_slug}."""

    class Meta:
        model = {model_name}
        fields = [
            "id",
            "title",
            "slug",
            "body",
            "published",
            "owner",
            "created_at",
            "updated_at",
        ]
        # Server-owned: returned, never accepted from the request body.
        read_only_fields = ["id", "owner", "created_at", "updated_at"]
'''

SLICE_VIEWS_PY = '''"""{app_title} views."""

from zeeb_api import permissions, viewsets

from .models import {model_name}
from .serializers import {model_name}Serializer


class {model_name}ViewSet(viewsets.ModelViewSet):
    """Anyone may read; writing needs a bearer token, and the owner is the caller.

    Serves list, retrieve, create, update, partial_update, destroy and
    POST /query under the prefix registered in urls.py.
    """

    queryset = {model_name}.objects.all()
    serializer_class = {model_name}Serializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    async def perform_create(self, serializer: {model_name}Serializer) -> None:
        """Stamp the authenticated caller as owner — never trust the body."""
        await serializer.save(owner_id=self.request.state.user.id)
'''

SLICE_URLS_PY = '''"""{app_title} URL configuration."""

from zeeb_api.routers import DefaultRouter

from .views import {model_name}ViewSet

router = DefaultRouter()
router.register("{route_prefix}", {model_name}ViewSet)
'''

SLICE_TESTS_PY = '''"""Tests for the {app_name} app, written by `startapp --model {model_name}`.

Fixtures come from tests/conftest.py: db, client, auth_client, admin_client,
api_prefix. Run them with:  pytest -q
"""

from apps.{app_name}.models import {model_name}


async def test_{model_slug}_roundtrips_through_the_orm(db):
    created = await {model_name}.objects.create(title="First", slug="first", body="hello")
    fetched = await {model_name}.objects.get(id=created.id)
    assert fetched.title == "First"
    assert await {model_name}.objects.filter(slug="first").count() == 1


async def test_{route_prefix}_are_readable_anonymously(client, api_prefix, db):
    response = await client.get(f"{{api_prefix}}/{route_prefix}")
    assert response.status_code == 200, response.text


async def test_creating_a_{model_slug}_requires_a_token(client, auth_client, api_prefix, db):
    payload = {{"title": "Second", "slug": "second", "body": "hi"}}

    rejected = await client.post(f"{{api_prefix}}/{route_prefix}", json=payload)
    assert rejected.status_code in (401, 403), rejected.text

    created = await auth_client.post(f"{{api_prefix}}/{route_prefix}", json=payload)
    assert created.status_code == 201, created.text
    # The owner comes from the token, never from the request body.
    assert created.json()["owner"] is not None
'''
