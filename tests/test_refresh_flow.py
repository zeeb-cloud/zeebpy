"""Refresh-token hardening: claims preservation, rotation/reuse, re-validation (H2).

The refresh endpoint used to mint a new pair from the presented refresh token's
claims (always empty, since refresh tokens carried none) with no rotation and no
user re-check. These tests pin the hardened behavior.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.auth.jwt import configure_jwt, create_token_pair, decode_token
from zeeb_api.auth.refresh_store import (
    InMemoryRefreshTokenStore,
    set_refresh_token_store,
)
from zeeb_api.auth.router import create_auth_router
from zeeb_api.exception_handlers import install_exception_handlers

TEST_SECRET = "a-real-strong-secret-of-at-least-32-bytes"


@pytest.fixture(autouse=True)
def fresh_store():
    """Isolate the per-process consumed-jti store between tests."""
    set_refresh_token_store(InMemoryRefreshTokenStore())
    yield
    set_refresh_token_store(InMemoryRefreshTokenStore())


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _make_app(**router_kwargs) -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_auth_router(**router_kwargs))
    return app


# --------------------------------------------------------------------------- #
# Non-database (custom authenticate) path
# --------------------------------------------------------------------------- #


def _static_auth_app() -> FastAPI:
    configure_jwt(secret_key=TEST_SECRET)

    async def authenticate(request, body):
        if body.get("password") == "secret123":
            return ("user-1", {"is_staff": True, "roles": ["admin"]})
        return None

    return _make_app(
        authenticate=authenticate, use_database=False, enable_registration=False
    )


async def test_refresh_preserves_claims_without_database():
    app = _static_auth_app()
    async with _client(app) as client:
        login = await client.post(
            "/auth/login", json={"email": "a@b.co", "password": "secret123"}
        )
        assert login.status_code == 200
        refresh_token = login.json()["refresh_token"]

        refreshed = await client.post(
            "/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert refreshed.status_code == 200

    access = decode_token(refreshed.json()["access_token"], token_type="access")
    # The refreshed access token still carries is_staff / roles (previously lost).
    assert access.claims.get("is_staff") is True
    assert access.claims.get("roles") == ["admin"]


async def test_refresh_token_is_single_use():
    app = _static_auth_app()
    async with _client(app) as client:
        login = await client.post(
            "/auth/login", json={"email": "a@b.co", "password": "secret123"}
        )
        refresh_token = login.json()["refresh_token"]

        first = await client.post(
            "/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert first.status_code == 200

        # Replaying the same (now rotated) refresh token is rejected.
        replay = await client.post(
            "/auth/refresh", json={"refresh_token": refresh_token}
        )
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "AUTH_TOKEN_INVALID"

        # The freshly issued refresh token still works.
        rotated = first.json()["refresh_token"]
        again = await client.post("/auth/refresh", json={"refresh_token": rotated})
        assert again.status_code == 200


# --------------------------------------------------------------------------- #
# Database-backed path: user re-validation + authoritative claims
# --------------------------------------------------------------------------- #


def _db_models():
    # ExternalIdentity has a reverse FK to User; deleting a user cascades to it,
    # so its table must exist once any test has imported the OAuth models.
    from zeeb_api.auth.models import Permission, User, UserPermission
    from zeeb_api.auth.oauth.models import ExternalIdentity

    return (User, Permission, UserPermission, ExternalIdentity)


@pytest.fixture
async def db():
    from zeeb_orm import close_all_connections, configure, setup_database
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    models = _db_models()
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


async def _issue_refresh_for(user_id: str) -> str:
    configure_jwt(secret_key=TEST_SECRET)
    _access, refresh = create_token_pair(user_id, {"is_staff": True})
    return refresh


async def test_refresh_rejected_for_deleted_user(db):
    from zeeb_api.auth.backends import create_user

    configure_jwt(secret_key=TEST_SECRET)
    user = await create_user(email="gone@example.com", password="pw-123456")
    refresh = await _issue_refresh_for(str(user.id))
    await user.delete()

    app = _make_app(enable_registration=False)
    async with _client(app) as client:
        resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_TOKEN_INVALID"


async def test_refresh_rejected_for_inactive_user(db):
    from zeeb_api.auth.backends import create_user

    configure_jwt(secret_key=TEST_SECRET)
    user = await create_user(email="inactive@example.com", password="pw-123456")
    refresh = await _issue_refresh_for(str(user.id))
    user.is_active = False
    await user.save()

    app = _make_app(enable_registration=False)
    async with _client(app) as client:
        resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 401


async def test_refresh_reloads_claims_from_database(db):
    """Claims come from the current DB state, not the stale token."""
    from zeeb_api.auth.backends import create_user

    configure_jwt(secret_key=TEST_SECRET)
    user = await create_user(email="staff@example.com", password="pw-123456")
    # Token was minted claiming is_staff, but the DB says otherwise.
    _access, refresh = create_token_pair(str(user.id), {"is_staff": True})

    app = _make_app(enable_registration=False)
    async with _client(app) as client:
        resp = await client.post("/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200

    access = decode_token(resp.json()["access_token"], token_type="access")
    # Authoritative claims: the user is not staff.
    assert access.claims.get("is_staff") is False
