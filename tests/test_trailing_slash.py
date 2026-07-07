"""Both slash variants of every route are served directly — never a 307.

Canonical paths have no trailing slash. A hidden alias serves the slash
variant, because FastAPI's 307 redirect is rejected by browsers on
CORS-preflighted requests (frontend generators like Lovable frequently
append a trailing slash and then see "Load failed" against a working
backend). The OpenAPI schema must stay canonical: slash-less paths only.
"""

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

from zeeb_api.auth.jwt import configure_jwt
from zeeb_api.auth.router import create_auth_router
from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.routers.default import (
    DefaultRouter,
    SimpleRouter,
    add_slash_alias_routes,
)
from zeeb_api.viewsets import ViewSet, action

TEST_SECRET = "a-real-strong-secret-of-at-least-32-bytes"


class CrudStubViewSet(ViewSet):
    """Stub exposing the default CRUD action names (no DB access needed
    for route registration)."""

    async def query(self, request):
        return {"results": []}

    async def create(self, request):
        return {}

    async def retrieve(self, request, **kwargs):
        return {}

    @action(detail=False, methods=["get"])
    async def featured(self, request):
        return {"ok": True}

    @action(detail=True, methods=["post"])
    async def approve(self, request, id):
        return {"approved": str(id)}


def _route_paths(routers: list[APIRouter]) -> set[str]:
    return {route.path for router in routers for route in router.routes}


def _make_client(router: DefaultRouter | SimpleRouter) -> AsyncClient:
    app = FastAPI()
    install_exception_handlers(app)
    for api_router in router.get_urls():
        app.include_router(api_router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestViewSetSlashAliases:
    def test_both_variants_registered(self):
        router = SimpleRouter()
        router.register("reports", CrudStubViewSet)
        paths = _route_paths(router.get_urls())

        for canonical in (
            "/reports",
            "/reports/query",
            "/reports/{id}",
            "/reports/featured",
            "/reports/{id}/approve",
        ):
            assert canonical in paths
            assert f"{canonical}/" in paths

    async def test_custom_actions_serve_both_variants_without_redirect(self):
        router = SimpleRouter()
        router.register("reports", CrudStubViewSet)

        async with _make_client(router) as client:
            for path in ("/reports/featured", "/reports/featured/"):
                response = await client.get(path)
                assert response.status_code == 200
                assert response.json() == {"ok": True}

            detail_id = "550e8400-e29b-41d4-a716-446655440000"
            for path in (
                f"/reports/{detail_id}/approve",
                f"/reports/{detail_id}/approve/",
            ):
                response = await client.post(path)
                assert response.status_code == 200
                assert response.json() == {"approved": detail_id}

    def test_openapi_schema_stays_canonical(self):
        router = SimpleRouter()
        router.register("reports", CrudStubViewSet)
        app = FastAPI()
        for api_router in router.get_urls():
            app.include_router(api_router)

        schema_paths = app.openapi()["paths"]
        assert "/reports/query" in schema_paths
        assert not [p for p in schema_paths if p != "/" and p.endswith("/")]


class TestIncludedRawRouterAliases:
    def _raw_router(self) -> APIRouter:
        raw = APIRouter()

        @raw.get("/hooks/ping")
        async def ping():
            return {"pong": True}

        return raw

    async def test_included_router_serves_both_variants(self):
        router = DefaultRouter(include_root_view=False)
        router.include(self._raw_router())

        async with _make_client(router) as client:
            for path in ("/hooks/ping", "/hooks/ping/"):
                response = await client.get(path)
                assert response.status_code == 200
                assert response.json() == {"pong": True}

    async def test_included_router_with_prefix_serves_both_variants(self):
        router = DefaultRouter(include_root_view=False)
        router.include(self._raw_router(), prefix="/integrations")

        async with _make_client(router) as client:
            for path in ("/integrations/hooks/ping", "/integrations/hooks/ping/"):
                response = await client.get(path)
                assert response.status_code == 200

    def test_get_urls_is_idempotent(self):
        # include() clears the route cache, so get_urls() can run repeatedly
        # on the same raw router object — aliases must not stack.
        router = DefaultRouter(include_root_view=False)
        raw = self._raw_router()
        router.include(raw)

        first = _route_paths(router.get_urls())
        second = _route_paths(router.get_urls())
        assert first == second == {"/hooks/ping", "/hooks/ping/"}
        assert len(raw.routes) == 2


class TestAuthRouterSlashAliases:
    def _auth_router(self) -> APIRouter:
        configure_jwt(secret_key=TEST_SECRET)

        async def authenticate(request, body):
            if body.get("password") == "secret123":
                return ("user-1", {})
            return None

        return create_auth_router(
            authenticate=authenticate,
            use_database=False,
            enable_registration=False,
        )

    async def test_login_serves_both_variants_without_redirect(self):
        router = DefaultRouter(include_root_view=False)
        router.include(self._auth_router())

        async with _make_client(router) as client:
            for path in ("/auth/login", "/auth/login/"):
                response = await client.post(
                    path, json={"email": "a@b.co", "password": "secret123"}
                )
                assert response.status_code == 200
                body = response.json()
                assert body["access_token"] and body["refresh_token"]

    async def test_me_serves_both_variants_without_redirect(self):
        router = DefaultRouter(include_root_view=False)
        router.include(self._auth_router())

        async with _make_client(router) as client:
            for path in ("/auth/me", "/auth/me/"):
                response = await client.get(path)
                # 401 (not 307/404): the route exists in both variants and
                # rejects the missing token itself.
                assert response.status_code == 401

    def test_auth_openapi_paths_are_slashless(self):
        app = FastAPI()
        app.include_router(self._auth_router())
        schema_paths = app.openapi()["paths"]
        assert {"/auth/login", "/auth/refresh", "/auth/logout", "/auth/me"} <= set(
            schema_paths
        )
        assert not [p for p in schema_paths if p != "/" and p.endswith("/")]


class TestAddSlashAliasRoutesHelper:
    def test_skips_root_and_existing_paths(self):
        router = APIRouter()

        @router.get("/")
        async def root():
            return {}

        @router.get("/items")
        async def items():
            return {}

        @router.get("/items/")
        async def items_slash():
            return {}

        before = len(router.routes)
        add_slash_alias_routes(router)
        assert len(router.routes) == before
