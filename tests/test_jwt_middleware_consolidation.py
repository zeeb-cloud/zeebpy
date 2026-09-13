"""The default-installed JWTAuthMiddleware and settings-driven JWT config.

Two implementations of ``JWTAuthMiddleware`` used to coexist: the richer one in
``zeeb_api.auth.middleware`` (recorded ``request.state.auth_error`` and accepted
external OAuth tokens) and the leaner one in ``zeeb_api.middleware.auth`` — the
latter being what the project scaffold actually installs as
``zeeb_api.middleware.JWTAuthMiddleware``. They have been consolidated into a
single class, so a scaffolded app now distinguishes an expired token (refresh)
from an invalid one (re-login) at the middleware layer.

Separately, ``get_jwt_config()`` now derives its secret and lifetimes from
project settings when ``configure_jwt`` was never called explicitly — so a
scaffolded ``asgi.py`` (which does not call the ``create_app`` factory) still
signs tokens with the project's own ``SECRET_KEY``.
"""

import pytest
from starlette.requests import Request
from starlette.responses import Response

from zeeb_api.auth.jwt import (
    _config_from_settings,
    configure_jwt,
    create_access_token,
)
from zeeb_api.exceptions import ErrorCode

SECRET = "a-real-strong-secret-of-at-least-32-bytes"


@pytest.fixture
def restore_jwt_config():
    import zeeb_api.auth.jwt as jwt_module

    saved = jwt_module._jwt_config
    yield
    jwt_module._jwt_config = saved


def _make_request(headers: dict | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": raw,
        "client": ("10.0.0.1", 1234),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


async def _dispatch(request: Request) -> Request:
    """Run the default-installed middleware over a request, return it back."""
    # Import the exact class the scaffold's dotted path resolves to.
    from zeeb_api.middleware import JWTAuthMiddleware

    mw = JWTAuthMiddleware(
        app=None, load_user_from_db=False, external_validators=[]
    )

    async def call_next(_req: Request) -> Response:
        return Response("ok")

    await mw.dispatch(request, call_next)
    return request


# --------------------------------------------------------------------------- #
# Middleware parity: auth_error is recorded on the default-installed middleware
# --------------------------------------------------------------------------- #


async def test_default_middleware_records_expired(restore_jwt_config):
    configure_jwt(secret_key=SECRET, access_token_expire_minutes=-1)
    token = create_access_token("user-7")
    request = await _dispatch(_make_request({"Authorization": f"Bearer {token}"}))
    assert request.state.user is None
    assert request.state.auth_error == ErrorCode.AUTH_TOKEN_EXPIRED


async def test_default_middleware_records_invalid(restore_jwt_config):
    configure_jwt(secret_key=SECRET)
    request = await _dispatch(_make_request({"Authorization": "Bearer not-a-jwt"}))
    assert request.state.user is None
    assert request.state.auth_error == ErrorCode.AUTH_TOKEN_INVALID


async def test_default_middleware_valid_token_sets_user(restore_jwt_config):
    configure_jwt(secret_key=SECRET)
    token = create_access_token("user-7", claims={"is_staff": True})
    request = await _dispatch(_make_request({"Authorization": f"Bearer {token}"}))
    assert request.state.user is not None
    assert request.state.user.id == "user-7"
    assert getattr(request.state, "auth_error", None) is None


async def test_default_middleware_anonymous_is_passthrough(restore_jwt_config):
    # No Authorization header: no secret/DB access, safe as an always-on default.
    request = await _dispatch(_make_request())
    assert request.state.user is None
    assert getattr(request.state, "auth_error", None) is None


def test_both_import_paths_are_the_same_class():
    from zeeb_api.auth.middleware import JWTAuthMiddleware as ViaAuth
    from zeeb_api.middleware import JWTAuthMiddleware as ViaMiddleware

    assert ViaAuth is ViaMiddleware


# --------------------------------------------------------------------------- #
# JWT config derives from settings when not explicitly configured
# --------------------------------------------------------------------------- #


def test_config_from_settings_honors_secret_and_lifetimes(restore_jwt_config):
    from zeeb_api.conf import settings

    overrides = {
        "SECRET_KEY": "another-strong-secret-of-at-least-32b!!",
        "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": 42,
        "JWT_REFRESH_TOKEN_EXPIRE_DAYS": 9,
    }
    # Capture originals and restore via setattr (the settings singleton stores
    # attributes through a custom setter, so delattr-based cleanup would leak
    # into the insecure-secret tests).
    saved = {key: getattr(settings, key) for key in overrides}
    for key, value in overrides.items():
        setattr(settings, key, value)
    try:
        config = _config_from_settings()
        assert config.secret_key == "another-strong-secret-of-at-least-32b!!"
        assert config.access_token_expire_minutes == 42
        assert config.refresh_token_expire_days == 9
    finally:
        for key, value in saved.items():
            setattr(settings, key, value)


# --------------------------------------------------------------------------- #
# default_user_loader must fail CLOSED: never authenticate from token claims
# when the subject no longer resolves to an active account (H3).
# --------------------------------------------------------------------------- #


def _loader_models():
    # ExternalIdentity has a reverse FK to User; a user delete cascades to it,
    # so its table must exist regardless of test ordering.
    from zeeb_api.auth.models import Permission, User, UserPermission
    from zeeb_api.auth.oauth.models import ExternalIdentity

    return (User, Permission, UserPermission, ExternalIdentity)


@pytest.fixture
async def db():
    """In-memory DB registering the auth models (pattern from test_oauth.py)."""
    from zeeb_orm import close_all_connections, configure, setup_database
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    models = _loader_models()
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


async def _payload_for(user_id: str):
    from zeeb_api.auth.jwt import decode_token

    token = create_access_token(user_id, claims={"is_staff": True, "is_superuser": True})
    return decode_token(token, token_type="access")


async def test_loader_returns_active_user(db, restore_jwt_config):
    from zeeb_api.auth.backends import create_user
    from zeeb_api.middleware.auth import default_user_loader

    configure_jwt(secret_key=SECRET)
    user = await create_user(email="active@example.com", password="pw-123456")

    loaded = await default_user_loader(await _payload_for(str(user.id)))
    assert loaded is not None
    assert loaded.id == user.id


async def test_loader_returns_none_for_deleted_user(db, restore_jwt_config):
    from zeeb_api.auth.backends import create_user
    from zeeb_api.middleware.auth import default_user_loader

    configure_jwt(secret_key=SECRET)
    user = await create_user(email="gone@example.com", password="pw-123456")
    payload = await _payload_for(str(user.id))
    await user.delete()

    # Must NOT return a claims-only AuthenticatedUser carrying is_staff/is_superuser.
    assert await default_user_loader(payload) is None


async def test_loader_returns_none_for_inactive_user(db, restore_jwt_config):
    from zeeb_api.auth.backends import create_user
    from zeeb_api.middleware.auth import default_user_loader

    configure_jwt(secret_key=SECRET)
    user = await create_user(email="inactive@example.com", password="pw-123456")
    user.is_active = False
    await user.save()

    assert await default_user_loader(await _payload_for(str(user.id))) is None
