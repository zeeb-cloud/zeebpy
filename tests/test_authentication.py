"""Tests for per-viewset authentication (zeeb_api.authentication)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

import zeeb_api.auth.jwt as jwt_module
from zeeb_api.auth.jwt import configure_jwt, create_access_token
from zeeb_api.authentication import (
    BaseAuthentication,
    JWTAuthentication,
    JWTStatelessAuthentication,
    OAuth2BearerAuthentication,
)
from zeeb_api.exceptions import AuthenticationException, ErrorCode
from zeeb_api.viewsets import ViewSet

SECRET = "a-real-strong-secret-of-at-least-32-bytes"


@pytest.fixture(autouse=True)
def restore_jwt_config():
    """Save/restore the global JWT config around each test."""
    saved = jwt_module._jwt_config
    yield
    jwt_module._jwt_config = saved


def make_request(headers: dict | None = None) -> Request:
    raw_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": raw_headers,
        "client": ("10.0.0.1", 1234),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope)


class _FakeUser:
    is_authenticated = True

    def __init__(self, name: str) -> None:
        self.name = name


def _fake_authenticator(name: str, result, calls: list[str]):
    class Fake(BaseAuthentication):
        async def authenticate(self, request):
            calls.append(name)
            if isinstance(result, Exception):
                raise result
            return result

    return Fake


class TestGetAuthenticators:
    def test_unset_returns_empty(self):
        assert ViewSet().get_authenticators() == []

    def test_empty_list_returns_empty(self):
        class VS(ViewSet):
            authentication_classes = []

        assert VS().get_authenticators() == []

    def test_list_instantiates_in_order(self):
        class VS(ViewSet):
            authentication_classes = [JWTStatelessAuthentication, JWTAuthentication]

        authenticators = VS().get_authenticators()
        assert isinstance(authenticators[0], JWTStatelessAuthentication)
        assert isinstance(authenticators[1], JWTAuthentication)


class TestPerformAuthentication:
    async def test_first_success_wins_in_order(self):
        calls: list[str] = []
        user = _FakeUser("second")

        class VS(ViewSet):
            authentication_classes = [
                _fake_authenticator("a", None, calls),
                _fake_authenticator("b", user, calls),
                _fake_authenticator("c", _FakeUser("never"), calls),
            ]

        request = make_request()
        await VS().perform_authentication(request)
        assert request.state.user is user
        assert calls == ["a", "b"]  # c never runs after b succeeds

    async def test_all_none_overrides_middleware_user(self):
        """A non-empty list takes ownership: the middleware's user is discarded."""
        calls: list[str] = []

        class VS(ViewSet):
            authentication_classes = [_fake_authenticator("a", None, calls)]

        request = make_request()
        request.state.user = _FakeUser("from-middleware")
        request.state.auth_error = ErrorCode.AUTH_TOKEN_EXPIRED
        await VS().perform_authentication(request)
        assert request.state.user is None
        assert request.state.auth_error is None

    async def test_unset_keeps_middleware_user(self):
        middleware_user = _FakeUser("from-middleware")
        request = make_request()
        request.state.user = middleware_user
        await ViewSet().perform_authentication(request)
        assert request.state.user is middleware_user

    async def test_hard_raise_propagates_and_skips_rest(self):
        calls: list[str] = []
        boom = AuthenticationException(message="bad credentials")

        class VS(ViewSet):
            authentication_classes = [
                _fake_authenticator("a", boom, calls),
                _fake_authenticator("b", _FakeUser("never"), calls),
            ]

        with pytest.raises(AuthenticationException):
            await VS().perform_authentication(make_request())
        assert calls == ["a"]


