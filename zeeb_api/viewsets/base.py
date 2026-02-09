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
    
    def get_permissions(self) -> list[BasePermission]:
        """Get permission instances for this view."""
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
    
    def get_queryset(self) -> Any:
        """
        Get the queryset for this view.
        
        Override to customize queryset (e.g., filter by user).
        """
        if self.queryset is None:
            raise ValueError(
                f"{self.__class__.__name__} should include a `queryset` attribute "
                "or override `get_queryset()`"
            )
        return self.queryset
    
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
