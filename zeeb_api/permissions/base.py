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
        if user is None or not getattr(user, "is_authenticated", True):
            return False
        user_id = getattr(user, "id", None)
        if user_id is None:
            user_id = getattr(user, "pk", None)
        if user_id is None:
            return False

        owner_id = _owner_id(obj, self.owner_field)
        if owner_id is None and self.owner_field != "user":
            # Try 'user' as fallback
            owner_id = _owner_id(obj, "user")
        if owner_id is None:
            return False

        # Compare as strings: a database user's id is a UUID, while a
        # token-only user carries the ``sub`` claim as a string.
        return str(owner_id) == str(user_id)


def _owner_id(obj: Any, field: str) -> Any:
    """The id stored in ``obj.<field>``, without loading a relation.

    For a foreign key the column value (``<field>_id``) is the answer. Reading
    ``obj.<field>`` instead returns a ``ForeignKeyLazyLoader`` when the
    relation is not loaded — an object with no ``id`` of its own — so every
    owner used to be denied. A loaded related instance yields its ``pk``; a
    plain scalar attribute (``owner = CharField()``) is used as is.
    """
    column_value = getattr(obj, f"{field}_id", None)
    if column_value is not None:
        return column_value
    value = getattr(obj, field, None)
    if value is None:
        return None
    fk_id = getattr(value, "_fk_id", None)
    if fk_id is not None:  # an unloaded ForeignKeyLazyLoader
        return fk_id
    for attr in ("pk", "id"):
        related = getattr(value, attr, None)
        if related is not None:
            return related
    return value


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


class ModelPermissions(BasePermission):
    """
    Permission class tied to per-model permissions.

    Maps HTTP methods to permission names:
    - GET, HEAD, OPTIONS: view_<model>
    - POST: add_<model>
    - PUT, PATCH: change_<model>
    - DELETE: delete_<model>

    ``POST /query`` only reads, so it requires ``view_<model>`` rather than
    the ``add_<model>`` its HTTP method would suggest (``action_perms_map``).
    A method missing from ``perms_map`` is denied rather than let through.
    """
    
    # Permission codenames follow the standard add_/change_/delete_/view_<model>
    # convention. They are matched against ``Permission.codename`` (which carries
    # no app-label prefix in this ORM), via the user's ``has_perm_async``.
    perms_map = {
        "GET": ["view_%(model_name)s"],
        "OPTIONS": [],
        "HEAD": [],
        "POST": ["add_%(model_name)s"],
        "PUT": ["change_%(model_name)s"],
        "PATCH": ["change_%(model_name)s"],
        "DELETE": ["delete_%(model_name)s"],
    }

    # Actions whose meaning is not their HTTP method. Checked before perms_map.
    action_perms_map = {
        "query": ["view_%(model_name)s"],
    }

    async def has_permission(self, request: Request, view: ViewSet) -> bool:
        user = getattr(request.state, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False

        model = self._get_model(view)
        if model is None:
            # Nothing to check the permission against: a misconfigured view
            # must not be an open one.
            return False

        # Get required permissions (None: a method this class does not know)
        perms = self._get_required_permissions(
            request.method, model, action=getattr(view, "action", None)
        )
        if perms is None:
            return False
        if not perms:
            return True

        # Verify each required permission against the DB-backed user. A user
        # object without ``has_perm_async`` (e.g. a token-only AuthenticatedUser
        # with no database row) cannot have model permissions verified, so deny.
        check = getattr(user, "has_perm_async", None)
        if not callable(check):
            return False
        for perm in perms:
            if not await check(perm):
                return False
        return True

    @staticmethod
    def _get_model(view: ViewSet) -> Any:
        """The model behind ``view.queryset`` (or ``view.get_queryset()``)."""
        queryset = getattr(view, "queryset", None)
        if queryset is None and callable(getattr(view, "get_queryset", None)):
            try:
                queryset = view.get_queryset()
            except Exception:
                queryset = None
        return getattr(queryset, "model", None) if queryset is not None else None

    def _get_required_permissions(
        self, method: str, model: Any, action: str | None = None
    ) -> list[str] | None:
        """The permission codenames a request needs, or None if the method is unknown."""
        kwargs = {
            "model_name": model.__name__.lower(),
        }

        if action is not None and action in self.action_perms_map:
            perms = self.action_perms_map[action]
        else:
            perms = self.perms_map.get(method.upper() if method else method)
            if perms is None:
                return None
        return [perm % kwargs for perm in perms]
