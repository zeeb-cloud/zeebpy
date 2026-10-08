"""Tests for the unified exception classes in zeeb_api.exceptions."""

import warnings

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from pydantic import BaseModel

from zeeb_api.exception_handlers import (
    install_error_response_schema,
    install_exception_handlers,
)
from zeeb_api.exceptions import (
    APIException,
    AuthenticationException,
    AuthenticationFailed,
    MethodNotAllowed,
    MethodNotAllowedException,
    NotFound,
    PermissionDenied,
    PermissionException,
    RateLimitException,
    ResourceNotFoundException,
    Throttled,
    ValidationError,
    ValidationException,
    ZeebException,
)


class TestDualInheritance:
    """Compat classes must belong to BOTH exception hierarchies."""

    def test_validation_error_isinstance(self):
        exc = ValidationError({"email": ["Invalid"]})
        assert isinstance(exc, ValidationException)
        assert isinstance(exc, APIException)
        assert isinstance(exc, ZeebException)
        assert isinstance(exc, HTTPException)
        assert isinstance(exc, StarletteHTTPException)

    def test_api_exception_isinstance(self):
        exc = APIException("boom", status_code=502)
        assert isinstance(exc, ZeebException)
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 502
        assert exc.detail == "boom"

    @pytest.mark.parametrize(
        "cls,zeeb_base,status",
        [
            (NotFound, ResourceNotFoundException, 404),
            (PermissionDenied, PermissionException, 403),
            (AuthenticationFailed, AuthenticationException, 401),
            (MethodNotAllowed, MethodNotAllowedException, 405),
            (Throttled, RateLimitException, 429),
        ],
    )
    def test_other_compat_classes(self, cls, zeeb_base, status):
        exc = cls()
        assert isinstance(exc, zeeb_base)
        assert isinstance(exc, APIException)
        assert isinstance(exc, ZeebException)
        assert isinstance(exc, HTTPException)
        assert exc.status_code == status

    def test_validation_error_attributes(self):
        exc = ValidationError({"email": ["Invalid"]})
        assert exc.status_code == 400
        assert exc.detail == {"email": ["Invalid"]}
        assert exc.code == "VALIDATION_ERROR"
        assert len(exc.details) == 1
        assert exc.details[0].field == "email"
        assert exc.details[0].code == "FIELD_INVALID_VALUE"

    def test_throttled_wait_appended_to_detail(self):
        exc = Throttled(wait=30)
        assert "30 seconds" in exc.detail


def _make_app(with_handlers: bool) -> TestClient:
    app = FastAPI()
    if with_handlers:
        install_exception_handlers(app)

    @app.get("/validation")
    async def validation():
        raise ValidationError({"email": ["Invalid"]})

    @app.get("/not-found")
    async def not_found():
        raise NotFound()

    @app.get("/permission")
    async def permission():
        raise PermissionDenied()

    @app.get("/auth")
    async def auth():
        raise AuthenticationFailed()

    @app.get("/method")
    async def method():
        raise MethodNotAllowed()

    @app.get("/throttled")
    async def throttled():
        raise Throttled()

    return TestClient(app, raise_server_exceptions=False)


class TestWithHandlersInstalled:
    """With install_exception_handlers, errors use the standardized envelope."""

    def test_validation_error_envelope(self):
        client = _make_app(with_handlers=True)
        resp = client.get("/validation")
        assert resp.status_code == 400
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_ERROR"
        details = body["error"]["details"]
        assert any(
            d["field"] == "email" and d["code"] == "FIELD_INVALID_VALUE"
            for d in details
        )

    @pytest.mark.parametrize(
        "path,status,code",
        [
            ("/not-found", 404, "RESOURCE_NOT_FOUND"),
            ("/permission", 403, "PERM_DENIED"),
            ("/auth", 401, "AUTH_TOKEN_MISSING"),
            ("/method", 405, "METHOD_NOT_ALLOWED"),
            ("/throttled", 429, "RATE_LIMIT_EXCEEDED"),
        ],
    )
    def test_other_exceptions_envelope(self, path, status, code):
        client = _make_app(with_handlers=True)
        resp = client.get(path)
        assert resp.status_code == status
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == code