class TestJWTAuthentication:
    async def test_no_header_returns_none(self):
        request = make_request()
        assert await JWTStatelessAuthentication().authenticate(request) is None
        assert getattr(request.state, "auth_error", None) is None

    async def test_valid_token_returns_user(self):
        configure_jwt(secret_key=SECRET)
        token = create_access_token("user-7", claims={"is_staff": True})
        request = make_request({"Authorization": f"Bearer {token}"})
        user = await JWTStatelessAuthentication().authenticate(request)
        assert user is not None
        assert user.id == "user-7"
        assert user.is_authenticated
        assert user.is_staff

    async def test_expired_token_records_auth_error(self):
        configure_jwt(secret_key=SECRET, access_token_expire_minutes=-1)
        token = create_access_token("user-7")
        request = make_request({"Authorization": f"Bearer {token}"})
        assert await JWTStatelessAuthentication().authenticate(request) is None
        assert request.state.auth_error == ErrorCode.AUTH_TOKEN_EXPIRED

    async def test_garbage_token_records_auth_error(self):
        configure_jwt(secret_key=SECRET)
        request = make_request({"Authorization": "Bearer not-a-jwt"})
        assert await JWTStatelessAuthentication().authenticate(request) is None
        assert request.state.auth_error == ErrorCode.AUTH_TOKEN_INVALID


class TestOAuth2BearerAuthentication:
    async def test_no_header_returns_none(self):
        assert await OAuth2BearerAuthentication().authenticate(make_request()) is None

    async def test_accepted_token_returns_user(self, monkeypatch):
        user = _FakeUser("external")

        async def accept(token):
            return user if token == "ext-token" else None

        monkeypatch.setattr(
            OAuth2BearerAuthentication,
            "_get_validators",
            lambda self: [accept],
        )
        request = make_request({"Authorization": "Bearer ext-token"})
        assert await OAuth2BearerAuthentication().authenticate(request) is user

    async def test_no_validators_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            OAuth2BearerAuthentication, "_get_validators", lambda self: []
        )
        request = make_request({"Authorization": "Bearer anything"})
        assert await OAuth2BearerAuthentication().authenticate(request) is None


class TestRouterIntegration:
    def _build_app(self, viewset_class) -> FastAPI:
        from zeeb_api.exception_handlers import install_exception_handlers
        from zeeb_api.routers.default import SimpleRouter

        app = FastAPI()
        install_exception_handlers(app)
        router = SimpleRouter()
        router.register("items", viewset_class)
        for api_router in router.get_urls():
            app.include_router(api_router)
        return app

    async def test_viewset_authentication_end_to_end(self):
        from zeeb_api.permissions import IsAuthenticated
        from zeeb_api.viewsets import action

        configure_jwt(secret_key=SECRET)

        class ItemViewSet(ViewSet):
            authentication_classes = [JWTStatelessAuthentication]
            permission_classes = [IsAuthenticated]

            @action(detail=False, methods=["get"])
            async def whoami(self, request):
                return {"id": request.state.user.id}

        app = self._build_app(ItemViewSet)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get("/items/whoami")
            assert denied.status_code == 401

            token = create_access_token("user-42")
            allowed = await client.get(
                "/items/whoami", headers={"Authorization": f"Bearer {token}"}
            )
            assert allowed.status_code == 200
            assert allowed.json() == {"id": "user-42"}

    async def test_viewset_auth_overrides_middleware(self):
        """A viewset pinned to its own authenticators ignores middleware users."""
        from zeeb_api.permissions import IsAuthenticated
        from zeeb_api.viewsets import action

        class RejectAll(BaseAuthentication):
            async def authenticate(self, request):
                return None

        class PinnedViewSet(ViewSet):
            authentication_classes = [RejectAll]
            permission_classes = [IsAuthenticated]

            @action(detail=False, methods=["get"])
            async def whoami(self, request):  # pragma: no cover - never reached
                return {"id": request.state.user.id}

        app = self._build_app(PinnedViewSet)

        @app.middleware("http")
        async def fake_auth_middleware(request, call_next):
            request.state.user = _FakeUser("from-middleware")
            return await call_next(request)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/items/whoami")
            assert response.status_code == 401


class TestMultiplePermissionClasses:
    async def test_two_classes_are_anded(self):
        from zeeb_api.permissions import IsAdminUser, IsAuthenticated

        class VS(ViewSet):
            permission_classes = [IsAuthenticated, IsAdminUser]

        class _User:
            is_authenticated = True
            is_staff = False
            is_admin = False
            is_superuser = False

        request = make_request()
        request.state.user = _User()
        with pytest.raises(Exception):  # PermissionDenied (403)
            await VS().check_permissions(request)

        request.state.user.is_staff = True
        await VS().check_permissions(request)  # both pass now
