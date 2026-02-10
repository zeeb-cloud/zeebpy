"""ViewSet action decorator."""

from __future__ import annotations

from typing import Any, Callable, Sequence, TYPE_CHECKING

from functools import wraps

if TYPE_CHECKING:
    from pydantic import BaseModel
    from zeeb_api.serializers import Serializer
    from zeeb_api.permissions.base import BasePermission


def action(
    methods: Sequence[str] | None = None,
    detail: bool = True,
    url_path: str | None = None,
    url_name: str | None = None,
    request_schema: type[BaseModel] | None = None,
    response_schema: type[BaseModel] | None = None,
    request_serializer: type[Serializer] | None = None,
    response_serializer: type[Serializer] | None = None,
    permission_classes: list[type[BasePermission]] | None = None,
    **kwargs: Any,
) -> Callable:
    """
    Mark a ViewSet method as a routable action.
    
    Args:
        methods: HTTP methods to respond to (default: ["get"])
        detail: If True, action operates on a single object (requires pk)
                If False, action operates on the collection
        url_path: Custom URL path segment (default: method name)
        url_name: Custom URL name (default: method name)
        request_schema: Pydantic model for request body validation
        response_schema: Pydantic model for response (OpenAPI docs)
        request_serializer: Custom Serializer class for request validation
        response_serializer: Custom Serializer class for response
        permission_classes: Override permissions for this action
    
    Usage:
        class UserViewSet(ModelViewSet):
            queryset = User.objects.all()
            serializer_class = UserSerializer
            
            @action(detail=True, methods=["post"])
            async def activate(self, request, pk=None):
                user = await self.get_object()
                user.is_active = True
                await user.save()
                return {"status": "activated"}
            
            @action(detail=False, methods=["get"])
            async def recent(self, request):
                users = await self.get_queryset().order_by("-created_at")[:10]
                serializer = self.get_serializer(users, many=True)
                return serializer.data
            
            # With request/response schemas
            @action(
                detail=False,
                methods=["post"],
                request_schema=SendEmailRequest,
                response_schema=SendEmailResponse,
            )
            async def send_email(self, request):
                data = self._request_body  # Pre-validated
                return {"queued": True, "job_id": "abc123"}
            
            # With action-specific permissions
            @action(
                detail=True,
                methods=["post"],
                permission_classes=[IsAdminUser],
            )
            async def approve(self, request, pk=None):
                ...
    """
    methods = methods or ["get"]
    methods = [m.upper() for m in methods]
    
    def decorator(func: Callable) -> Callable:
        func._action_config = {
            "methods": methods,
            "detail": detail,
            "url_path": url_path or func.__name__,
            "url_name": url_name or func.__name__,
            "request_schema": request_schema,
            "response_schema": response_schema,
            "request_serializer": request_serializer,
            "response_serializer": response_serializer,
            "permission_classes": permission_classes,
            "kwargs": kwargs,
        }
        
        @wraps(func)
        async def wrapper(*args: Any, **kw: Any) -> Any:
            return await func(*args, **kw)
        
        # Preserve action config on wrapper
        wrapper._action_config = func._action_config
        return wrapper
    
    return decorator
