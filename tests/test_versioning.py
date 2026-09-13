"""Tests for API versioning (zeeb_api.versioning)."""

import copy

import pytest
from fastapi import Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request as StarletteRequest

from zeeb_api.conf import settings
from zeeb_api.exception_handlers import install_exception_handlers
from zeeb_api.exceptions import ValidationException
from zeeb_api.versioning import (
    AcceptHeaderVersioning,
    HeaderVersioning,
    InvalidVersionError,
    QueryParameterVersioning,
    URLPathVersioning,
    VersioningMiddleware,
    get_api_version,
)

VERSIONING_SETTINGS = (
    "DEFAULT_VERSIONING_CLASS",
    "DEFAULT_VERSION",
    "ALLOWED_VERSIONS",
    "VERSION_PARAM",
    "VERSION_HEADER",
)


@pytest.fixture(autouse=True)
def restore_versioning_settings():
    """Save/restore patched versioning settings."""
    saved = {name: copy.deepcopy(getattr(settings, name)) for name in VERSIONING_SETTINGS}
    yield
    for name, value in saved.items():
        setattr(settings, name, value)


def make_request(path="/", query="", headers=None):
    raw_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode(),
        "headers": raw_headers,
        "client": ("10.0.0.1", 1234),
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return StarletteRequest(scope)


class TestQueryParameterVersioning:
    def test_extracts_version_param(self):
        request = make_request(query="version=2.0")
        assert QueryParameterVersioning().determine_version(request) == "2.0"

    def test_custom_param_name_from_settings(self):
        settings.VERSION_PARAM = "v"
        request = make_request(query="v=3")
        assert QueryParameterVersioning().determine_version(request) == "3"

    def test_missing_param_falls_back_to_default(self):
        settings.DEFAULT_VERSION = "1.0"
        request = make_request()
        assert QueryParameterVersioning().determine_version(request) == "1.0"

    def test_missing_param_no_default_returns_none(self):
        request = make_request()
        assert QueryParameterVersioning().determine_version(request) is None

    def test_disallowed_version_raises(self):
        settings.ALLOWED_VERSIONS = ["1.0", "2.0"]
        request = make_request(query="version=9.0")
        with pytest.raises(InvalidVersionError) as exc_info:
            QueryParameterVersioning().determine_version(request)
        assert exc_info.value.version == "9.0"

    def test_default_version_is_always_allowed(self):
        settings.DEFAULT_VERSION = "0.9"
        settings.ALLOWED_VERSIONS = ["1.0"]
        request = make_request(query="version=0.9")
        assert QueryParameterVersioning().determine_version(request) == "0.9"


class TestHeaderVersioning:
    def test_extracts_default_header(self):
        request = make_request(headers={"X-API-Version": "2.0"})
        assert HeaderVersioning().determine_version(request) == "2.0"

    def test_custom_header_from_settings(self):
        settings.VERSION_HEADER = "X-My-Version"
        request = make_request(headers={"X-My-Version": "4"})
        assert HeaderVersioning().determine_version(request) == "4"

    def test_missing_header_uses_default(self):
        settings.DEFAULT_VERSION = "1.0"
        request = make_request()
        assert HeaderVersioning().determine_version(request) == "1.0"

    def test_disallowed_version_raises(self):
        settings.ALLOWED_VERSIONS = ["1.0"]
        request = make_request(headers={"X-API-Version": "5.0"})
        with pytest.raises(InvalidVersionError):
            HeaderVersioning().determine_version(request)


class TestAcceptHeaderVersioning:
    def test_extracts_media_type_param(self):
        request = make_request(headers={"Accept": "application/json; version=1.0"})
        assert AcceptHeaderVersioning().determine_version(request) == "1.0"

    def test_quoted_value(self):
        request = make_request(headers={"Accept": 'application/json; version="2.0"'})
        assert AcceptHeaderVersioning().determine_version(request) == "2.0"

    def test_multiple_media_types(self):
        request = make_request(
            headers={"Accept": "text/html, application/json; q=0.9; version=3.0"}
        )
        assert AcceptHeaderVersioning().determine_version(request) == "3.0"

    def test_no_version_param_uses_default(self):
        settings.DEFAULT_VERSION = "1.0"
        request = make_request(headers={"Accept": "application/json"})
        assert AcceptHeaderVersioning().determine_version(request) == "1.0"

    def test_disallowed_version_raises(self):
        settings.ALLOWED_VERSIONS = ["1.0"]
        request = make_request(headers={"Accept": "application/json; version=8"})
        with pytest.raises(InvalidVersionError):
            AcceptHeaderVersioning().determine_version(request)


