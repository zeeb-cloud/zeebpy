"""The auth dependencies refuse what the middleware refuses.

``JWTAuthMiddleware`` treats a deleted or deactivated account as anonymous,
but ``get_current_user``/``get_current_user_optional`` then decoded the still
valid token themselves and returned a claims-only ``AuthenticatedUser``: every
``Depends(get_current_user)`` route — ``/auth/me``, ``/auth/logout``,
``require_auth`` — kept accepting the account for the rest of the token's
lifetime. Without any middleware the dependencies never consulted the database
at all.

``require_auth(roles=...)`` read ``user.claims["roles"]`` — a claim nothing
emitted, and an attribute a database user does not have (500) — and refused
with 401 where the caller was authenticated but not allowed (403).
"""

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.auth.jwt import configure_jwt, create_access_token
from zeeb_api.auth.router import create_auth_router
from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.exceptions import ErrorCode
from zeeb_api.middleware.auth import (
    AuthenticatedUser,
    JWTAuthMiddleware,
    get_current_user,
    get_current_user_optional,
    get_user_roles,
    require_auth,
)

SECRET = "a-real-strong-secret-of-at-least-32-bytes"


@pytest.fixture(autouse=True)
def jwt_secret():
    import zeeb_api.auth.jwt as jwt_module

    saved = jwt_module._jwt_config
    configure_jwt(secret_key=SECRET)
    yield
    jwt_module._jwt_config = saved


def _models():
    from zeeb_api.auth.models import Permission, User, UserPermission
    from zeeb_api.auth.oauth.models import ExternalIdentity

    return (User, Permission, UserPermission, ExternalIdentity)


@pytest.fixture
async def db():
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


def _app(*, with_middleware: bool) -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    if with_middleware:
        app.add_middleware(JWTAuthMiddleware, external_validators=[])
    app.include_router(create_auth_router(enable_registration=False))

    @app.get("/private")
    async def private(user=Depends(get_current_user)):
        return {"id": str(user.id)}

    @app.get("/maybe")
    async def maybe(user=Depends(get_current_user_optional)):
        return {"id": str(user.id) if user is not None else None}

    @app.get("/admin", dependencies=[Depends(require_auth(roles=["admin"]))])
    async def admin():
        return {"ok": True}

    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _user(email: str, **extra):
    from zeeb_api.auth.backends import create_user

    return await create_user(email=email, password="pw-123456", **extra)


def _auth(user) -> dict[str, str]:
    token = create_access_token(str(user.id), user.get_claims())
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("with_middleware", [True, False])
async def test_active_user_is_authenticated(db, with_middleware):
    user = await _user("active@example.com")
    async with _client(_app(with_middleware=with_middleware)) as client:
        me = await client.get("/auth/me", headers=_auth(user))
        private = await client.get("/private", headers=_auth(user))
    assert me.status_code == 200, me.text
    assert private.json() == {"id": str(user.id)}


@pytest.mark.parametrize("with_middleware", [True, False])
@pytest.mark.parametrize("fate", ["deactivated", "deleted"])
async def test_gone_user_is_refused_everywhere(db, with_middleware, fate):
    user = await _user(f"{fate}@example.com")
    headers = _auth(user)
    if fate == "deactivated":
        user.is_active = False
        await user.save()
    else:
        await user.delete()

    async with _client(_app(with_middleware=with_middleware)) as client:
        me = await client.get("/auth/me", headers=headers)
        logout = await client.post("/auth/logout", headers=headers)
        private = await client.get("/private", headers=headers)
        maybe = await client.get("/maybe", headers=headers)
        admin = await client.get("/admin", headers=headers)

    for response in (me, logout, private, admin):
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == ErrorCode.AUTH_TOKEN_INVALID.value
    assert maybe.json() == {"id": None}


@pytest.mark.parametrize("with_middleware", [True, False])
async def test_missing_and_expired_tokens_keep_their_codes(db, with_middleware):
    configure_jwt(secret_key=SECRET, access_token_expire_minutes=-1)
    user = await _user("expired@example.com")
    expired = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    configure_jwt(secret_key=SECRET)

    async with _client(_app(with_middleware=with_middleware)) as client:
        missing = await client.get("/private")
        stale = await client.get("/private", headers=expired)
    assert missing.json()["error"]["code"] == ErrorCode.AUTH_TOKEN_MISSING.value
    assert stale.json()["error"]["code"] == ErrorCode.AUTH_TOKEN_EXPIRED.value


# --------------------------------------------------------------------------- #
# require_auth(roles=...)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("with_middleware", [True, False])
async def test_require_auth_admits_role_holder(db, with_middleware):
    staff = await _user("staff@example.com", is_staff=True)
    async with _client(_app(with_middleware=with_middleware)) as client:
        response = await client.get("/admin", headers=_auth(staff))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("with_middleware", [True, False])
async def test_require_auth_forbids_authenticated_user_without_role(db, with_middleware):
    plain = await _user("plain@example.com")
    async with _client(_app(with_middleware=with_middleware)) as client:
        response = await client.get("/admin", headers=_auth(plain))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == ErrorCode.PERM_INSUFFICIENT_ROLE.value


async def test_require_auth_is_401_when_unauthenticated(db):
    async with _client(_app(with_middleware=True)) as client:
        response = await client.get("/admin")
    assert response.status_code == 401


def test_roles_come_from_flags_claims_and_user_models():
    from zeeb_api.auth.jwt import TokenPayload
    from zeeb_api.auth.models import User

    assert get_user_roles(User(email="a@b.c", is_staff=True)) >= {"staff", "admin"}
    assert get_user_roles(User(email="a@b.c", is_superuser=True)) >= {"superuser", "admin"}
    assert get_user_roles(User(email="a@b.c")) == set()
    assert User(email="a@b.c", is_staff=True).get_claims()["roles"] == ["staff", "admin"]

    payload = TokenPayload(
        sub="1",
        type="access",
        exp="2099-01-01T00:00:00Z",
        iat="2020-01-01T00:00:00Z",
        jti="j",
        claims={"roles": ["editor"]},
    )
    assert get_user_roles(AuthenticatedUser(payload)) == {"editor"}
