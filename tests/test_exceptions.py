"""Tests for the unified exception classes in zeeb_api.exceptions."""

import warnings

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from zeeb_api.exception_handlers import install_exception_handlers
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
