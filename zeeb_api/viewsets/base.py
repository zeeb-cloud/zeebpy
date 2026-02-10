"""Base ViewSet classes."""

from __future__ import annotations

from typing import Any, ClassVar, Sequence, TYPE_CHECKING
from fastapi import Request, HTTPException

if TYPE_CHECKING:
    from zeeb_api.serializers import Serializer
    from zeeb_api.permissions.base import BasePermission
    from zeeb_api.pagination.base import BasePagination
    from zeeb_api.filters.base import BaseFilter


class ViewSetMeta(type):
    """Metaclass to track action methods."""
    
    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> ViewSetMeta:
        cls = super().__new__(mcs, name, bases, namespace)
        
        # Collect action methods
        actions = {}
        for key, value in namespace.items():
            if callable(value) and hasattr(value, "_action_config"):
                actions[key] = value._action_config
        
        # Include actions from parent classes
        for base in bases:
            if hasattr(base, "_action_methods"):
                for key, config in base._action_methods.items():
                    if key not in actions:
                        actions[key] = config
        
        cls._action_methods = actions
        return cls


class ViewSet(metaclass=ViewSetMeta):
    """
    Base ViewSet class.
    
    A ViewSet is a class-based view that combines related views
    for a resource. It doesn't provide any actions by default.
    """
    
    _action_methods: ClassVar[dict[str, dict[str, Any]]]
    
    # Configuration
    permission_classes: ClassVar[list[type[BasePermission]]] = []
    
    def __init__(self, request: Request | None = None, **kwargs: Any) -> None:
        self.request = request
        self.kwargs = kwargs
        self.action: str | None = None
        self._action_permission_classes: list[type[BasePermission]] | None = None
    
    def get_permissions(self) -> list[BasePermission]:
        """
        Get permission instances for this view.
        
        If the current action has specific permissions set via @action decorator,
        those take precedence over the class-level permission_classes.
        """
        # Use action-specific permissions if set
        if self._action_permission_classes is not None:
            return [permission() for permission in self._action_permission_classes]
        return [permission() for permission in self.permission_classes]
    
    async def check_permissions(self, request: Request) -> None:
        """Check if the request should be permitted."""
        for permission in self.get_permissions():
            if not await permission.has_permission(request, self):
                from zeeb_api.response import PermissionDenied
                raise PermissionDenied(
                    getattr(permission, "message", "Permission denied")
                )
    
    async def check_object_permissions(self, request: Request, obj: Any) -> None:
        """Check if the request should be permitted for a specific object."""
        for permission in self.get_permissions():
            if not await permission.has_object_permission(request, self, obj):
                from zeeb_api.response import PermissionDenied
                raise PermissionDenied(
                    getattr(permission, "message", "Permission denied")
                )
    
    def get_action_request_body(self) -> dict[str, Any] | None:
        """
        Get the validated request body for the current action.
        
        Returns None if no request body was provided or validated.
        """
        return getattr(self, "_request_body", None)


