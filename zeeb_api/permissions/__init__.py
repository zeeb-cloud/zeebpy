"""
Permissions - DRF-like permission classes.
"""

from zeeb_api.permissions.base import (
    BasePermission,
    AllowAny,
    IsAuthenticated,
    IsAdminUser,
    IsAuthenticatedOrReadOnly,
    IsOwner,
    IsOwnerOrReadOnly,
    ModelPermissions,
)

__all__ = [
    "BasePermission",
    "AllowAny",
    "IsAuthenticated",
    "IsAdminUser",
    "IsAuthenticatedOrReadOnly",
    "IsOwner",
    "IsOwnerOrReadOnly",
    "ModelPermissions",
]
