"""
Authentication - DRF-like per-viewset authentication classes.
"""

from zeeb_api.authentication.base import (
    BaseAuthentication,
    JWTAuthentication,
    JWTStatelessAuthentication,
    OAuth2BearerAuthentication,
)

__all__ = [
    "BaseAuthentication",
    "JWTAuthentication",
    "JWTStatelessAuthentication",
    "OAuth2BearerAuthentication",
]
