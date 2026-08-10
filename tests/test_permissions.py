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