class GenericViewSet(ViewSet):
    """
    GenericViewSet with queryset and serializer support.
    
    Provides common functionality for model-based viewsets.
    """
    
    # Configuration
    queryset: Any = None
    serializer_class: type[Serializer] | None = None
    lookup_field: str = "id"
    lookup_url_kwarg: str | None = None
    pagination_class: type[BasePagination] | None = None
    filter_backends: list[type[BaseFilter]] = []
    
    # Object permission configuration
    use_object_permissions: bool = False
    
    # Map actions to permission types for queryset filtering
    _action_permission_map: ClassVar[dict[str, str]] = {
        "list": "read",
        "retrieve": "read",
        "create": "add",
        "update": "change",
        "partial_update": "change",
        "destroy": "delete",
    }
    
    @classmethod
    def get_response_schema(cls) -> type | None:
        """Get Pydantic response schema for FastAPI docs."""
        serializer_class = cls.serializer_class
        if serializer_class and hasattr(serializer_class, "ResponseSchema"):
            return serializer_class.ResponseSchema
        return None
    
    @classmethod
    def get_request_schema(cls) -> type | None:
        """Get Pydantic request schema for FastAPI docs."""
        serializer_class = cls.serializer_class
        if serializer_class and hasattr(serializer_class, "RequestSchema"):
            return serializer_class.RequestSchema
        return None
    
    @classmethod
    def get_list_response_schema(cls) -> type | None:
        """Get paginated list response schema."""
        response_schema = cls.get_response_schema()
        if response_schema:
            from zeeb_api.serializers.pydantic import create_list_response_schema
            return create_list_response_schema(response_schema)
        return None
    
    def _get_permission_type_for_action(self) -> str | None:
        """Get the permission type for the current action."""
        if self.action is None:
            return None
        return self._action_permission_map.get(self.action)
    
    def _get_user_from_request(self) -> Any:
        """Extract user from request, returns None for anonymous."""
        if self.request is None:
            return None
        return getattr(self.request, "user", None)
    
    def get_queryset(self) -> Any:
        """
        Get the queryset for this view.
        
        If use_object_permissions is True and the model has permission rules,
        automatically filters the queryset based on the current action.
        
        Override to customize queryset (e.g., filter by user).
        """
        if self.queryset is None:
            raise ValueError(
                f"{self.__class__.__name__} should include a `queryset` attribute "
                "or override `get_queryset()`"
            )
        
        queryset = self.queryset
        
        # Apply object permission filtering if enabled
        if self.use_object_permissions:
            queryset = self._apply_permission_filter(queryset)
        
        return queryset
    
    def _apply_permission_filter(self, queryset: Any) -> Any:
        """Apply permission filter to queryset based on current action."""
        permission_type = self._get_permission_type_for_action()
        if permission_type is None:
            return queryset
        
        # Skip add permission - it's class-level, not queryset filter
        if permission_type == "add":
            return queryset
        
        # Check if queryset supports permission filtering
        if hasattr(queryset, "with_permission"):
            user = self._get_user_from_request()
            return queryset.with_permission(user, permission_type)
        
        return queryset
    
    def get_serializer_class(self) -> type[Serializer]:
        """Get the serializer class for this view."""
        if self.serializer_class is None:
            raise ValueError(
                f"{self.__class__.__name__} should include a `serializer_class` attribute "
                "or override `get_serializer_class()`"
            )
        return self.serializer_class
    
    def get_serializer(
        self,
        instance: Any = None,
        data: Any = None,
        many: bool = False,
        partial: bool = False,
        **kwargs: Any,
    ) -> Serializer:
        """Get a serializer instance."""
        serializer_class = self.get_serializer_class()
        kwargs["context"] = self.get_serializer_context()
        
        return serializer_class(
            instance=instance,
            data=data,
            many=many,
            partial=partial,
            **kwargs,
        )
    
    def get_serializer_context(self) -> dict[str, Any]:
        """Get context to pass to serializer."""
        return {
            "request": self.request,
            "view": self,
        }
    
    async def get_object(self) -> Any:
        """Get the object for detail views."""
        queryset = self.get_queryset()
        
        # Get lookup value
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        lookup_value = self.kwargs.get(lookup_url_kwarg)
        
        if lookup_value is None:
            from zeeb_api.response import NotFound
            raise NotFound("Object not found")
        
        # Build filter
        filter_kwargs = {self.lookup_field: lookup_value}
        
        # Get object
        try:
            obj = await queryset.filter(**filter_kwargs).first()
        except Exception:
            obj = None
        
        if obj is None:
            from zeeb_api.response import NotFound
            raise NotFound("Object not found")
        
        # Check object permissions
        await self.check_object_permissions(self.request, obj)
        
        return obj
    
    async def check_object_permissions(self, request: Request, obj: Any) -> None:
        """
        Check if the request should be permitted for a specific object.
        
        First checks class-level permissions, then object-level permissions
        if use_object_permissions is enabled.
        """
        # Check class-level permissions
        for permission in self.get_permissions():
            if not await permission.has_object_permission(request, self, obj):
                from zeeb_api.response import PermissionDenied
                raise PermissionDenied(
                    getattr(permission, "message", "Permission denied")
                )
        
        # Check model-level object permissions if enabled
        if self.use_object_permissions:
            await self._check_model_object_permission(request, obj)
    
    async def _check_model_object_permission(self, request: Request, obj: Any) -> None:
        """Check model's object permission for the current action."""
        permission_type = self._get_permission_type_for_action()
        if permission_type is None:
            return
        
        check_method_name = f"check_{permission_type}_permission"
        
        # Check if the object has the permission check method
        if not hasattr(obj, check_method_name):
            return
        
        check_method = getattr(obj, check_method_name)
        user = self._get_user_from_request()
        
        # Call the check method
        has_permission = await check_method(user)
        
        if not has_permission:
            from zeeb_api.response import PermissionDenied
            raise PermissionDenied(f"You do not have {permission_type} permission for this object")
    
    async def check_add_permission(self) -> None:
        """Check if user has add permission for the model."""
        if not self.use_object_permissions:
            return
        
        # Get the model class
        queryset = self.queryset
        if queryset is None:
            return
        
        model_class = getattr(queryset, "model", None)
        if model_class is None:
            return
        
        # Check if model has add permission check
        if not hasattr(model_class, "check_add_permission"):
            return
        
        user = self._get_user_from_request()
        has_permission = await model_class.check_add_permission(user)
        
        if not has_permission:
            from zeeb_api.response import PermissionDenied
            raise PermissionDenied("You do not have permission to create this object")
    
    def get_pagination_class(self) -> type[BasePagination] | None:
        """Get pagination class."""
        return self.pagination_class
    
    async def paginate_queryset(self, queryset: Any) -> tuple[list[Any], dict[str, Any]]:
        """
        Paginate the queryset if pagination is configured.
        
        Returns (paginated_items, pagination_info)
        """
        pagination_class = self.get_pagination_class()
        if pagination_class is None:
            items = await queryset.all()
            return items, {}
        
        paginator = pagination_class()
        return await paginator.paginate_queryset(queryset, self.request)
    
    def filter_queryset(self, queryset: Any) -> Any:
        """Apply filters to queryset."""
        for backend_class in self.filter_backends:
            backend = backend_class()
            queryset = backend.filter_queryset(self.request, queryset, self)
        return queryset
