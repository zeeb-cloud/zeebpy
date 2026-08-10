"""Tests for the OAuth2/OIDC authentication layer (zeeb_api.auth.oauth)."""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request

import zeeb_api.auth.jwt as jwt_module
from zeeb_api.auth.jwt import configure_jwt, decode_token
from zeeb_api.auth.middleware import JWTAuthMiddleware
from zeeb_api.auth.oauth import (
    AzureADProvider,
    ExternalTokenValidator,
    GitHubProvider,
    JWKSCache,
    OAuth2Client,
    OAuthExchangeError,
    OAuthProvider,
    OAuthValidationError,
    build_providers_from_settings,
    create_oauth_router,
    create_state_token,
    decode_state_token,
    generate_nonce,
    generate_pkce_pair,
    validate_id_token,
)
from zeeb_api.auth.oauth.registry import clear_provider_cache
from zeeb_api.conf import settings
from zeeb_api.exceptions import AuthenticationException, ErrorCode, ImproperlyConfigured

ISSUER = "https://idp.example.com"
CLIENT_ID = "zeeb-client-id"
CLIENT_SECRET = "zeeb-client-secret"
KID = "test-key"
TEST_SECRET = "a-real-strong-test-secret-key-with-more-than-32-bytes"

# RSA fixtures (module-scoped: key generation is expensive)


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_rsa_key():
    """A second key for bad-signature tests."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _private_pem(key) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _jwk(key, kid: str) -> dict:
    jwk = pyjwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


@pytest.fixture(scope="module")
def jwks(rsa_key):
    return {"keys": [_jwk(rsa_key, KID)]}


def make_id_token(claims: dict, key, *, kid: str = KID, alg: str = "RS256") -> str:
    """Sign an RS256 ID token with sane defaults (override via claims)."""
    now = datetime.now(timezone.utc)
    payload = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "subj-1",
        "exp": now + timedelta(minutes=10),
        "iat": now,
        **claims,
    }
    return pyjwt.encode(payload, _private_pem(key), algorithm=alg, headers={"kid": kid})


# Fake IdP (httpx.MockTransport)


class FakeIdP:
    """In-memory IdP: discovery, /token, /jwks, /userinfo."""

    def __init__(self, rsa_key, jwks):
        self.rsa_key = rsa_key
        self.jwks = jwks
        self.nonce: str | None = None
        self.token_status = 200
        self.token_requests: list[dict] = []
        self.jwks_fetches = 0
        self.id_token_claims: dict = {}
        self.userinfo = {"sub": "subj-1", "email": "alice@example.com", "name": "Alice Example"}

    @property
    def discovery(self) -> dict:
        return {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "userinfo_endpoint": f"{ISSUER}/userinfo",
            "jwks_uri": f"{ISSUER}/jwks",
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery)
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            claims = {
                "sub": "subj-1",
                "email": "alice@example.com",
                "name": "Alice Example",
                "email_verified": True,
                **self.id_token_claims,
            }
            if self.nonce is not None:
                claims["nonce"] = self.nonce
            return httpx.Response(
                200,
                json={
                    "access_token": "at-123",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "refresh_token": "rt-123",
                    "id_token": make_id_token(claims, self.rsa_key),
                },
            )
        if path == "/jwks":
            self.jwks_fetches += 1
            return httpx.Response(200, json=self.jwks)
        if path == "/userinfo":
            return httpx.Response(200, json=self.userinfo)
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def idp(rsa_key, jwks):
    return FakeIdP(rsa_key, jwks)


@pytest.fixture
def mock_http(idp):
    return httpx.AsyncClient(transport=httpx.MockTransport(idp.handler))


@pytest.fixture
def oauth_client(mock_http):
    return OAuth2Client(http_client=mock_http)


def make_provider(mock_http, **kwargs) -> OAuthProvider:
    kwargs.setdefault("client_id", CLIENT_ID)
    kwargs.setdefault("client_secret", CLIENT_SECRET)
    kwargs.setdefault("name", "test")
    kwargs.setdefault(
        "server_metadata_url", f"{ISSUER}/.well-known/openid-configuration"
    )
    return OAuthProvider(http_client=mock_http, **kwargs)


@pytest.fixture
def provider(mock_http):
    return make_provider(mock_http)


# Global state isolation (pattern from tests/test_jwt_security.py)

_OAUTH_SETTINGS = (
    "OAUTH_PROVIDERS",
    "OAUTH_AUTO_CREATE_USERS",
    "OAUTH_LINK_BY_EMAIL",
    "OAUTH_STATE_TTL_SECONDS",
    "OAUTH_REDIRECT_URI",
    "OAUTH_SUCCESS_REDIRECT",
    "OAUTH_ALLOWED_REDIRECT_HOSTS",
    "OAUTH_ACCEPT_EXTERNAL_TOKENS",
)


@pytest.fixture(autouse=True)
def reset_global_state():
    """Save/restore JWT config and OAuth settings; install a secure secret."""
    saved_config = jwt_module._jwt_config
    saved_settings = {name: getattr(settings, name) for name in _OAUTH_SETTINGS}

    configure_jwt(secret_key=TEST_SECRET)  # satisfy the insecure-secret guard
    clear_provider_cache()

    yield

    jwt_module._jwt_config = saved_config
    for name, value in saved_settings.items():
        setattr(settings, name, value)
    clear_provider_cache()


# Unit tests: client


class TestOAuth2Client:
    async def test_fetch_metadata(self, oauth_client):
        metadata = await oauth_client.fetch_metadata(
            f"{ISSUER}/.well-known/openid-configuration"
        )
        assert metadata.issuer == ISSUER
        assert metadata.authorization_endpoint == f"{ISSUER}/authorize"
        assert metadata.token_endpoint == f"{ISSUER}/token"
        assert metadata.jwks_uri == f"{ISSUER}/jwks"
        assert metadata.id_token_signing_alg_values_supported == ["RS256"]

    async def test_exchange_code_sends_form_body(self, oauth_client, idp):
        tokens = await oauth_client.exchange_code(
            f"{ISSUER}/token",
            code="the-code",
            redirect_uri="https://app/cb",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            code_verifier="the-verifier",
        )
        assert tokens.access_token == "at-123"
        assert tokens.refresh_token == "rt-123"
        form = idp.token_requests[-1]
        assert form["grant_type"] == "authorization_code"
        assert form["code"] == "the-code"
        assert form["redirect_uri"] == "https://app/cb"
        assert form["client_id"] == CLIENT_ID
        assert form["client_secret"] == CLIENT_SECRET
        assert form["code_verifier"] == "the-verifier"

    async def test_exchange_code_error_raises(self, oauth_client, idp):
        idp.token_status = 400
        with pytest.raises(OAuthExchangeError):
            await oauth_client.exchange_code(
                f"{ISSUER}/token",
                code="bad",
                redirect_uri="https://app/cb",
                client_id=CLIENT_ID,
            )

    async def test_refresh_token_grant(self, oauth_client, idp):
        tokens = await oauth_client.refresh_token(
            f"{ISSUER}/token",
            refresh_token="rt-old",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        assert tokens.access_token == "at-123"
        form = idp.token_requests[-1]
        assert form["grant_type"] == "refresh_token"
        assert form["refresh_token"] == "rt-old"


# Unit tests: JWKS + ID token validation


class TestJWKSValidation:
    @pytest.fixture
    def cache(self, oauth_client):
        return JWKSCache(oauth_client, f"{ISSUER}/jwks")

    async def test_happy_path(self, cache, rsa_key):
        nonce = generate_nonce()
        token = make_id_token({"nonce": nonce}, rsa_key)
        claims = await validate_id_token(
            token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER, nonce=nonce
        )
        assert claims["sub"] == "subj-1"

    async def test_jwks_cached_between_calls(self, cache, rsa_key, idp):
        token = make_id_token({}, rsa_key)
        await validate_id_token(token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER)
        await validate_id_token(token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER)
        assert idp.jwks_fetches == 1

    async def test_bad_signature(self, cache, other_rsa_key):
        token = make_id_token({}, other_rsa_key)  # wrong key, same kid
        with pytest.raises(OAuthValidationError):
            await validate_id_token(token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER)

    async def test_wrong_audience(self, cache, rsa_key):
        token = make_id_token({"aud": "someone-else"}, rsa_key)
        with pytest.raises(OAuthValidationError):
            await validate_id_token(token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER)

    async def test_expired(self, cache, rsa_key):
        token = make_id_token(
            {"exp": datetime.now(timezone.utc) - timedelta(minutes=5)}, rsa_key
        )
        with pytest.raises(OAuthValidationError):
            await validate_id_token(token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER)

    async def test_wrong_issuer(self, cache, rsa_key):
        token = make_id_token({"iss": "https://evil.example.com"}, rsa_key)
        with pytest.raises(OAuthValidationError):
            await validate_id_token(token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER)

    async def test_wrong_nonce(self, cache, rsa_key):
        token = make_id_token({"nonce": "expected"}, rsa_key)
        with pytest.raises(OAuthValidationError):
            await validate_id_token(
                token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER, nonce="different"
            )

    async def test_unknown_kid_forces_one_refetch(self, cache, rsa_key, idp):
        # Warm the cache.
        await validate_id_token(
            make_id_token({}, rsa_key), jwks=cache, client_id=CLIENT_ID, issuer=ISSUER
        )
        assert idp.jwks_fetches == 1
        token = make_id_token({}, rsa_key, kid="rolled-over-kid")
        with pytest.raises(OAuthValidationError):
            await validate_id_token(token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER)
        assert idp.jwks_fetches == 2  # exactly ONE forced refetch

    async def test_hs256_token_rejected(self, cache):
        token = pyjwt.encode(
            {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x"},
            "hmac-secret",
            algorithm="HS256",
            headers={"kid": KID},
        )
        with pytest.raises(OAuthValidationError):
            await validate_id_token(token, jwks=cache, client_id=CLIENT_ID, issuer=ISSUER)

    async def test_hs_and_none_never_allowed_even_if_listed(self, cache, rsa_key):
        token = make_id_token({}, rsa_key)
        for alg in ("HS256", "none"):
            with pytest.raises(OAuthValidationError):
                await validate_id_token(
                    token,
                    jwks=cache,
                    client_id=CLIENT_ID,
                    issuer=ISSUER,
                    allowed_algs=[alg],
                )


# Unit tests: claim mapping


class TestClaimMapping:
    def test_default_mapping(self, provider):
        claims = provider.map_claims(
            {"sub": "abc", "email": "a@b.c", "name": "A B", "email_verified": True}
        )
        assert claims.subject == "abc"
        assert claims.email == "a@b.c"
        assert claims.name == "A B"
        assert claims.email_verified is True
        assert claims.raw["sub"] == "abc"

    def test_dotted_path_lookup(self, mock_http):
        p = make_provider(
            mock_http,
            claim_mapping={"subject": "user.id", "email": "user.contact.email"},
        )
        claims = p.map_claims(
            {"user": {"id": 42, "contact": {"email": "deep@example.com"}}}
        )
        assert claims.subject == "42"
        assert claims.email == "deep@example.com"

    def test_missing_subject_raises(self, provider):
        with pytest.raises(OAuthValidationError):
            provider.map_claims({"email": "a@b.c"})


# Unit tests: Azure AD preset


class TestAzureADProvider:
    GUID = "11111111-2222-3333-4444-555555555555"

    def test_authority_url_per_tenant(self):
        for tenant in ("common", "organizations", "consumers", self.GUID):
            p = AzureADProvider(tenant=tenant, client_id=CLIENT_ID)
            assert p.server_metadata_url == (
                f"https://login.microsoftonline.com/{tenant}/v2.0/"
                ".well-known/openid-configuration"
            )

    def test_default_scopes_include_offline_access(self):
        p = AzureADProvider(client_id=CLIENT_ID)
        assert p.scopes == ["openid", "profile", "email", "offline_access"]

    def test_concrete_tenant_issuer_exact_match(self):
        p = AzureADProvider(tenant=self.GUID, client_id=CLIENT_ID)
        assert p._issuer_validator(
            f"https://login.microsoftonline.com/{self.GUID}/v2.0"
        )
        other = "99999999-8888-7777-6666-555555555555"
        assert not p._issuer_validator(
            f"https://login.microsoftonline.com/{other}/v2.0"
        )

    def test_multi_tenant_issuer_regex(self):
        p = AzureADProvider(tenant="common", client_id=CLIENT_ID)
        assert p._issuer_validator(
            f"https://login.microsoftonline.com/{self.GUID}/v2.0"
        )
        # Foreign hosts and lookalikes are rejected.
        assert not p._issuer_validator(
            f"https://evil.example.com/{self.GUID}/v2.0"
        )
        assert not p._issuer_validator(
            f"https://login.microsoftonline.com.evil.com/{self.GUID}/v2.0"
        )
        assert not p._issuer_validator("https://login.microsoftonline.com/common/v2.0")

    def test_map_claims_prefers_oid_and_preferred_username(self):
        p = AzureADProvider(client_id=CLIENT_ID)
        claims = p.map_claims(
            {
                "oid": "oid-123",
                "sub": "pairwise-sub",
                "preferred_username": "alice@contoso.com",
                "name": "Alice",
            }
        )
        assert claims.subject == "oid-123"
        assert claims.email == "alice@contoso.com"

        claims = p.map_claims({"sub": "pairwise-sub", "email": "a@b.c"})
        assert claims.subject == "pairwise-sub"
        assert claims.email == "a@b.c"


# Unit tests: GitHub preset


class TestGitHubProvider:
    async def test_get_claims_falls_back_to_emails_endpoint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/user":
                assert request.headers["Authorization"] == "Bearer gh-token"
                return httpx.Response(
                    200, json={"id": 99, "login": "octocat", "email": None, "name": None}
                )
            if request.url.path == "/user/emails":
                return httpx.Response(
                    200,
                    json=[
                        {"email": "other@example.com", "primary": False, "verified": True},
                        {"email": "octo@example.com", "primary": True, "verified": True},
                    ],
                )
            return httpx.Response(404)

        p = GitHubProvider(
            client_id=CLIENT_ID,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        assert p.validate_id_token is False
        from zeeb_api.auth.oauth import OAuthTokenSet

        claims = await p.get_claims(OAuthTokenSet(access_token="gh-token"))
        assert claims.subject == "99"
        assert claims.email == "octo@example.com"
        assert claims.name == "octocat"
        # The entry was selected *because* it is verified — say so.
        assert claims.email_verified is True

    @staticmethod
    def _provider(user_json, emails_response):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/user":
                return httpx.Response(200, json=user_json)
            if request.url.path == "/user/emails":
                return emails_response
            return httpx.Response(404)

        return GitHubProvider(
            client_id=CLIENT_ID,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    async def _claims(self, user_json, emails_response):
        from zeeb_api.auth.oauth import OAuthTokenSet

        p = self._provider(user_json, emails_response)
        return await p.get_claims(OAuthTokenSet(access_token="gh-token"))

    async def test_public_email_is_marked_verified(self):
        """A public primary address on /user still needs /user/emails.

        /user carries no verification flag, so without this lookup
        email_verified stays None and OAUTH_REQUIRE_VERIFIED_EMAIL (default
        True) rejects every first login with a 401.
        """
        claims = await self._claims(
            {"id": 99, "login": "octocat", "email": "octo@example.com", "name": None},
            httpx.Response(
                200,
                json=[{"email": "octo@example.com", "primary": True, "verified": True}],
            ),
        )
        assert claims.email == "octo@example.com"
        assert claims.email_verified is True

    async def test_public_email_unverified_is_reported(self):
        claims = await self._claims(
            {"id": 99, "login": "octocat", "email": "octo@example.com"},
            httpx.Response(
                200,
                json=[{"email": "octo@example.com", "primary": True, "verified": False}],
            ),
        )
        assert claims.email_verified is False

    async def test_email_match_is_case_insensitive(self):
        claims = await self._claims(
            {"id": 99, "login": "octocat", "email": "Octo@Example.com"},
            httpx.Response(
                200,
                json=[{"email": "octo@example.com", "primary": True, "verified": True}],
            ),
        )
        assert claims.email_verified is True

    async def test_emails_endpoint_denied_leaves_verification_unknown(self):
        """A token without the user:email scope must not break the login."""
        claims = await self._claims(
            {"id": 99, "login": "octocat", "email": "octo@example.com"},
            httpx.Response(403, json={"message": "Requires user:email"}),
        )
        assert claims.email == "octo@example.com"
        assert claims.email_verified is None

    async def test_unknown_address_leaves_verification_unknown(self):
        claims = await self._claims(
            {"id": 99, "login": "octocat", "email": "octo@example.com"},
            httpx.Response(
                200,
                json=[{"email": "other@example.com", "primary": True, "verified": True}],
            ),
        )
        assert claims.email_verified is None


# Unit tests: state + PKCE


class TestStateAndPKCE:
    def test_state_round_trip(self):
        nonce = generate_nonce()
        token = create_state_token(
            "azure", nonce=nonce, redirect_uri="https://app/cb", next_url="/dash"
        )
        claims = decode_state_token(token, "azure")
        assert claims["nonce"] == nonce
        assert claims["redirect_uri"] == "https://app/cb"
        assert claims["next"] == "/dash"

    def test_state_wrong_provider(self):
        token = create_state_token("azure", nonce="n", redirect_uri="https://app/cb")
        with pytest.raises(AuthenticationException) as exc_info:
            decode_state_token(token, "google")
        assert exc_info.value.code == ErrorCode.AUTH_OAUTH_STATE_INVALID.value

    def test_state_expired(self):
        settings.OAUTH_STATE_TTL_SECONDS = -10
        token = create_state_token("azure", nonce="n", redirect_uri="https://app/cb")
        with pytest.raises(AuthenticationException) as exc_info:
            decode_state_token(token, "azure")
        assert exc_info.value.code == ErrorCode.AUTH_OAUTH_STATE_INVALID.value

    def test_state_tampered(self):
        token = create_state_token("azure", nonce="n", redirect_uri="https://app/cb")
        tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        with pytest.raises(AuthenticationException):
            decode_state_token(tampered, "azure")

    def test_pkce_s256_correctness(self):
        verifier, challenge = generate_pkce_pair()
        assert 43 <= len(verifier) <= 128
        assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected
        assert "=" not in challenge

    def test_pkce_pairs_unique(self):
        assert generate_pkce_pair() != generate_pkce_pair()


class TestSafeNext:
    """Unit tests for the `next` redirect-target validator."""

    @pytest.mark.parametrize(
        "url",
        ["/dashboard", "/a/b/c?x=1#frag", "/"],
    )
    def test_relative_paths_allowed(self, url):
        from zeeb_api.auth.oauth.router import _is_safe_next

        assert _is_safe_next(url, set()) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/grab",      # absolute, not allowlisted
            "//evil.example/grab",            # protocol-relative
            "http://evil.example",            # absolute, not allowlisted
            "javascript:alert(1)",            # non-http scheme
            "\\\\evil.example/x",             # backslashes normalized to //
            "/path\nwith-newline",            # control char
            "",                                # empty
            "relative-without-slash",         # not a rooted relative path
        ],
    )
    def test_unsafe_targets_rejected(self, url):
        from zeeb_api.auth.oauth.router import _is_safe_next

        assert _is_safe_next(url, {"app.example.com"}) is False

    def test_absolute_allowlisted_host_allowed(self):
        from zeeb_api.auth.oauth.router import _is_safe_next

        assert _is_safe_next("https://app.example.com/done", {"app.example.com"}) is True
        # Wrong scheme still rejected even for an allowlisted host.
        assert _is_safe_next("ftp://app.example.com/x", {"app.example.com"}) is False


# Unit tests: registry


class TestRegistry:
    def test_infers_known_provider_classes(self):
        settings.OAUTH_PROVIDERS = {
            "azure": {"tenant": "common", "client_id": "cid-a"},
            "github": {"client_id": "cid-g"},
        }
        providers = build_providers_from_settings()
        assert isinstance(providers["azure"], AzureADProvider)
        assert providers["azure"].tenant == "common"
        assert providers["azure"].name == "azure"
        assert isinstance(providers["github"], GitHubProvider)

    def test_explicit_class_path(self):
        settings.OAUTH_PROVIDERS = {
            "corp": {
                "class": "zeeb_api.auth.oauth.provider.OAuthProvider",
                "client_id": "cid",
                "authorization_endpoint": "https://corp/auth",
                "token_endpoint": "https://corp/token",
            }
        }
        providers = build_providers_from_settings()
        assert type(providers["corp"]) is OAuthProvider
        assert providers["corp"].name == "corp"

    def test_unknown_name_without_class_raises(self):
        settings.OAUTH_PROVIDERS = {"mystery": {"client_id": "cid"}}
        with pytest.raises(ImproperlyConfigured):
            build_providers_from_settings()


# Integration tests (aiosqlite in-memory)


def _get_models():
    from zeeb_api.auth.models import Permission, User, UserPermission
    from zeeb_api.auth.oauth.models import ExternalIdentity

    return (User, Permission, UserPermission, ExternalIdentity)


@pytest.fixture
async def db():
    """In-memory DB (pattern from tests/test_related_lookups.py)."""
    from zeeb_orm import close_all_connections, configure, setup_database
    from zeeb_orm.conf.settings import Settings
    from zeeb_orm.models.base import metadata

    models = _get_models()
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


def make_app(provider, *, external_validators=None, **router_kwargs):
    from zeeb_api.exception_handlers import install_exception_handlers

    app = FastAPI()
    install_exception_handlers(app)
    app.add_middleware(
        JWTAuthMiddleware,
        external_validators=external_validators if external_validators is not None else [],
    )
    app.include_router(create_oauth_router(providers={"test": provider}, **router_kwargs))

    @app.get("/whoami/")
    async def whoami(request: Request):
        user = request.state.user
        return {
            "id": str(user.id) if user is not None else None,
            "authenticated": bool(getattr(user, "is_authenticated", False)),
        }

    return app


def asgi_client(app) -> httpx.AsyncClient:
    # https base_url so the Secure PKCE cookie is stored and replayed.
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://testserver"
    )


SPA_BODY = {"code": "spa-code", "redirect_uri": "https://spa.example.com/cb"}


class TestSPATokenFlow:
    """Flow A: POST /{provider}/token/ (SPA exchanges the code itself)."""

    async def test_full_login_creates_user_and_identity(self, db, provider, idp):
        from zeeb_api.auth.models import User
        from zeeb_api.auth.oauth.models import ExternalIdentity

        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.post(
                "/auth/test/token/", json={**SPA_BODY, "code_verifier": "spa-verifier"}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == jwt_module.get_jwt_config().access_token_expire_minutes * 60

        # The code exchange hit the IdP with the right form fields.
        form = idp.token_requests[-1]
        assert form["grant_type"] == "authorization_code"
        assert form["code"] == "spa-code"
        assert form["code_verifier"] == "spa-verifier"
        assert form["client_secret"] == CLIENT_SECRET

        # Locally-issued access token decodes and points at the new user.
        user = await User.objects.filter(email="alice@example.com").first()
        assert user is not None
        assert not user.has_usable_password()
        assert user.date_joined is not None
        payload = decode_token(body["access_token"], token_type="access")
        assert payload.sub == str(user.id)
        assert payload.claims["email"] == "alice@example.com"
        refresh = decode_token(body["refresh_token"], token_type="refresh")
        assert refresh.sub == str(user.id)

        identity = await ExternalIdentity.objects.filter(
            provider="test", subject="subj-1"
        ).first()
        assert identity is not None
        assert identity.user_id == user.id
        assert identity.email == "alice@example.com"

    async def test_second_login_no_duplicates(self, db, provider):
        from zeeb_api.auth.models import User
        from zeeb_api.auth.oauth.models import ExternalIdentity

        app = make_app(provider)
        async with asgi_client(app) as client:
            first = await client.post("/auth/test/token/", json=SPA_BODY)
            second = await client.post("/auth/test/token/", json=SPA_BODY)
        assert first.status_code == 200
        assert second.status_code == 200

        users = await User.objects.filter(email="alice@example.com")
        assert len(users) == 1
        identities = await ExternalIdentity.objects.filter(provider="test")
        assert len(identities) == 1
        assert identities[0].last_login_at is not None

    async def test_auto_create_disabled_returns_401(self, db, provider):
        settings.OAUTH_AUTO_CREATE_USERS = False
        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.post("/auth/test/token/", json=SPA_BODY)
        assert response.status_code == 401
        assert (
            response.json()["error"]["code"]
            == ErrorCode.AUTH_OAUTH_USER_NOT_PROVISIONED.value
        )

    async def test_link_by_email_attaches_to_existing_user(self, db, provider):
        from zeeb_api.auth.backends import create_user
        from zeeb_api.auth.oauth.models import ExternalIdentity

        existing = await create_user(email="alice@example.com", password="local-pass-123")
        settings.OAUTH_AUTO_CREATE_USERS = False  # force the linking path

        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.post("/auth/test/token/", json=SPA_BODY)
        assert response.status_code == 200
        payload = decode_token(response.json()["access_token"], token_type="access")
        assert payload.sub == str(existing.id)

        identity = await ExternalIdentity.objects.filter(
            provider="test", subject="subj-1"
        ).first()
        assert identity is not None
        assert identity.user_id == existing.id

    async def test_unverified_email_does_not_link_to_existing_user(self, db, provider, idp):
        """An unverified IdP email must not take over an existing local account."""
        from zeeb_api.auth.backends import create_user
        from zeeb_api.auth.oauth.models import ExternalIdentity

        existing = await create_user(email="alice@example.com", password="local-pass-123")
        settings.OAUTH_AUTO_CREATE_USERS = False  # force the linking path
        idp.id_token_claims = {"email_verified": False}

        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.post("/auth/test/token/", json=SPA_BODY)

        assert response.status_code == 401
        assert (
            response.json()["error"]["code"]
            == ErrorCode.AUTH_OAUTH_EMAIL_UNVERIFIED.value
        )
        # No identity was attached to the victim account.
        identity = await ExternalIdentity.objects.filter(
            provider="test", subject="subj-1"
        ).first()
        assert identity is None

    async def test_unverified_email_does_not_auto_create(self, db, provider, idp):
        """Auto-provisioning must refuse an unverified email (email absent = unverified)."""
        from zeeb_api.auth.models import User

        idp.id_token_claims = {"email_verified": False}
        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.post("/auth/test/token/", json=SPA_BODY)

        assert response.status_code == 401
        assert (
            response.json()["error"]["code"]
            == ErrorCode.AUTH_OAUTH_EMAIL_UNVERIFIED.value
        )
        assert await User.objects.filter(email="alice@example.com").first() is None

    async def test_require_verified_email_disabled_allows_unverified(self, db, provider, idp):
        """A provider that opts out (trusted IdP) may link unverified emails."""
        from zeeb_api.auth.oauth.models import ExternalIdentity

        provider.require_verified_email = False
        idp.id_token_claims = {"email_verified": False}
        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.post("/auth/test/token/", json=SPA_BODY)

        assert response.status_code == 200
        identity = await ExternalIdentity.objects.filter(
            provider="test", subject="subj-1"
        ).first()
        assert identity is not None

    async def test_exchange_failure_returns_401(self, db, provider, idp):
        idp.token_status = 400
        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.post("/auth/test/token/", json=SPA_BODY)
        assert response.status_code == 401
        assert (
            response.json()["error"]["code"]
            == ErrorCode.AUTH_OAUTH_EXCHANGE_FAILED.value
        )

    async def test_unknown_provider_404(self, db, provider):
        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.post("/auth/nope/token/", json=SPA_BODY)
        assert response.status_code == 404
        assert (
            response.json()["error"]["code"]
            == ErrorCode.AUTH_OAUTH_PROVIDER_NOT_FOUND.value
        )


class TestBrowserFlow:
    """Flow B: GET authorize -> IdP -> GET callback (with PKCE cookie + state)."""

    async def test_authorize_redirects_with_pkce_and_state(self, db, provider):
        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.get("/auth/test/authorize/")
        assert response.status_code == 307
        location = response.headers["location"]
        assert location.startswith(f"{ISSUER}/authorize?")
        params = {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}
        assert params["response_type"] == "code"
        assert params["client_id"] == CLIENT_ID
        assert params["code_challenge_method"] == "S256"
        assert params["code_challenge"]
        assert params["nonce"]
        assert params["redirect_uri"] == "https://testserver/auth/test/callback/"
        # State decodes for this provider and carries the nonce.
        state_claims = decode_state_token(params["state"], "test")
        assert state_claims["nonce"] == params["nonce"]
        # PKCE verifier cookie was set (HttpOnly, never inside the state JWT).
        set_cookie = response.headers["set-cookie"]
        assert "zeeb_oauth_pkce_test=" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert params["state"] not in set_cookie

    async def test_callback_completes_login(self, db, provider, idp):
        from zeeb_api.auth.models import User

        app = make_app(provider)
        async with asgi_client(app) as client:
            authorize = await client.get("/auth/test/authorize/")
            params = {
                k: v[0]
                for k, v in parse_qs(urlparse(authorize.headers["location"]).query).items()
            }
            # The fake IdP signs the next id_token with the expected nonce.
            idp.nonce = params["nonce"]
            verifier = client.cookies.get("zeeb_oauth_pkce_test")
            assert verifier

            callback = await client.get(
                "/auth/test/callback/",
                params={"code": "browser-code", "state": params["state"]},
            )
        assert callback.status_code == 200, callback.text
        body = callback.json()
        user = await User.objects.filter(email="alice@example.com").first()
        assert user is not None
        assert decode_token(body["access_token"], token_type="access").sub == str(user.id)
        # The PKCE verifier from the cookie reached the token endpoint.
        assert idp.token_requests[-1]["code_verifier"] == verifier

    async def test_callback_form_post(self, db, provider, idp):
        """Azure AD response_mode=form_post delivers code+state via POST form."""
        app = make_app(provider)
        async with asgi_client(app) as client:
            authorize = await client.get("/auth/test/authorize/")
            params = {
                k: v[0]
                for k, v in parse_qs(urlparse(authorize.headers["location"]).query).items()
            }
            idp.nonce = params["nonce"]
            callback = await client.post(
                "/auth/test/callback/",
                data={"code": "browser-code", "state": params["state"]},
            )
        assert callback.status_code == 200, callback.text
        assert "access_token" in callback.json()

    async def test_tampered_state_returns_401(self, db, provider):
        app = make_app(provider)
        async with asgi_client(app) as client:
            authorize = await client.get("/auth/test/authorize/")
            params = {
                k: v[0]
                for k, v in parse_qs(urlparse(authorize.headers["location"]).query).items()
            }
            bad_state = params["state"][:-4] + (
                "AAAA" if not params["state"].endswith("AAAA") else "BBBB"
            )
            callback = await client.get(
                "/auth/test/callback/", params={"code": "c", "state": bad_state}
            )
        assert callback.status_code == 401
        assert (
            callback.json()["error"]["code"] == ErrorCode.AUTH_OAUTH_STATE_INVALID.value
        )

    async def test_success_redirect_puts_tokens_in_fragment(self, db, provider, idp):
        app = make_app(provider, success_redirect="https://app.example.com/done")
        async with asgi_client(app) as client:
            authorize = await client.get("/auth/test/authorize/")
            params = {
                k: v[0]
                for k, v in parse_qs(urlparse(authorize.headers["location"]).query).items()
            }
            idp.nonce = params["nonce"]
            callback = await client.get(
                "/auth/test/callback/",
                params={"code": "c", "state": params["state"]},
            )
        assert callback.status_code == 303
        location = callback.headers["location"]
        assert location.startswith("https://app.example.com/done#")
        assert "access_token=" in location.split("#", 1)[1]

    async def _callback_with_next(self, client, idp, next_value):
        authorize = await client.get("/auth/test/authorize/", params={"next": next_value})
        params = {
            k: v[0]
            for k, v in parse_qs(urlparse(authorize.headers["location"]).query).items()
        }
        idp.nonce = params["nonce"]
        return await client.get(
            "/auth/test/callback/",
            params={"code": "c", "state": params["state"]},
        )

    async def test_unsafe_next_does_not_leak_tokens(self, db, provider, idp):
        """An attacker-controlled `next` host is ignored (no token redirect)."""
        # No success_redirect configured -> falls back to JSON, not the evil host.
        app = make_app(provider)
        async with asgi_client(app) as client:
            callback = await self._callback_with_next(
                client, idp, "https://evil.example/grab"
            )
        assert callback.status_code == 200
        assert "evil.example" not in callback.headers.get("location", "")
        assert "access_token" in callback.json()

    async def test_unsafe_next_falls_back_to_configured_redirect(self, db, provider, idp):
        app = make_app(provider, success_redirect="https://app.example.com/done")
        async with asgi_client(app) as client:
            callback = await self._callback_with_next(
                client, idp, "https://evil.example/grab"
            )
        assert callback.status_code == 303
        location = callback.headers["location"]
        assert location.startswith("https://app.example.com/done#")
        assert "evil.example" not in location

    async def test_relative_next_is_honored(self, db, provider, idp):
        app = make_app(provider)
        async with asgi_client(app) as client:
            callback = await self._callback_with_next(client, idp, "/dashboard")
        assert callback.status_code == 303
        location = callback.headers["location"]
        assert location.startswith("/dashboard#")
        assert "access_token=" in location.split("#", 1)[1]

    async def test_allowlisted_absolute_next_is_honored(self, db, provider, idp):
        settings.OAUTH_ALLOWED_REDIRECT_HOSTS = ["app.example.com"]
        app = make_app(provider)
        async with asgi_client(app) as client:
            callback = await self._callback_with_next(
                client, idp, "https://app.example.com/done"
            )
        assert callback.status_code == 303
        assert callback.headers["location"].startswith("https://app.example.com/done#")

    async def test_providers_listing(self, db, provider):
        app = make_app(provider)
        async with asgi_client(app) as client:
            response = await client.get("/auth/providers/")
        assert response.status_code == 200
        listing = response.json()
        assert listing == [
            {
                "name": "test",
                "authorize_url": "https://testserver/auth/test/authorize/",
            }
        ]


class TestExternalBearer:
    """External bearer mode: middleware accepts IdP-issued RS256 tokens."""

    async def test_external_token_sets_request_user(self, db, provider, rsa_key):
        validator = ExternalTokenValidator(provider)
        app = make_app(provider, external_validators=[validator])
        token = make_id_token({"sub": "subj-1", "email": "alice@example.com"}, rsa_key)
        async with asgi_client(app) as client:
            response = await client.get(
                "/whoami/", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["authenticated"] is True
        assert body["id"] == "subj-1"  # no linked identity -> lightweight user

    async def test_external_token_resolves_linked_db_user(self, db, provider, rsa_key):
        from zeeb_api.auth.backends import create_user
        from zeeb_api.auth.oauth.models import ExternalIdentity

        user = await create_user(email="alice@example.com", password="local-pass-123")
        identity = ExternalIdentity(
            user_id=user.id, provider="test", subject="subj-1", email=user.email
        )
        await identity.save()

        validator = ExternalTokenValidator(provider)
        app = make_app(provider, external_validators=[validator])
        token = make_id_token({"sub": "subj-1"}, rsa_key)
        async with asgi_client(app) as client:
            response = await client.get(
                "/whoami/", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.json()["id"] == str(user.id)

    async def test_garbage_token_leaves_user_none(self, db, provider):
        validator = ExternalTokenValidator(provider)
        app = make_app(provider, external_validators=[validator])
        async with asgi_client(app) as client:
            response = await client.get(
                "/whoami/", headers={"Authorization": "Bearer not.a.jwt"}
            )
        assert response.status_code == 200
        assert response.json()["id"] is None

    async def test_wrong_audience_external_token_rejected(self, db, provider, rsa_key):
        validator = ExternalTokenValidator(provider)
        app = make_app(provider, external_validators=[validator])
        token = make_id_token({"aud": "other-app"}, rsa_key)
        async with asgi_client(app) as client:
            response = await client.get(
                "/whoami/", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.json()["id"] is None

    async def test_local_tokens_still_work(self, db, provider):
        from zeeb_api.auth.backends import create_user
        from zeeb_api.auth.jwt import create_access_token

        user = await create_user(email="local@example.com", password="local-pass-123")
        validator = ExternalTokenValidator(provider)
        app = make_app(provider, external_validators=[validator])
        token = create_access_token(str(user.id))
        async with asgi_client(app) as client:
            response = await client.get(
                "/whoami/", headers={"Authorization": f"Bearer {token}"}
            )
        assert response.json()["id"] == str(user.id)
