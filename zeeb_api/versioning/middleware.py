"""
Versioning middleware.

Determines the API version for every request using the scheme configured in
settings.DEFAULT_VERSIONING_CLASS and stores it on ``request.state.version``.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from zeeb_api.exceptions import (
    ErrorBody,
    ErrorCode,
    ErrorMeta,
    ErrorResponse,
)
from zeeb_api.versioning.base import BaseVersioning, InvalidVersionError

_UNRESOLVED = object()


class VersioningMiddleware(BaseHTTPMiddleware):
    """
    Middleware that sets ``request.state.version``.

    Self-configures from settings (loadable kwargs-less via
    settings.MIDDLEWARE): the versioning scheme is resolved lazily from
    settings.DEFAULT_VERSIONING_CLASS on first request. If no scheme is
    configured, ``request.state.version`` is set to None and the request
    passes through.

    Invalid versions produce a standardized 400 ErrorResponse with code
    API_VERSION_INVALID. The response is returned directly (not raised)
    because exception handlers do not catch exceptions raised in
    middleware.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        # (dotted_path, resolved class) cache; re-resolved if the setting changes.
        self._scheme_cache: tuple[object, type[BaseVersioning] | None] = (_UNRESOLVED, None)

    def _get_scheme_class(self) -> type[BaseVersioning] | None:
        from zeeb_api.conf import settings

        dotted_path = getattr(settings, "DEFAULT_VERSIONING_CLASS", None)

        cached_path, cached_class = self._scheme_cache
        if cached_path == dotted_path:
            return cached_class

        if dotted_path is None:
            scheme_class = None
        else:
            from zeeb_api.middleware.loader import import_string

            scheme_class = import_string(dotted_path)

        self._scheme_cache = (dotted_path, scheme_class)
        return scheme_class

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        scheme_class = self._get_scheme_class()

        if scheme_class is None:
            request.state.version = None
            return await call_next(request)

        try:
            request.state.version = scheme_class().determine_version(request)
        except InvalidVersionError as exc:
            return self._invalid_version_response(request, exc)

        return await call_next(request)

    @staticmethod
    def _invalid_version_response(
        request: Request,
        exc: InvalidVersionError,
    ) -> JSONResponse:
        """Build the standardized 400 envelope for an invalid version."""
        error_response = ErrorResponse(
            success=False,
            error=ErrorBody(
                code=ErrorCode.API_VERSION_INVALID.value,
                message=str(exc),
                details=[],
                meta=ErrorMeta(
                    request_id=request.headers.get("X-Request-ID", str(uuid.uuid4())),
                    path=str(request.url.path),
                    method=request.method,
                ),
            ),
        )
        return JSONResponse(status_code=400, content=error_response.model_dump())
