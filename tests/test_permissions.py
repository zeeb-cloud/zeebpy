"""ModelPermissions: DB-backed per-model permission enforcement (M7).

``ModelPermissions`` previously read a ``user.permissions`` attribute that no
user model exposes, so it denied every request and never consulted the
permission tables. These tests pin the repaired behavior: it maps the HTTP
method to an ``add_/change_/delete_/view_<model>`` codename and checks it
against the user's ``has_perm_async``.
"""

import pytest

from zeeb_api.permissions import ModelPermissions


def _models():
    from zeeb_api.auth.models import Permission, User, UserPermission

    return (User, Permission, UserPermission)


@pytest.fixture
async def db():
    """In-memory DB registering the auth models (pattern from test_oauth.py)."""
    from zeeb_orm import close_all_connections, configure, setup_database
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    models = _models()
    Settings.reset()
    for model in models:
        model._sa_table = None
        model._sa_model = None
    metadata.clear()

    configure(database={"url": "sqlite+aiosqlite:///:memory:"})
    database = await setup_database("sqlite+aiosqlite:///:memory:")
    for model in models:
        model._get_table()
    await database.create_all()

    yield database

    await database.drop_all()
    await close_all_connections()
    for model in models:
        table = metadata.tables.get(model._meta.db_table)
        if table is not None:
            metadata.remove(table)
        model._sa_table = None
        model._sa_model = None
    Settings.reset()


class Widget:
    """Stand-in model whose lowercased name drives the codename (view_widget…)."""

    class _Meta:
        db_table = "widgets"

    _meta = _Meta()


class _View:
    """Minimal view exposing a queryset with a ``.model``."""

    class _QS:
        model = Widget

    queryset = _QS()


def _request(method: str, user):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": method,
        "path": "/",
        "query_string": b"",
        "headers": [],
    }
    req = Request(scope)
    req.state.user = user
    return req


async def _grant(user, codename: str):
    from zeeb_api.auth.models import Permission, UserPermission

    perm = await Permission.objects.create(name=codename, codename=codename)
    await UserPermission.objects.create(user_id=user.id, permission_id=perm.id)


async def test_denies_anonymous(db):
    assert await ModelPermissions().has_permission(_request("GET", None), _View()) is False


async def test_denies_user_without_permission(db):
    from zeeb_api.auth.backends import create_user

    user = await create_user(email="noperm@example.com", password="pw-123456")
    # No grant: viewing requires view_widget, which the user does not hold.
    assert await ModelPermissions().has_permission(_request("GET", user), _View()) is False


async def test_allows_user_with_matching_permission(db):
    from zeeb_api.auth.backends import create_user

    user = await create_user(email="viewer@example.com", password="pw-123456")
    await _grant(user, "view_widget")

    assert await ModelPermissions().has_permission(_request("GET", user), _View()) is True
    # But that grant does not authorize a write.
    assert await ModelPermissions().has_permission(_request("POST", user), _View()) is False


async def test_write_requires_the_write_codename(db):
    from zeeb_api.auth.backends import create_user

    user = await create_user(email="editor@example.com", password="pw-123456")
    await _grant(user, "add_widget")

    assert await ModelPermissions().has_permission(_request("POST", user), _View()) is True


async def test_superuser_passes_without_explicit_grant(db):
    from zeeb_api.auth.backends import create_user

    user = await create_user(email="root@example.com", password="pw-123456")
    user.is_superuser = True
    await user.save()

    assert await ModelPermissions().has_permission(_request("DELETE", user), _View()) is True


async def test_token_only_user_without_db_lookup_is_denied(db):
    """A user object lacking has_perm_async cannot be verified → deny."""

    class _TokenOnlyUser:
        is_authenticated = True
        # deliberately no has_perm_async

    assert await ModelPermissions().has_permission(
        _request("GET", _TokenOnlyUser()), _View()
    ) is False


# --------------------------------------------------------------------------- #
# POST /query is a read; unknown methods and model-less views are denied
# --------------------------------------------------------------------------- #


class _QueryView(_View):
    action = "query"


async def test_query_requires_view_not_add(db):
    """POST /query only reads: view_<model> grants it, add_<model> does not."""
    from zeeb_api.auth.backends import create_user

    reader = await create_user(email="reader@example.com", password="pw-123456")
    await _grant(reader, "view_widget")
    adder = await create_user(email="adder@example.com", password="pw-123456")
    await _grant(adder, "add_widget")

    assert await ModelPermissions().has_permission(_request("POST", reader), _QueryView()) is True
    assert await ModelPermissions().has_permission(_request("POST", adder), _QueryView()) is False
    # A plain POST (create) still needs add_<model>.
    assert await ModelPermissions().has_permission(_request("POST", adder), _View()) is True
    assert await ModelPermissions().has_permission(_request("POST", reader), _View()) is False


async def test_unknown_method_is_denied(db):
    from zeeb_api.auth.backends import create_user

    user = await create_user(email="odd@example.com", password="pw-123456")
    for codename in ("view_widget", "add_widget", "change_widget", "delete_widget"):
        await _grant(user, codename)

    assert await ModelPermissions().has_permission(_request("PROPFIND", user), _View()) is False
    assert await ModelPermissions().has_permission(_request("TRACE", user), _View()) is False


async def test_view_without_a_model_is_denied(db):
    from zeeb_api.auth.backends import create_user

    user = await create_user(email="nomodel@example.com", password="pw-123456")

    class _NoModelView:
        queryset = None

    assert await ModelPermissions().has_permission(_request("GET", user), _NoModelView()) is False


# --------------------------------------------------------------------------- #
# IsOwner compares the foreign-key column, not the lazily loaded relation
# --------------------------------------------------------------------------- #


def _owned_model():
    from zeeb_orm import Model, fields

    class OwnedGadget(Model):
        owner = fields.ForeignKey("User", on_delete="CASCADE")
        name = fields.CharField(max_length=50)

        class Meta:
            table_name = "owned_gadgets"

    return OwnedGadget


class _User:
    is_authenticated = True

    def __init__(self, user_id):
        self.id = user_id


async def test_is_owner_allows_owner_of_an_unloaded_foreign_key():
    import uuid

    from zeeb_api.permissions import IsOwner, IsOwnerOrReadOnly
    from zeeb_orm.models.fields import ForeignKeyLazyLoader

    owner_id = uuid.uuid4()
    gadget = _owned_model()(owner_id=owner_id, name="g")
    # The relation is not loaded: this is what the old code compared against.
    assert isinstance(gadget.owner, ForeignKeyLazyLoader)

    assert await IsOwner().has_object_permission(
        _request("PATCH", _User(owner_id)), None, gadget
    ) is True
    # A token-only user carries the id as a string (the ``sub`` claim).
    assert await IsOwner().has_object_permission(
        _request("PATCH", _User(str(owner_id))), None, gadget
    ) is True
    assert await IsOwnerOrReadOnly().has_object_permission(
        _request("DELETE", _User(owner_id)), None, gadget
    ) is True


async def test_is_owner_denies_someone_else():
    import uuid

    from zeeb_api.permissions import IsOwner

    gadget = _owned_model()(owner_id=uuid.uuid4(), name="g")
    assert await IsOwner().has_object_permission(
        _request("PATCH", _User(uuid.uuid4())), None, gadget
    ) is False
    assert await IsOwner().has_object_permission(_request("PATCH", None), None, gadget) is False
