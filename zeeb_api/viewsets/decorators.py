"""ViewSet action decorator."""

from __future__ import annotations

from typing import Any, Callable, Sequence
from functools import wraps


def action(
    methods: Sequence[str] | None = None,
    detail: bool = True,
    url_path: str | None = None,
    url_name: str | None = None,
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
    """
    methods = methods or ["get"]
    methods = [m.upper() for m in methods]
    
    def decorator(func: Callable) -> Callable:
        func._action_config = {
            "methods": methods,
            "detail": detail,
            "url_path": url_path or func.__name__,
            "url_name": url_name or func.__name__,
            "kwargs": kwargs,
        }
        
        @wraps(func)
        async def wrapper(*args: Any, **kw: Any) -> Any:
            return await func(*args, **kw)
        
        # Preserve action config on wrapper
        wrapper._action_config = func._action_config
        return wrapper
    
    return decorator
