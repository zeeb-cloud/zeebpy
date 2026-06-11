"""Default router implementation."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Type
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from zeeb_api.viewsets.base import ViewSet


class Route:
    """Route configuration for a ViewSet action."""
    
    def __init__(
        self,
        url: str,
        mapping: dict[str, str],
        name: str,
        detail: bool,
        initkwargs: dict[str, Any] | None = None,
    ) -> None:
        self.url = url
        self.mapping = mapping  # {http_method: action_name}
        self.name = name
        self.detail = detail
        self.initkwargs = initkwargs or {}


class SimpleRouter:
    """
    Simple router that generates routes for ViewSets.
    
    Routes generated:
    - {prefix}/query/ (query with Q filters) - POST
    - {prefix}/ (create) - POST
    - {prefix}/{lookup}/ (retrieve, update, partial_update, destroy)
    - {prefix}/{lookup}/{action}/ (custom detail actions)
    - {prefix}/{action}/ (custom list actions)
    """
    
    # Default route patterns
    default_routes = [
        # Query route (replaces list with Q filter support)
        Route(
            url="/query",
            mapping={
                "post": "query",
            },
            name="{basename}-query",
            detail=False,
        ),
        # Create route
        Route(
            url="",
            mapping={
                "post": "create",
            },
            name="{basename}-create",
            detail=False,
        ),
        # Detail route
        Route(
            url="/{lookup}",
            mapping={
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            },
            name="{basename}-detail",
            detail=True,
        ),
    ]
    
    def __init__(self) -> None:
        self._registry: list[tuple[str, Type[ViewSet], str]] = []
        self._routes: list[APIRouter] = []
    
    def register(
        self,
        prefix: str,
        viewset: Type[ViewSet],
        basename: str | None = None,
    ) -> None:
        """
        Register a ViewSet with a URL prefix.
        
        Args:
            prefix: URL prefix (e.g., "users")
            viewset: ViewSet class
            basename: Base name for URL names (default: prefix)
        """
        if basename is None:
            basename = prefix.strip("/").replace("/", "-")
        
        self._registry.append((prefix, viewset, basename))
    
    def _get_lookup_regex(self, viewset: Type[ViewSet]) -> str:
        """Get the lookup field pattern."""
        lookup_field = getattr(viewset, "lookup_field", "id")
        # For now, use a simple pattern that accepts int or string
        return "{" + lookup_field + "}"
    
    def _get_routes(self, viewset: Type[ViewSet]) -> list[Route]:
        """Get all routes for a ViewSet including custom actions.
        
        IMPORTANT: Routes are ordered so that non-detail custom actions come BEFORE
        the detail route (/{lookup}), ensuring FastAPI matches /posts/featured before
        /posts/{id}. Detail custom actions come AFTER the detail route.
        """
        # Collect custom actions
        non_detail_actions: list[Route] = []
        detail_actions: list[Route] = []
        
        for method_name in dir(viewset):
            method = getattr(viewset, method_name, None)
            if method is None:
                continue
            
            action_config = getattr(method, "_action_config", None)
            if action_config is None:
                continue
            
            # Build route for custom action
            if action_config["detail"]:
                url = "/{lookup}/" + action_config["url_path"]
            else:
                url = "/" + action_config["url_path"]
            
            mapping = {m.lower(): method_name for m in action_config["methods"]}
            
            route = Route(
                url=url,
                mapping=mapping,
                name="{basename}-" + action_config["url_name"],
                detail=action_config["detail"],
            )
            
            if action_config["detail"]:
                detail_actions.append(route)
            else:
                non_detail_actions.append(route)
        
        # Build final routes list with correct ordering:
        # 1. Query route (/query)
        # 2. Create route (/)
        # 3. Non-detail custom actions (/featured, /my_posts) - BEFORE /{lookup}
        # 4. Detail route (/{lookup})
        # 5. Detail custom actions (/{lookup}/like, /{lookup}/publish) - AFTER /{lookup}
        routes: list[Route] = []
        
        # Add default routes, inserting non-detail actions before detail route
        for route in self.default_routes:
            if route.detail:
                # Insert non-detail custom actions before the detail route
                routes.extend(non_detail_actions)
            routes.append(route)
        
        # Add detail custom actions after the detail route
        routes.extend(detail_actions)
        
        return routes
    
    def get_urls(self) -> list[APIRouter]:
        """Generate FastAPI routers for all registered ViewSets."""
        routers = []
        
        for prefix, viewset, basename in self._registry:
            router = self._get_router_for_viewset(prefix, viewset, basename)
            routers.append(router)
        
        return routers
    
    def _get_router_for_viewset(
        self,
        prefix: str,
        viewset: Type[ViewSet],
        basename: str,
    ) -> APIRouter:
        """Generate a FastAPI router for a ViewSet."""
        from zeeb_api.query import QueryRequest, create_query_response_model
        
        router = APIRouter(prefix=f"/{prefix.strip('/')}", tags=[basename])
        lookup = self._get_lookup_regex(viewset)
        
        # Get default schemas from viewset (used as fallback)
        default_response_schema = None
        default_request_schema = None
        query_response_schema = None
        
        if hasattr(viewset, "get_response_schema"):
            default_response_schema = viewset.get_response_schema()
        if hasattr(viewset, "get_request_schema"):
            default_request_schema = viewset.get_request_schema()
        
        # Create query response schema if we have a response schema
        if default_response_schema:
            query_response_schema = create_query_response_model(default_response_schema)
        
        # Get the lookup field type from the model
        lookup_type = self._get_lookup_type(viewset)
        
        for route in self._get_routes(viewset):
            # Replace {lookup} with actual lookup field
            url = route.url.replace("{lookup}", lookup)
            
            # Create endpoint for each HTTP method
            for method, action_name in route.mapping.items():
                # Check if viewset has this action
                if not hasattr(viewset, action_name):
                    continue
                
                # Get action config if this is a custom action
                action_method = getattr(viewset, action_name, None)
                action_config = getattr(action_method, "_action_config", None) if action_method else None
                
                # Get action-specific schemas by checking if viewset has get_serializer_class
                action_response_schema = default_response_schema
                action_request_schema = default_request_schema
                
                # Try to get action-specific serializer
                if hasattr(viewset, "get_serializer_class"):
                    # Create temp instance to get action-specific serializer
                    temp_viewset = object.__new__(viewset)
                    temp_viewset.action = action_name
                    try:
                        serializer_class = temp_viewset.get_serializer_class()
                        if serializer_class:
                            # Instantiate to trigger schema generation
                            serializer_class(data={})
                            if hasattr(serializer_class, "ResponseSchema"):
                                action_response_schema = serializer_class.ResponseSchema
                            if hasattr(serializer_class, "RequestSchema"):
                                action_request_schema = serializer_class.RequestSchema
                    except Exception:
                        pass  # Fall back to default schemas
                
                # Determine response model and request schema based on action
                action_response_model = None
                final_request_schema = None
                action_permission_classes = None
                
                if action_name == "query":
                    # Query uses QueryRequest and QueryResponse
                    action_response_model = query_response_schema
                    final_request_schema = QueryRequest
                elif action_name in ("retrieve",) and action_response_schema:
                    action_response_model = action_response_schema
                elif action_name == "create":
                    action_response_model = action_response_schema
                    final_request_schema = action_request_schema
                elif action_name in ("update", "partial_update"):
                    # For updates, use response schema but no request schema validation
                    # This allows partial updates (PATCH) without requiring all fields
                    action_response_model = action_response_schema
                    # Don't set final_request_schema - let viewset handle validation
                elif action_config:
                    # Custom action - check for schemas/serializers in action config
                    final_request_schema, action_response_model = self._get_action_schemas(
                        action_config, method
                    )
                    action_permission_classes = action_config.get("permission_classes")
                
                # Create the endpoint function
                endpoint = self._create_endpoint(
                    viewset, action_name, route.detail, lookup,
                    final_request_schema, lookup_type, action_permission_classes,
                )
                
                # Register with FastAPI router
                route_name = route.name.format(basename=basename)
                
                router.add_api_route(
                    url,
                    endpoint,
                    methods=[method.upper()],
                    name=route_name,
                    response_model=action_response_model,
                )
        
        return router
    
    def _get_action_schemas(
        self,
        action_config: dict[str, Any],
        method: str,
    ) -> tuple[type | None, type | None]:
        """
        Extract request and response schemas from action config.
        
        Supports both Pydantic models and custom Serializers.
        Returns (request_schema, response_schema).
        """
        from pydantic import BaseModel
        
        request_schema = None
        response_schema = None
        
        # Request schema: Pydantic takes precedence, then Serializer
        if action_config.get("request_schema"):
            request_schema = action_config["request_schema"]
        elif action_config.get("request_serializer"):
            serializer_class = action_config["request_serializer"]
            # Instantiate to trigger schema generation
            serializer_class(data={})
            if hasattr(serializer_class, "RequestSchema"):
                request_schema = serializer_class.RequestSchema
        
        # Response schema: Pydantic takes precedence, then Serializer
        if action_config.get("response_schema"):
            response_schema = action_config["response_schema"]
        elif action_config.get("response_serializer"):
            serializer_class = action_config["response_serializer"]
            # Instantiate to trigger schema generation
            serializer_class(data={})
            if hasattr(serializer_class, "ResponseSchema"):
                response_schema = serializer_class.ResponseSchema
        
        # Only use request schema for POST/PUT/PATCH methods
        if method.upper() not in ("POST", "PUT", "PATCH"):
            request_schema = None
        
        return request_schema, response_schema
    
    def _get_lookup_type(self, viewset: Type[ViewSet]) -> type:
        """Get the Python type for the lookup field (PK type)."""
        # Try to get from model's _meta.pk
        model = getattr(viewset, "model", None)
        if model is None:
            # Try to infer from queryset
            queryset = getattr(viewset, "queryset", None)
            if queryset is not None and hasattr(queryset, "_model"):
                model = queryset._model
            elif queryset is not None and hasattr(queryset, "_original_model"):
                model = queryset._original_model
        
        if model is not None and hasattr(model, "_meta"):
            pk_field = model._meta.pk
            if pk_field is not None:
                python_type = getattr(pk_field, "_python_type", None)
                if python_type is not None:
                    return python_type
        
        # Default to UUID (new default)
        return uuid.UUID
    
    def _create_endpoint(
        self,
        viewset_class: Type[ViewSet],
        action_name: str,
        detail: bool,
        lookup: str,
        request_schema: type | None = None,
        lookup_type: type = uuid.UUID,
        permission_classes: list | None = None,
    ) -> Callable:
        """Create a FastAPI endpoint function for a ViewSet action."""
        from pydantic import BaseModel
        
        lookup_field = lookup.strip("{}")
        
        if detail:
            if request_schema:
                # Detail endpoint with request body
                async def detail_endpoint_with_body(
                    request: Request,
                    body: BaseModel,  # Will be overridden by signature
                    **path_params: Any,
                ) -> Any:
                    viewset = viewset_class(request=request, **path_params)
                    viewset.action = action_name
                    viewset.kwargs = path_params
                    
                    # Use action-specific permissions if provided
                    if permission_classes is not None:
                        viewset._action_permission_classes = permission_classes
                    
                    await viewset.check_permissions(request)
                    viewset.version = getattr(request.state, "version", None)
                    await viewset.check_throttles(request)

                    # Store body data for action
                    viewset._request_body = body.model_dump()
                    
                    action = getattr(viewset, action_name)
                    result = await action(request, **path_params)
                    
                    if result is None:
                        return Response(status_code=204)
                    return result
                
                # Build signature with typed body
                from inspect import Parameter, Signature
                params = [
                    Parameter("request", Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
                    Parameter(lookup_field, Parameter.POSITIONAL_OR_KEYWORD, annotation=lookup_type),
                    Parameter("body", Parameter.POSITIONAL_OR_KEYWORD, annotation=request_schema),
                ]
                detail_endpoint_with_body.__signature__ = Signature(params)  # type: ignore
                return detail_endpoint_with_body
            else:
                async def detail_endpoint(request: Request, **path_params: Any) -> Any:
                    viewset = viewset_class(request=request, **path_params)
                    viewset.action = action_name
                    viewset.kwargs = path_params
                    
                    # Use action-specific permissions if provided
                    if permission_classes is not None:
                        viewset._action_permission_classes = permission_classes
                    
                    await viewset.check_permissions(request)
                    viewset.version = getattr(request.state, "version", None)
                    await viewset.check_throttles(request)

                    action = getattr(viewset, action_name)
                    result = await action(request, **path_params)
                    
                    if result is None:
                        return Response(status_code=204)
                    return result
                
                from inspect import Parameter, Signature
                params = [
                    Parameter("request", Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
                    Parameter(lookup_field, Parameter.POSITIONAL_OR_KEYWORD, annotation=lookup_type),
                ]
                detail_endpoint.__signature__ = Signature(params)  # type: ignore
                return detail_endpoint
        else:
            if request_schema:
                # Non-detail endpoint with request body (create, query, custom actions)
                async def list_endpoint_with_body(
                    request: Request,
                    body: BaseModel,
                ) -> Any:
                    viewset = viewset_class(request=request)
                    viewset.action = action_name
                    viewset.kwargs = {}
                    
                    # Use action-specific permissions if provided
                    if permission_classes is not None:
                        viewset._action_permission_classes = permission_classes
                    
                    await viewset.check_permissions(request)
                    viewset.version = getattr(request.state, "version", None)
                    await viewset.check_throttles(request)

                    # Store body data for serializer/query
                    viewset._request_body = body.model_dump()
                    
                    action = getattr(viewset, action_name)
                    result = await action(request)
                    
                    if result is None:
                        return Response(status_code=204)
                    return result
                
                from inspect import Parameter, Signature
                params = [
                    Parameter("request", Parameter.POSITIONAL_OR_KEYWORD, annotation=Request),
                    Parameter("body", Parameter.POSITIONAL_OR_KEYWORD, annotation=request_schema),
                ]
                list_endpoint_with_body.__signature__ = Signature(params)  # type: ignore
                return list_endpoint_with_body
            else:
                async def list_endpoint(request: Request) -> Any:
                    viewset = viewset_class(request=request)
                    viewset.action = action_name
                    viewset.kwargs = {}
                    
                    # Use action-specific permissions if provided
                    if permission_classes is not None:
                        viewset._action_permission_classes = permission_classes
                    
                    await viewset.check_permissions(request)
                    viewset.version = getattr(request.state, "version", None)
                    await viewset.check_throttles(request)

                    action = getattr(viewset, action_name)
                    result = await action(request)
                    
                    if result is None:
                        return Response(status_code=204)
                    return result
                
                return list_endpoint
    
    @property
    def routes(self) -> list[APIRouter]:
        """Get all generated routes."""
        if not self._routes:
            self._routes = self.get_urls()
        return self._routes


class DefaultRouter(SimpleRouter):
    """
    Router with additional features:
    - API root view
    - Format suffix support (optional)
    """
    
    include_root_view: bool = True
    include_format_suffixes: bool = False
    
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self.include_root_view = kwargs.get("include_root_view", True)
        self._api_routers: list[tuple[str, APIRouter]] = []
    
    def _get_root_router(self) -> APIRouter:
        """Create a root router with API index."""
        router = APIRouter()
        
        @router.get("/", name="api-root")
        async def api_root(request: Request) -> dict[str, str]:
            """API root - list available endpoints."""
            base_url = str(request.base_url).rstrip("/")
            
            return {
                basename: f"{base_url}/{prefix.strip('/')}/"
                for prefix, _, basename in self._registry
            }
        
        return router
    
    def include(
        self,
        router: "SimpleRouter | DefaultRouter | APIRouter",
        prefix: str = "",
    ) -> None:
        """
        Include another router's registrations or an APIRouter.
        
        Args:
            router: Router to include (SimpleRouter, DefaultRouter, or FastAPI APIRouter)
            prefix: URL prefix to add
        
        Examples:
            # Include a ViewSet router
            router.include(blog_router)
            
            # Include auth patterns
            from zeeb_api.auth.urls import auth_patterns
            router.include(auth_patterns, prefix="/auth")
        """
        if isinstance(router, (SimpleRouter, DefaultRouter)):
            # Include ViewSet-based router
            for route_prefix, viewset, basename in router._registry:
                combined_prefix = f"{prefix.strip('/')}/{route_prefix.strip('/')}" if prefix else route_prefix
                self._registry.append((combined_prefix, viewset, basename))
            # Propagate raw APIRouters from nested DefaultRouters
            if hasattr(router, '_api_routers'):
                for api_prefix, api_router in router._api_routers:
                    combined = f"{prefix.strip('/')}/{api_prefix.strip('/')}" if prefix and api_prefix else (prefix or api_prefix)
                    self._api_routers.append((combined, api_router))
        elif isinstance(router, APIRouter):
            # Include FastAPI APIRouter directly (like auth_patterns)
            self._api_routers.append((prefix, router))
        else:
            raise TypeError(f"Cannot include router of type {type(router)}")
        
        # Clear cached routes
        self._routes = []
    
    def get_urls(self) -> list[APIRouter]:
        """Generate URLs with optional root view."""
        urls = super().get_urls()
        
        # Add any raw APIRouters that were included
        for prefix, api_router in self._api_routers:
            if prefix:
                # Create a wrapper router with the prefix
                wrapper = APIRouter(prefix=f"/{prefix.strip('/')}")
                wrapper.include_router(api_router)
                urls.append(wrapper)
            else:
                urls.append(api_router)
        
        if self.include_root_view:
            root_router = self._get_root_router()
            urls.insert(0, root_router)
        
        return urls


def include(router: APIRouter, prefix: str = "") -> tuple[str, APIRouter]:
    """
    Helper function to include an APIRouter with a prefix.
    
    This is a convenience function for Django-style URL inclusion.
    
    Usage:
        from zeeb_api.routers import DefaultRouter, include
        from zeeb_api.auth.urls import auth_patterns
        
        router = DefaultRouter()
        router.include(*include(auth_patterns, "/auth"))
    
    Or directly on router.include():
        router.include(auth_patterns, prefix="/auth")
    """
    return (prefix, router)


def load_urlconf(urlconf_module: str) -> list[APIRouter]:
    """
    Load URL configuration from a module path.
    
    Like Django's ROOT_URLCONF, this loads the router from a module
    and returns its routes.
    
    Args:
        urlconf_module: Dotted path to the URL module (e.g., "myproject.urls")
    
    Returns:
        List of APIRouters
    
    The URL module should define either:
    - `router` - A DefaultRouter/SimpleRouter instance
    - `get_routes()` - A function that returns routes
    - `urlpatterns` - A list of APIRouters (Django-style)
    """
    import importlib
    
    module = importlib.import_module(urlconf_module)
    
    # Try different patterns
    if hasattr(module, "router"):
        router = module.router
        if hasattr(router, "routes"):
            return router.routes
        elif hasattr(router, "get_urls"):
            return router.get_urls()
    
    if hasattr(module, "get_routes"):
        return module.get_routes()
    
    if hasattr(module, "urlpatterns"):
        return module.urlpatterns
    
    raise ImportError(
        f"URL module '{urlconf_module}' must define 'router', 'get_routes()', or 'urlpatterns'"
    )
