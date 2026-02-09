"""Base permission classes."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING
from fastapi import Request

if TYPE_CHECKING:
    from zeeb_api.viewsets.base import ViewSet


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class BasePermission:
    """
    Base permission class.
    
    Override `has_permission` and/or `has_object_permission` to implement
    custom permission logic.
    """
    
    message: str = "Permission denied."
    
    async def has_permission(self, request: Request, view: ViewSet) -> bool:
        """
        Return True if permission is granted for the request.
        
        This is called before any action is executed.
        """
        return True
    
    async def has_object_permission(
        self,
        request: Request,
        view: ViewSet,
        obj: Any,
    ) -> bool:
        """
        Return True if permission is granted for the specific object.
        
        This is called after has_permission(), for detail views.
        """
        return True


class AllowAny(BasePermission):
    """
    Allow any access.
    
    This permission class will allow unrestricted access,
    regardless of authentication status.
    """
    
    async def has_permission(self, request: Request, view: ViewSet) -> bool:
        return True


class IsAuthenticated(BasePermission):
    """
    Require authentication.
    
    Access is allowed only to authenticated users.
    """
    
    message = "Authentication required."
    
    async def has_permission(self, request: Request, view: ViewSet) -> bool:
        # Check if user is authenticated
        # FastAPI stores user in request.state after authentication
        user = getattr(request.state, "user", None)
        return user is not None and getattr(user, "is_authenticated", True)


class IsAdminUser(BasePermission):
    """
    Require admin/staff status.
    
    Access is allowed only to admin/staff users.
    """
    
    message = "Admin access required."
    
    async def has_permission(self, request: Request, view: ViewSet) -> bool:
        user = getattr(request.state, "user", None)
        if user is None:
            return False
        
        return (
            getattr(user, "is_staff", False) or
            getattr(user, "is_admin", False) or
            getattr(user, "is_superuser", False)
        )


class IsAuthenticatedOrReadOnly(BasePermission):
    """
    Allow read-only access for unauthenticated users.
    
    Write operations (POST, PUT, PATCH, DELETE) require authentication.
    """
    
    message = "Authentication required for write operations."
    
    async def has_permission(self, request: Request, view: ViewSet) -> bool:
        # Safe methods (GET, HEAD, OPTIONS) are always allowed
        if request.method in SAFE_METHODS:
            return True
        
        # Write methods require authentication
        user = getattr(request.state, "user", None)
        return user is not None and getattr(user, "is_authenticated", True)


class IsOwner(BasePermission):
    """
    Object-level permission to only allow owners of an object.
    
    Assumes the model has an `owner` or `user` attribute.
    """
    
    message = "You do not have permission to access this object."
    owner_field = "owner"  # Can be overridden
    
    async def has_object_permission(
        self,
        request: Request,
        view: ViewSet,
        obj: Any,
    ) -> bool:
        user = getattr(request.state, "user", None)
        if user is None:
            return False
        
        # Check owner field
        owner = getattr(obj, self.owner_field, None)
        if owner is None:
            # Try 'user' as fallback
            owner = getattr(obj, "user", None)
        
        if owner is None:
            return False
        
        # Compare by ID if available
        owner_id = getattr(owner, "id", owner)
        user_id = getattr(user, "id", user)
        
        return owner_id == user_id


class IsOwnerOrReadOnly(IsOwner):
    """
    Object-level permission allowing read-only for non-owners.
    """
    
    async def has_object_permission(
        self,
        request: Request,
        view: ViewSet,
        obj: Any,
    ) -> bool:
        # Safe methods allowed for everyone
        if request.method in SAFE_METHODS:
            return True
        
        # Write methods require ownership
        return await super().has_object_permission(request, view, obj)


class DjangoModelPermissions(BasePermission):
    """
    Permission class tied to Django-style model permissions.
    
    Maps HTTP methods to permission names:
    - GET, HEAD, OPTIONS: view_<model>
    - POST: add_<model>
    - PUT, PATCH: change_<model>
    - DELETE: delete_<model>
    """
    
    perms_map = {
        "GET": ["%(app_label)s.view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": [],
        "POST": ["%(app_label)s.add_%(model_name)s"],
        "PUT": ["%(app_label)s.change_%(model_name)s"],
        "PATCH": ["%(app_label)s.change_%(model_name)s"],
        "DELETE": ["%(app_label)s.delete_%(model_name)s"],
    }
    
    async def has_permission(self, request: Request, view: ViewSet) -> bool:
        user = getattr(request.state, "user", None)
        if user is None:
            return False
        
        # Get model info
        queryset = getattr(view, "queryset", None)
        if queryset is None:
            return True
        
        model = getattr(queryset, "model", None)
        if model is None:
            return True
        
        # Get required permissions
        perms = self._get_required_permissions(request.method, model)
        
        # Check user has permissions
        user_perms = getattr(user, "permissions", set())
        return all(perm in user_perms for perm in perms)
    
    def _get_required_permissions(self, method: str, model: Any) -> list[str]:
        """Get the required permissions for a method."""
        kwargs = {
            "app_label": getattr(model._meta, "app_label", "app"),
            "model_name": model.__name__.lower(),
        }
        
        perms = self.perms_map.get(method, [])
        return [perm % kwargs for perm in perms]
