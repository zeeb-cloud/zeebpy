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
    permission_type: str | None = None,
    status_code: int | None = None,
    responses: dict[int | str, dict[str, Any]] | None = None,
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
        permission_type: The ``use_object_permissions`` rule this action is
                scoped by: ``"read"``, ``"change"`` or ``"delete"``. Both
                ``self.get_queryset()`` and ``self.get_object()`` apply it.
                Default: derived from the HTTP method (GET/HEAD/OPTIONS ->
                read, DELETE -> delete, anything else -> change), so a
                POST that only reads must say ``permission_type="read"``.
        status_code: The success status the route documents (and answers
                with when the action returns plain data). Default 200. An
                action returning its own ``Response`` keeps that response's
                status, so set this to what it returns, e.g. ``202``.
        responses: Extra OpenAPI responses, merged over the router's own
                error responses, e.g.
                ``{409: error_response_doc(409, "Already published")}``.
    
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
    if permission_type is not None and permission_type not in ("read", "change", "delete"):
        raise ValueError(
            f"@action(permission_type={permission_type!r}): expected 'read', "
            "'change' or 'delete'"
        )
    
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
            "permission_type": permission_type,
            "status_code": status_code,
            "responses": responses,
            "kwargs": kwargs,
        }
        
        @wraps(func)
        async def wrapper(*args: Any, **kw: Any) -> Any:
            return await func(*args, **kw)
        
        # Preserve action config on wrapper
        wrapper._action_config = func._action_config
        return wrapper
    
    return decorator


def extend_schema(
    request_schema: type[BaseModel] | None = None,
    response_schema: type[BaseModel] | None = None,
    *,
    status_code: int | None = None,
    responses: dict[int | str, dict[str, Any]] | None = None,
) -> Callable:
    """
    Declare the OpenAPI shape of a viewset's built-in route.

    ``@action`` is for routes of your own; the routes a router generates for
    ``create``, ``update``, ``partial_update``, ``retrieve``, ``list`` and
    ``destroy`` take their schemas from a serializer, which a plain
    :class:`~zeeb_api.viewsets.ViewSet` does not have. Without one the route
    declares no body: the docs show none, nothing is validated, and the
    action has to parse ``request`` itself. ``extend_schema`` gives such a
    route what ``@action`` gives a custom one:

    Args:
        request_schema: Pydantic model of the body (``create``, ``update``,
                ``partial_update``). The router validates it, answering 422 on
                a bad body, and the action reads it from
                ``self.get_action_request_model()`` (or the dumped dict from
                ``self.get_action_request_body()``).
        response_schema: Pydantic model of the success response.
        status_code: The documented success status (``create`` defaults to
                201, ``destroy`` to 204, the rest to 200).
        responses: Extra OpenAPI responses, merged last.

    It overrides what a serializer would declare and does not create a route.

    Usage:
        class ProjectViewSet(ViewSet):
            @extend_schema(request_schema=CreateProject, response_schema=Project)
            async def create(self, request):
                body = self.get_action_request_model()
                ...
    """

    def decorator(func: Callable) -> Callable:
        func._schema_config = {
            "request_schema": request_schema,
            "response_schema": response_schema,
            "status_code": status_code,
            "responses": responses,
        }
        return func

    return decorator
