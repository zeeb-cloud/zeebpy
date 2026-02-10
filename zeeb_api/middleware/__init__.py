"""
Zeeb API middleware module.

Provides Django-style middleware configuration.
"""

from zeeb_api.middleware.auth import (
    JWTAuthMiddleware,
    AuthenticatedUser,
    get_current_user,
    get_current_user_optional,
    require_auth,
)
from zeeb_api.middleware.cors import CORSMiddleware
from zeeb_api.middleware.loader import install_middleware

__all__ = [
    "JWTAuthMiddleware",
    "AuthenticatedUser",
    "get_current_user",
    "get_current_user_optional",
    "require_auth",
    "CORSMiddleware",
    "install_middleware",
]