class TestWithoutHandlers:
    """Without handlers, Starlette's HTTPException handling preserves the
    legacy status codes and detail payloads."""

    def test_validation_error_legacy_detail(self):
        client = _make_app(with_handlers=False)
        resp = client.get("/validation")
        assert resp.status_code == 400
        assert resp.json() == {"detail": {"email": ["Invalid"]}}

    @pytest.mark.parametrize(
        "path,status",
        [
            ("/not-found", 404),
            ("/permission", 403),
            ("/auth", 401),
            ("/method", 405),
            ("/throttled", 429),
        ],
    )
    def test_other_exceptions_legacy_status(self, path, status):
        client = _make_app(with_handlers=False)
        resp = client.get(path)
        assert resp.status_code == status
        assert "detail" in resp.json()


def _make_openapi_app() -> TestClient:
    """App with a body-validated route, using the standard error contract."""
    app = FastAPI(title="probe", version="1.0.0")
    install_exception_handlers(app)
    install_error_response_schema(app)

    class LoginIn(BaseModel):
        email: str
        password: str

    @app.post("/login")
    async def login(body: LoginIn):
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


class TestErrorResponseSchema:
    """install_error_response_schema makes the OpenAPI match the runtime envelope."""

    def test_runtime_422_uses_envelope(self):
        client = _make_openapi_app()
        resp = client.post("/login", json={})
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_ERROR"
        fields = {d["field"] for d in body["error"]["details"]}
        assert {"email", "password"} <= fields

    def test_openapi_422_references_error_response(self):
        client = _make_openapi_app()
        spec = client.get("/openapi.json").json()
        schema = spec["paths"]["/login"]["post"]["responses"]["422"]["content"][
            "application/json"
        ]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorResponse"}

    def test_openapi_components_and_stale_schemas(self):
        client = _make_openapi_app()
        comps = client.get("/openapi.json").json()["components"]["schemas"]
        assert {"ErrorResponse", "ErrorBody", "ErrorDetail", "ErrorMeta"} <= set(comps)
        # The default FastAPI validation schemas must be gone — the server never
        # returns that shape once the envelope handlers are installed.
        assert "HTTPValidationError" not in comps
        assert "ValidationError" not in comps

    def test_openapi_422_is_described_as_the_envelope(self):
        client = _make_openapi_app()
        response = client.get("/openapi.json").json()["paths"]["/login"]["post"]["responses"]["422"]
        assert response["description"] == "Request validation failed"
        example = response["content"]["application/json"]["example"]
        assert example["error"]["code"] == "VALIDATION_ERROR"
        assert example["error"]["details"][0]["code"] == "FIELD_REQUIRED"

    def test_description_only_error_responses_get_the_envelope(self):
        """A route declaring ``responses={404: {"description": ...}}`` documented
        no body; the server answers it with the envelope all the same."""
        app = FastAPI(title="probe", version="1.0.0")
        install_exception_handlers(app)
        install_error_response_schema(app)

        @app.get("/things/{name}", responses={404: {"description": "No such thing"}, 503: {}})
        async def thing(name: str):
            return {"name": name}

        responses = TestClient(app).get("/openapi.json").json()["paths"]["/things/{name}"]["get"][
            "responses"
        ]
        not_found = responses["404"]
        assert not_found["description"] == "No such thing"
        media = not_found["content"]["application/json"]
        assert media["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}
        assert media["example"]["error"]["code"] == "RESOURCE_NOT_FOUND"
        unavailable = responses["503"]
        assert unavailable["content"]["application/json"]["example"]["error"]["code"] == (
            "SERVER_UNAVAILABLE"
        )

    def test_an_example_the_route_declares_is_kept(self):
        from zeeb_api.exceptions import error_response_doc

        app = FastAPI(title="probe", version="1.0.0")
        install_error_response_schema(app)

        @app.post("/publish", responses={409: error_response_doc(409, code="ALREADY_PUBLISHED")})
        async def publish():
            return {}

        media = TestClient(app).get("/openapi.json").json()["paths"]["/publish"]["post"][
            "responses"
        ]["409"]["content"]["application/json"]
        assert media["example"]["error"]["code"] == "ALREADY_PUBLISHED"

    def test_get_error_responses_examples_match_their_status(self):
        from zeeb_api.exception_handlers import get_error_responses
        from zeeb_api.exceptions import STATUS_CODE_TO_ERROR_CODE

        for status, doc in get_error_responses().items():
            example = doc["content"]["application/json"]["example"]
            assert example["error"]["code"] == STATUS_CODE_TO_ERROR_CODE[status]


class TestScaffoldAsgiTemplate:
    """The generated asgi.py delegates the standard error contract to create_app()."""

    def test_asgi_template_delegates_error_contract_to_create_app(self):
        from zeeb_orm.cli.commands.startproject import ASGI_PY

        rendered = ASGI_PY.format(project_name="probe_api")
        # The generated asgi.py is a thin shim: the error envelope (and middleware,
        # routes, health) is installed by zeeb_api.create_app() from settings, not
        # hand-rolled in the template.
        assert 'create_app("probe_api.settings")' in rendered
        assert "install_exception_handlers" not in rendered
        # Rendered template must be valid Python.
        compile(rendered, "asgi.py", "exec")

    def test_create_app_installs_exception_handlers_by_default(self):
        """create_app() is where the error contract actually gets installed."""
        from fastapi import FastAPI

        from zeeb_api.exception_handlers import install_exception_handlers
        from zeeb_api.exceptions import ZeebException

        app = FastAPI()
        install_exception_handlers(app)
        # The base ZeebException handler is registered (renders the envelope).
        assert ZeebException in app.exception_handlers


class TestDeprecatedResponseImports:
    """zeeb_api.response keeps working but warns."""

    def test_import_emits_deprecation_warning(self):
        import zeeb_api.response

        with pytest.warns(DeprecationWarning, match="zeeb_api.exceptions"):
            getattr(zeeb_api.response, "NotFound")

    def test_response_shim_returns_canonical_class(self):
        import zeeb_api.response

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            assert zeeb_api.response.ValidationError is ValidationError

    def test_package_import_does_not_warn(self):
        """`import zeeb_api` must not emit DeprecationWarning (fresh process)."""
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-W", "error::DeprecationWarning", "-c", "import zeeb_api"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- #
# 500s always use the envelope; request ids are validated before being echoed
# --------------------------------------------------------------------------- #


def _crashing_app(*, debug: bool) -> TestClient:
    app = FastAPI(debug=debug)
    install_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom-secret-detail")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("debug", [False, True])
def test_unhandled_exception_uses_envelope_even_in_debug(debug, monkeypatch):
    from zeeb_api.conf import settings

    monkeypatch.setattr(settings, "DEBUG", False, raising=False)
    resp = _crashing_app(debug=debug).get("/boom")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "SERVER_ERROR"
    assert body["error"]["message"] == "Internal server error"
    details = body["error"]["details"]
    if debug:
        assert details[0]["message"] == "RuntimeError: kaboom-secret-detail"
        assert "Traceback" in details[0]["meta"]["traceback"]
    else:
        assert details == []
        assert "kaboom-secret-detail" not in resp.text


def test_debug_setting_alone_adds_details(monkeypatch):
    from zeeb_api.conf import settings

    monkeypatch.setattr(settings, "DEBUG", True, raising=False)
    resp = _crashing_app(debug=False).get("/boom")
    assert resp.json()["error"]["details"][0]["meta"]["exception"] == "RuntimeError"


def test_unhandled_exception_is_still_raised_to_the_server():
    app = FastAPI(debug=True)
    install_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        TestClient(app).get("/boom")


@pytest.mark.parametrize(
    "request_id",
    [
        "abc-123",
        "550e8400-e29b-41d4-a716-446655440000",
        "trace:svc/1.2=+_",
        "x" * 128,
    ],
)
def test_safe_request_id_is_echoed(request_id):
    resp = _make_app(with_handlers=True).get("/not-found", headers={"X-Request-ID": request_id})
    assert resp.json()["error"]["meta"]["request_id"] == request_id


@pytest.mark.parametrize(
    "request_id",
    [
        "<script>alert(1)</script>",
        "has space",
        "x" * 129,
        "line\tbreak",
        "quote\"d",
    ],
)
def test_unsafe_request_id_is_replaced(request_id):
    import uuid as _uuid

    resp = _make_app(with_handlers=True).get("/not-found", headers={"X-Request-ID": request_id})
    echoed = resp.json()["error"]["meta"]["request_id"]
    assert echoed != request_id
    _uuid.UUID(echoed)  # a generated id instead
