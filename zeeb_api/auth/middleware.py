"""
JWT authentication middleware and FastAPI dependencies.

The implementation now lives in :mod:`zeeb_api.middleware.auth` (the module the
default project scaffold installs as ``zeeb_api.middleware.JWTAuthMiddleware``).
This module re-exports it so the historical ``zeeb_api.auth.middleware`` import
path keeps working and both paths resolve to the exact same classes — avoiding
the earlier split where only one of the two set ``request.state.auth_error`` /
accepted external OAuth tokens.
"""

from __future__ import annotations

from zeeb_api.middleware.auth import (
    AuthenticatedUser,
    JWTAuthMiddleware,
    UserLoaderFunc,
    bearer_scheme,
    default_user_loader,
    get_current_user,
    get_current_user_optional,
    require_auth,
)

__all__ = [
    "AuthenticatedUser",
    "JWTAuthMiddleware",
    "UserLoaderFunc",
    "bearer_scheme",
    "default_user_loader",
    "get_current_user",
    "get_current_user_optional",
    "require_auth",
]
