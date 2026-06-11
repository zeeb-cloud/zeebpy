"""
FastAPI dependency for accessing the API version in route handlers.
"""

from __future__ import annotations

from fastapi import Request

from zeeb_api.exceptions import ErrorCode, ValidationException
from zeeb_api.versioning.base import InvalidVersionError


async def get_api_version(request: Request) -> str | None:
    """
    Return the API version for the current request.

    If VersioningMiddleware is installed, returns request.state.version.
    Otherwise runs the scheme from settings.DEFAULT_VERSIONING_CLASS on
    demand (returning None if no scheme is configured).

    Raises ValidationException (400, API_VERSION_INVALID) for invalid
    versions when run on demand.

    Usage:
        @app.get("/things")
        async def things(version: str | None = Depends(get_api_version)):
            ...
    """
    state = getattr(request, "state", None)
    if state is not None and hasattr(state, "version"):
        return state.version

    from zeeb_api.conf import settings

    dotted_path = getattr(settings, "DEFAULT_VERSIONING_CLASS", None)
    if dotted_path is None:
        return None

    from zeeb_api.middleware.loader import import_string

    scheme_class = import_string(dotted_path)

    try:
        return scheme_class().determine_version(request)
    except InvalidVersionError as exc:
        error = ValidationException(message=str(exc))
        error.code = ErrorCode.API_VERSION_INVALID.value
        raise error from exc
