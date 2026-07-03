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
    DjangoModelPermissions,
)

__all__ = [
    "BasePermission",
    "AllowAny",
    "IsAuthenticated",
    "IsAdminUser",
    "IsAuthenticatedOrReadOnly",
    "IsOwner",
    "IsOwnerOrReadOnly",
    "DjangoModelPermissions",
]
