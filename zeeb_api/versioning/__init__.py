"""
DRF-style API versioning for Zeeb API.

Provides:
- BaseVersioning and concrete schemes (query param, header, accept header,
  URL path)
- VersioningMiddleware that sets request.state.version
- get_api_version() FastAPI dependency
"""

from zeeb_api.versioning.base import (
    AcceptHeaderVersioning,
    BaseVersioning,
    HeaderVersioning,
    InvalidVersionError,
    QueryParameterVersioning,
    URLPathVersioning,
)
from zeeb_api.versioning.dependency import get_api_version
from zeeb_api.versioning.middleware import VersioningMiddleware

__all__ = [
    "AcceptHeaderVersioning",
    "BaseVersioning",
    "HeaderVersioning",
    "InvalidVersionError",
    "QueryParameterVersioning",
    "URLPathVersioning",
    "VersioningMiddleware",
    "get_api_version",
]