class TestURLPathVersioning:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("/v1/users", "1"),
            ("/v2.1/users/", "2.1"),
            ("/api/v3", "3"),
            ("/v10/things/abc", "10"),
        ],
    )
    def test_extracts_from_path(self, path, expected):
        request = make_request(path=path)
        assert URLPathVersioning().determine_version(request) == expected

    def test_no_version_segment_uses_default(self):
        settings.DEFAULT_VERSION = "1"
        request = make_request(path="/users/")
        assert URLPathVersioning().determine_version(request) == "1"

    def test_version_like_words_not_matched(self):
        # "/vienna" must not match the /v<number> pattern
        request = make_request(path="/vienna/users")
        assert URLPathVersioning().extract_version(request) is None

    def test_disallowed_version_raises(self):
        settings.ALLOWED_VERSIONS = ["1", "2"]
        request = make_request(path="/v9/users")
        with pytest.raises(InvalidVersionError):
            URLPathVersioning().determine_version(request)


def _make_app() -> FastAPI:
    app = FastAPI()
    install_exception_handlers(app)
    app.add_middleware(VersioningMiddleware)

    @app.get("/echo")
    async def echo(request: Request):
        return {"version": request.state.version}

    return app


class TestVersioningMiddleware:
    async def test_sets_request_state_version(self):
        settings.DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.QueryParameterVersioning"
        app = _make_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/echo", params={"version": "2.0"})
            assert response.status_code == 200
            assert response.json() == {"version": "2.0"}

    async def test_default_version_applied(self):
        settings.DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.HeaderVersioning"
        settings.DEFAULT_VERSION = "1.0"
        app = _make_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/echo")
            assert response.json() == {"version": "1.0"}

    async def test_invalid_version_returns_400_envelope(self):
        settings.DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.QueryParameterVersioning"
        settings.ALLOWED_VERSIONS = ["1.0", "2.0"]
        app = _make_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/echo", params={"version": "9.9"})
            assert response.status_code == 400
            body = response.json()
            assert body["success"] is False
            assert body["error"]["code"] == "API_VERSION_INVALID"
            assert "9.9" in body["error"]["message"]

    async def test_no_scheme_configured_passes_through(self):
        # Default settings: DEFAULT_VERSIONING_CLASS is None
        app = _make_app()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/echo", params={"version": "2.0"})
            assert response.status_code == 200
            assert response.json() == {"version": None}


class TestGetApiVersionDependency:
    async def test_returns_state_version_when_set(self):
        request = make_request()
        request.state.version = "5.0"
        assert await get_api_version(request) == "5.0"

    async def test_runs_scheme_on_demand(self):
        settings.DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.HeaderVersioning"
        request = make_request(headers={"X-API-Version": "2.0"})
        assert await get_api_version(request) == "2.0"

    async def test_no_scheme_returns_none(self):
        request = make_request()
        assert await get_api_version(request) is None

    async def test_invalid_version_raises_validation_exception(self):
        settings.DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.HeaderVersioning"
        settings.ALLOWED_VERSIONS = ["1.0"]
        request = make_request(headers={"X-API-Version": "7"})
        with pytest.raises(ValidationException) as exc_info:
            await get_api_version(request)
        assert exc_info.value.code == "API_VERSION_INVALID"
        assert exc_info.value.status_code == 400

    async def test_route_dependency_integration(self):
        settings.DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.QueryParameterVersioning"
        app = FastAPI()
        install_exception_handlers(app)

        @app.get("/items")
        async def items(version: str | None = Depends(get_api_version)):
            return {"version": version}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/items", params={"version": "3"})
            assert response.json() == {"version": "3"}


class TestViewSetVersionAttribute:
    async def test_viewset_receives_version(self):
        from zeeb_api.routers.default import SimpleRouter
        from zeeb_api.viewsets import ViewSet, action

        settings.DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.QueryParameterVersioning"

        class EchoViewSet(ViewSet):
            @action(detail=False, methods=["get"])
            async def current_version(self, request):
                return {"version": self.version}

        app = FastAPI()
        install_exception_handlers(app)
        app.add_middleware(VersioningMiddleware)
        router = SimpleRouter()
        router.register("echo", EchoViewSet)
        for api_router in router.get_urls():
            app.include_router(api_router)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/echo/current_version", params={"version": "2.0"})
            assert response.status_code == 200
            assert response.json() == {"version": "2.0"}
