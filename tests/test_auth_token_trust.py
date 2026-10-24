"""Roles and keys an authentication token may not decide on its own.

- A multi-tenant Azure AD provider accepts no tenant until the app names them
  (``allowed_tenants``) or opts into all of them (``allow_any_tenant``), and an
  external token with no linked account carries no roles or staff flags unless
  the deployment trusts them (``trust_token_roles``/``OAUTH_TRUST_TOKEN_ROLES``).
- A user loaded from the database gets its roles from the database alone, not
  from the ``roles`` claim baked into its token at login.
- An unknown ``kid`` (read from an unverified header) cannot make the server
  refetch the JWKS on every request.
- A login against an account with no usable password costs one bcrypt check,
  like a missing account or a wrong password.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, Request

import zeeb_api.auth.jwt as jwt_module
from zeeb_api.auth.jwt import configure_jwt, create_access_token
from zeeb_api.auth.middleware import JWTAuthMiddleware, get_user_roles, require_auth
from zeeb_api.auth.oauth import (
    AzureADProvider,
    ExternalTokenValidator,
    JWKSCache,
    OAuth2Client,
    OAuthProvider,
    OAuthValidationError,
)
from zeeb_api.auth.oauth.registry import clear_provider_cache
from zeeb_api.conf import settings
from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.exceptions import ImproperlyConfigured
from zeeb_api.permissions import IsAdminUser

SECRET = "a-real-strong-test-secret-key-with-more-than-32-bytes"
CLIENT_ID = "zeeb-client-id"
ISSUER = "https://idp.example.com"
OUR_TENANT = "11111111-2222-3333-4444-555555555555"
FOREIGN_TENANT = "99999999-8888-7777-6666-555555555555"


@pytest.fixture(autouse=True)
def jwt_secret():
    saved = jwt_module._jwt_config
    configure_jwt(secret_key=SECRET)
    clear_provider_cache()
    yield
    jwt_module._jwt_config = saved
    clear_provider_cache()


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _jwk(key, kid: str) -> dict:
    jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid=kid, alg="RS256", use="sig")
    return jwk


def _sign(key, kid: str, **claims) -> str:
    now = datetime.now(timezone.utc)
    payload = {"aud": CLIENT_ID, "exp": now + timedelta(minutes=5), "iat": now, **claims}
    return pyjwt.encode(payload, _pem(key), algorithm="RS256", headers={"kid": kid})


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


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _app(*, external_validators, load_user_from_db=True) -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    app.add_middleware(
        JWTAuthMiddleware,
        load_user_from_db=load_user_from_db,
        external_validators=external_validators,
    )

    @app.get("/admin", dependencies=[Depends(require_auth(roles=["admin"]))])
    async def admin():
        return {"ok": True}

    @app.get("/whoami")
    async def whoami(request: Request):
        user = request.state.user
        return {
            "id": None if user is None else str(user.id),
            "is_admin_user": await IsAdminUser().has_permission(request, None),
        }

    return app


# ---------------------------------------------------------------------------
# Finding: foreign-tenant Azure tokens and IdP-asserted roles
# ---------------------------------------------------------------------------


def _azure(rsa_key, **kwargs) -> AzureADProvider:
    jwk = _jwk(rsa_key, "ms")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, json={
                "issuer": "https://login.microsoftonline.com/{tenantid}/v2.0",
                "authorization_endpoint": "https://login.microsoftonline.com/a",
                "token_endpoint": "https://login.microsoftonline.com/t",
                "jwks_uri": "https://login.microsoftonline.com/common/discovery/v2.0/keys",
                "id_token_signing_alg_values_supported": ["RS256"],
            })
        if request.url.path.endswith("/keys"):
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    kwargs.setdefault("tenant", "common")
    return AzureADProvider(
        client_id=CLIENT_ID,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _azure_token(rsa_key, tenant: str, **claims) -> str:
    return _sign(
        rsa_key,
        "ms",
        iss=f"https://login.microsoftonline.com/{tenant}/v2.0",
        sub="pairwise",
        oid="aaaaaaaa-0000-0000-0000-000000000001",
        tid=tenant,
        roles=["admin"],
        is_staff=True,
        is_superuser=True,
        **claims,
    )


async def _get(app, path: str, token: str) -> httpx.Response:
    async with _client(app) as client:
        return await client.get(path, headers={"Authorization": f"Bearer {token}"})


async def test_multi_tenant_azure_refuses_every_tenant_by_default(db, rsa_key):
    provider = _azure(rsa_key)  # tenant="common", no allow-list, no opt-in
    app = _app(external_validators=[ExternalTokenValidator(provider)])
    token = _azure_token(rsa_key, FOREIGN_TENANT)

    assert (await _get(app, "/admin", token)).status_code == 401
    assert (await _get(app, "/whoami", token)).json()["id"] is None
    with pytest.raises(OAuthValidationError, match="allowed_tenants"):
        await provider.validate_external_token(token)


@pytest.mark.parametrize("trust", [False, True])
async def test_tenant_outside_the_allow_list_is_refused(db, rsa_key, trust):
    provider = _azure(rsa_key, allowed_tenants=[OUR_TENANT], trust_token_roles=trust)
    app = _app(external_validators=[ExternalTokenValidator(provider)])
    token = _azure_token(rsa_key, FOREIGN_TENANT)

    assert (await _get(app, "/admin", token)).status_code == 401
    assert (await _get(app, "/whoami", token)).json()["id"] is None


async def test_allowed_tenant_without_opt_in_gets_no_roles(db, rsa_key):
    provider = _azure(rsa_key, allowed_tenants=[OUR_TENANT])
    app = _app(external_validators=[ExternalTokenValidator(provider)])
    token = _azure_token(rsa_key, OUR_TENANT)

    whoami = (await _get(app, "/whoami", token)).json()
    assert whoami == {"id": "aaaaaaaa-0000-0000-0000-000000000001", "is_admin_user": False}
    response = await _get(app, "/admin", token)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERM_INSUFFICIENT_ROLE"


async def test_allowed_tenant_with_opt_in_gets_roles(db, rsa_key):
    provider = _azure(rsa_key, allowed_tenants=[OUR_TENANT], trust_token_roles=True)
    app = _app(external_validators=[ExternalTokenValidator(provider)])
    token = _azure_token(rsa_key, OUR_TENANT)

    assert (await _get(app, "/admin", token)).status_code == 200
    assert (await _get(app, "/whoami", token)).json()["is_admin_user"] is True


async def test_any_tenant_never_trusts_token_roles(db, rsa_key):
    provider = _azure(rsa_key, allow_any_tenant=True, trust_token_roles=True)
    app = _app(external_validators=[ExternalTokenValidator(provider)])
    token = _azure_token(rsa_key, FOREIGN_TENANT)

    whoami = (await _get(app, "/whoami", token)).json()
    assert whoami["id"] is not None  # authenticated...
    assert whoami["is_admin_user"] is False  # ...without the token's staff flags
    assert (await _get(app, "/admin", token)).status_code == 403


def test_allowed_tenants_must_be_guids():
    with pytest.raises(ImproperlyConfigured):
        AzureADProvider(client_id=CLIENT_ID, allowed_tenants=["contoso.com"])


def test_issuer_validator_checks_the_allow_list():
    provider = AzureADProvider(client_id=CLIENT_ID, allowed_tenants=[OUR_TENANT.upper()])
    assert provider._issuer_validator(f"https://login.microsoftonline.com/{OUR_TENANT}/v2.0")
    assert not provider._issuer_validator(
        f"https://login.microsoftonline.com/{FOREIGN_TENANT}/v2.0"
    )


def test_env_oauth_providers_reads_the_azure_tenant_options(monkeypatch):
    from zeeb_api.conf.env import env_oauth_providers

    monkeypatch.setenv("AZURE_CLIENT_ID", "aid")
    monkeypatch.setenv("AZURE_ALLOWED_TENANTS", f"{OUR_TENANT}, {FOREIGN_TENANT}")
    monkeypatch.setenv("AZURE_ALLOW_ANY_TENANT", "false")
    assert env_oauth_providers(("azure",))["azure"] == {
        "client_id": "aid",
        "tenant": "common",
        "allowed_tenants": [OUR_TENANT, FOREIGN_TENANT],
    }


def _generic_provider(rsa_key) -> OAuthProvider:
    jwk = _jwk(rsa_key, "k1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/a",
                "token_endpoint": f"{ISSUER}/t",
                "jwks_uri": f"{ISSUER}/jwks",
                "id_token_signing_alg_values_supported": ["RS256"],
            })
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [jwk]})
        return httpx.Response(404)

    return OAuthProvider(
        client_id=CLIENT_ID,
        name="corp",
        server_metadata_url=f"{ISSUER}/.well-known/openid-configuration",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(("setting", "expected"), [(False, 403), (True, 200)])
async def test_trust_token_roles_setting(db, rsa_key, monkeypatch, setting, expected):
    monkeypatch.setattr(settings, "OAUTH_TRUST_TOKEN_ROLES", setting)
    provider = _generic_provider(rsa_key)
    app = _app(external_validators=[ExternalTokenValidator(provider)])
    token = _sign(rsa_key, "k1", iss=ISSUER, sub="ext-1", roles=["admin"])

    assert (await _get(app, "/admin", token)).status_code == expected


# ---------------------------------------------------------------------------
# Finding: a DB-loaded user's roles come from the database, not the token
# ---------------------------------------------------------------------------


async def _staff_user():
    from zeeb_api.auth.models import User

    user = User(email="ex-admin@example.com", is_staff=True)
    await user.save()
    return user


async def test_demoted_admin_loses_require_auth_roles_at_once(db):
    staff = await _staff_user()
    token = create_access_token(str(staff.id), staff.get_claims())  # roles: staff, admin
    app = _app(external_validators=[])

    assert (await _get(app, "/admin", token)).status_code == 200

    staff.is_staff = False
    await staff.save()

    response = await _get(app, "/admin", token)
    assert response.status_code == 403
    assert (await _get(app, "/whoami", token)).json()["is_admin_user"] is False


async def test_db_user_roles_ignore_the_token_roles_claim(db):
    from zeeb_api.auth.jwt import decode_token
    from zeeb_api.auth.middleware import default_user_loader

    user = await _staff_user()
    user.is_staff = False
    await user.save()
    token = create_access_token(str(user.id), {"roles": ["admin", "billing"]})

    loaded = await default_user_loader(decode_token(token, token_type="access"))
    assert get_user_roles(loaded) == set()


async def test_token_only_user_still_reads_its_roles_claim(db):
    staff = await _staff_user()
    token = create_access_token(str(staff.id), staff.get_claims())
    app = _app(external_validators=[], load_user_from_db=False)

    assert (await _get(app, "/admin", token)).status_code == 200


# ---------------------------------------------------------------------------
# Finding: unknown kids cannot force a JWKS fetch per request
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _JWKSServer:
    def __init__(self, keys: list[dict]) -> None:
        self.keys = keys
        self.fetches = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.fetches += 1
        await asyncio.sleep(0)  # let concurrent callers pile up
        return httpx.Response(200, json={"keys": list(self.keys)})

    def cache(self, clock: _Clock, **kwargs) -> JWKSCache:
        client = OAuth2Client(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        )
        return JWKSCache(client, f"{ISSUER}/jwks", clock=clock, **kwargs)


async def test_random_kids_cause_at_most_one_forced_refetch(rsa_key):
    server = _JWKSServer([_jwk(rsa_key, "real")])
    cache = server.cache(_Clock())

    for i in range(50):
        with pytest.raises(OAuthValidationError):
            await cache.get_signing_key(f"bogus-{i}")
    assert server.fetches <= 2  # the initial fetch + one forced refetch
    assert (await cache.get_signing_key("real")).key_id == "real"
    assert server.fetches <= 2


async def test_key_rotation_is_picked_up_after_the_cooldown(rsa_key):
    clock = _Clock()
    server = _JWKSServer([_jwk(rsa_key, "old")])
    cache = server.cache(clock, refetch_cooldown_seconds=60)
    await cache.get_signing_key("old")
    with pytest.raises(OAuthValidationError):
        await cache.get_signing_key("probe")  # spends the forced refetch
    fetches = server.fetches

    server.keys.append(_jwk(rsa_key, "new"))  # the IdP rotates
    with pytest.raises(OAuthValidationError):
        await cache.get_signing_key("new")  # still inside the cooldown
    assert server.fetches == fetches

    clock.now += 61
    assert (await cache.get_signing_key("new")).key_id == "new"
    assert server.fetches == fetches + 1


async def test_a_kid_confirmed_absent_is_negatively_cached(rsa_key):
    clock = _Clock()
    server = _JWKSServer([_jwk(rsa_key, "real")])
    cache = server.cache(clock, refetch_cooldown_seconds=60, unknown_kid_ttl_seconds=300)
    with pytest.raises(OAuthValidationError):
        await cache.get_signing_key("bogus")
    fetches = server.fetches

    clock.now += 120  # past the cooldown, inside the negative TTL
    with pytest.raises(OAuthValidationError):
        await cache.get_signing_key("bogus")
    assert server.fetches == fetches

    clock.now += 300  # the negative entry expired: one more look
    with pytest.raises(OAuthValidationError):
        await cache.get_signing_key("bogus")
    assert server.fetches == fetches + 1


async def test_negative_cache_is_bounded(rsa_key):
    clock = _Clock()
    server = _JWKSServer([_jwk(rsa_key, "real")])
    cache = server.cache(clock, refetch_cooldown_seconds=0, max_unknown_kids=5)
    for i in range(20):
        with pytest.raises(OAuthValidationError):
            await cache.get_signing_key(f"bogus-{i}")
    assert len(cache._unknown_kids) == 5


async def test_concurrent_fetches_are_coalesced(rsa_key):
    server = _JWKSServer([_jwk(rsa_key, "real")])
    cache = server.cache(_Clock())

    keys = await asyncio.gather(*(cache.get_signing_key("real") for _ in range(20)))
    assert {key.key_id for key in keys} == {"real"}
    assert server.fetches == 1

    results = await asyncio.gather(
        *(cache.get_signing_key(f"bogus-{i}") for i in range(20)), return_exceptions=True
    )
    assert all(isinstance(r, OAuthValidationError) for r in results)
    assert server.fetches == 2


async def test_random_kids_through_the_middleware_do_not_amplify(rsa_key):
    provider = _generic_provider(rsa_key)
    fetches = {"n": 0}
    original = provider.client.fetch_jwks

    async def counting_fetch(uri):
        fetches["n"] += 1
        return await original(uri)

    provider.client.fetch_jwks = counting_fetch  # type: ignore[method-assign]
    app = _app(
        external_validators=[ExternalTokenValidator(provider)], load_user_from_db=False
    )
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    async with _client(app) as client:
        for i in range(50):
            token = _sign(attacker, f"kid-{i}", iss=ISSUER, sub="x")
            response = await client.get(
                "/whoami", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.json()["id"] is None
    assert fetches["n"] <= 2


def test_provider_reads_the_cooldown_setting(monkeypatch, rsa_key):
    monkeypatch.setattr(settings, "OAUTH_JWKS_REFETCH_COOLDOWN_SECONDS", 5)
    assert _generic_provider(rsa_key)._resolved_jwks_refetch_cooldown() == 5.0
    provider = _generic_provider(rsa_key)
    provider.jwks_refetch_cooldown_seconds = 120
    assert provider._resolved_jwks_refetch_cooldown() == 120.0


# ---------------------------------------------------------------------------
# Finding: login timing must not reveal passwordless (OAuth-only) accounts
# ---------------------------------------------------------------------------


@pytest.fixture
def dummy_checks(monkeypatch):
    from zeeb_api.auth import hashers

    calls: list[str] = []
    monkeypatch.setattr(hashers, "dummy_password_check", calls.append)
    return calls


@pytest.mark.parametrize("stored", [None, "", "not-a-bcrypt-hash"])
async def test_passwordless_account_runs_the_dummy_check(db, dummy_checks, stored):
    from zeeb_api.auth.backends import authenticate
    from zeeb_api.auth.models import User

    user = User(email="sso-only@example.com")
    user.password = stored
    await user.save()

    assert await authenticate(email="sso-only@example.com", password="guess") is None
    assert dummy_checks == ["guess"]


async def test_missing_account_runs_the_dummy_check(db, dummy_checks):
    from zeeb_api.auth.backends import authenticate

    assert await authenticate(email="nobody@example.com", password="guess") is None
    assert dummy_checks == ["guess"]


def test_check_password_against_an_unusable_hash_runs_the_dummy_check(dummy_checks):
    from zeeb_api.auth.hashers import check_password

    assert check_password("guess", "") is False
    assert check_password("guess", None) is False  # type: ignore[arg-type]
    assert dummy_checks == ["guess", "guess"]
