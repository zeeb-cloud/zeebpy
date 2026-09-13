"""
DRF-style API versioning schemes.

A versioning scheme extracts the requested API version from the request,
validates it against the allowed versions, and falls back to the default.

Configure globally via settings:

    DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.HeaderVersioning"
    DEFAULT_VERSION = "1.0"
    ALLOWED_VERSIONS = ["1.0", "2.0"]
"""

from __future__ import annotations

import re

from fastapi import Request


class InvalidVersionError(Exception):
    """Raised when the requested API version is not allowed."""

    def __init__(self, version: str | None, message: str | None = None) -> None:
        self.version = version
        super().__init__(message or f"Invalid API version: {version!r}")


class BaseVersioning:
    """
    Base class for versioning schemes.

    Class attributes default to None and fall back to settings
    (DEFAULT_VERSION / ALLOWED_VERSIONS / VERSION_PARAM) at instantiation
    time; subclasses may override them explicitly.
    """

    default_version: str | None = None
    allowed_versions: list[str] | None = None
    version_param: str | None = None

    def __init__(self) -> None:
        from zeeb_api.conf import settings

        if self.default_version is None:
            self.default_version = getattr(settings, "DEFAULT_VERSION", None)
        if self.allowed_versions is None:
            self.allowed_versions = getattr(settings, "ALLOWED_VERSIONS", None)
        if self.version_param is None:
            self.version_param = getattr(settings, "VERSION_PARAM", "version")

    def extract_version(self, request: Request) -> str | None:
        """Extract the raw version from the request (None if absent)."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement extract_version()"
        )

    def is_allowed_version(self, version: str) -> bool:
        """Check a version against allowed_versions (always True if unset)."""
        if not self.allowed_versions:
            return True
        return version == self.default_version or version in self.allowed_versions

    def determine_version(self, request: Request) -> str | None:
        """
        Determine the API version for a request.

        Extracts the version; if absent, falls back to default_version.
        Raises InvalidVersionError if the version is not allowed.
        """
        version = self.extract_version(request)
        if version is None:
            version = self.default_version
            if version is None:
                return None
        if not self.is_allowed_version(version):
            raise InvalidVersionError(version)
        return version


class QueryParameterVersioning(BaseVersioning):
    """
    Version from a query parameter:

        GET /users/?version=1.0
    """

    def extract_version(self, request: Request) -> str | None:
        return request.query_params.get(self.version_param)


class HeaderVersioning(BaseVersioning):
    """
    Version from a request header (settings.VERSION_HEADER,
    default "X-API-Version"):

        GET /users/
        X-API-Version: 1.0
    """

    version_header: str | None = None

    def __init__(self) -> None:
        super().__init__()
        if self.version_header is None:
            from zeeb_api.conf import settings

            self.version_header = getattr(settings, "VERSION_HEADER", "X-API-Version")

    def extract_version(self, request: Request) -> str | None:
        return request.headers.get(self.version_header)


class AcceptHeaderVersioning(BaseVersioning):
    """
    Version from a media-type parameter on the Accept header:

        GET /users/
        Accept: application/json; version=1.0
    """

    def extract_version(self, request: Request) -> str | None:
        accept = request.headers.get("Accept")
        if not accept:
            return None

        # The Accept header may list several media types; check each one's
        # parameters for the version param.
        for media_type in accept.split(","):
            parts = media_type.split(";")
            for param in parts[1:]:
                if "=" not in param:
                    continue
                key, _, value = param.partition("=")
                if key.strip() == self.version_param:
                    return value.strip().strip('"')
        return None


class URLPathVersioning(BaseVersioning):
    """
    Version from the URL path:

        GET /v1/users/
        GET /v2.1/users/

    The version is matched with a regex on request.url.path because
    request.path_params is empty inside BaseHTTPMiddleware (path params
    are only resolved during routing, after middleware runs).
    """

    path_regex = re.compile(r"/v(?P<version>\d+(?:\.\d+)?)(/|$)")

    def extract_version(self, request: Request) -> str | None:
        match = self.path_regex.search(request.url.path)
        if match:
            return match.group("version")
        return None
