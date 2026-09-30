"""Default router implementation."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import Any, Callable, Type
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from zeeb_api.viewsets.base import GenericViewSet, ViewSet

from zeeb_api.middleware.auth import bearer_scheme

logger = logging.getLogger(__name__)

try:  # FastAPI >= 0.137 includes routers lazily
    from fastapi.routing import iter_route_contexts as _iter_route_contexts
except ImportError:  # older versions copy an included router's routes
    _iter_route_contexts = None


def served_routes(routes: Iterable[Any]) -> list[Any]:
    """Every route *routes* serve, each answering with the path it is served on.

    Up to FastAPI 0.136, ``include_router()`` copied the included router's
    routes, prefixed, into the router that included it, so walking ``routes``
    saw every path. From 0.137 it appends one ``_IncludedRouter`` instead,
    which serves the sub-router's routes lazily under the include's prefix and
    has no ``path`` of its own: a walk saw only what was added directly, and an
    app built from included routers looked empty. ``iter_route_contexts`` is
    how FastAPI's own OpenAPI generator reads them back. Each context answers
    ``path``, ``methods``, ``name``, ``endpoint``, ``dependencies`` and the
    rest as the route is served (prefix, include-level dependencies and all),
    and ``original_route`` is the route object that was declared.

    Before 0.137 the copies are already there, and *routes* come back as they
    are. Use :func:`declared_route` for an ``isinstance`` check.
    """
    if _iter_route_contexts is None:
        return list(routes)
    return list(_iter_route_contexts(list(routes)))


def declared_route(route: Any) -> Any:
    """The route object behind an entry of :func:`served_routes`."""
    return getattr(route, "original_route", route)


def add_slash_alias_routes(router: APIRouter) -> None:
    """Register a hidden trailing-slash alias for every route on the router.

    Canonical paths have no trailing slash. Without an alias, a client that
    appends one gets FastAPI's 307 redirect, which browsers reject on
    CORS-preflighted requests ("Load failed" with a working backend). The
    alias serves both variants directly; ``include_in_schema=False`` keeps
    the OpenAPI schema canonical (slash-less only).

    Idempotent: paths that already exist on the router are skipped, so this
    can run both on a sub-router (e.g. the auth router) and again on the
    router that includes it.
    """
    from fastapi.routing import APIRoute

    # Served routes, not ``router.routes``: an included router's are behind one
    # entry without a path on FastAPI >= 0.137 (see served_routes).
    served = served_routes(router.routes)
    existing = {getattr(route, "path", None) for route in served}
    for route in served:
        if not isinstance(declared_route(route), APIRoute):
            continue
        path = route.path
        if not path or path == "/":
            continue
        alias = path[:-1] if path.endswith("/") else path + "/"
        if alias == "/" or alias in existing:
            continue
        existing.add(alias)
        # add_api_route() re-prepends the router prefix, so strip it here.
        sub_path = alias[len(router.prefix):] if router.prefix else alias
        router.add_api_route(
            sub_path,
            route.endpoint,
            methods=sorted(route.methods or []),
            name=f"{route.name}-slash" if route.name else None,
            response_model=route.response_model,
            status_code=route.status_code,
            dependencies=list(route.dependencies or []),
            include_in_schema=False,
        )


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
    
    Routes generated (canonical paths have no trailing slash; a hidden
    trailing-slash alias is registered for each so both variants work):
    - {prefix}/query (query with Q filters) - POST
    - {prefix} (list) - GET
    - {prefix} (create) - POST
    - {prefix}/{lookup} (retrieve, update, partial_update, destroy)
    - {prefix}/{lookup}/{action} (custom detail actions)
    - {prefix}/{action} (custom list actions)
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
        # List route (GET collection; filters/pagination via ListModelMixin)
        Route(
            url="",
            mapping={
                "get": "list",
            },
            name="{basename}-list",
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

        Raises:
            ValueError: When the prefix is already registered — a second
                registration at the same prefix would be silently shadowed
                by FastAPI's first-match routing.
        """
        if basename is None:
            basename = prefix.strip("/").replace("/", "-")

        self._check_prefix_free(prefix, viewset)
        self._registry.append((prefix, viewset, basename))
        # A registration after ``.routes`` was read must show up in it.
        self._routes = []

    def _check_prefix_free(self, prefix: str, viewset: Type[ViewSet]) -> None:
        """Raise ``ValueError`` when *prefix* is already in the registry."""
        normalized = prefix.strip("/")
        for existing_prefix, existing_viewset, _ in self._registry:
            if existing_prefix.strip("/") == normalized:
                raise ValueError(
                    f"Route prefix '{normalized}' is already registered to "
                    f"{existing_viewset.__name__}; cannot also register "
                    f"{viewset.__name__} there — the later registration would be "
                    f"silently shadowed. Register it under a different prefix."
                )
    
    def _get_lookup_regex(self, viewset: Type[ViewSet]) -> str:
        """The path parameter for detail routes: ``{lookup_url_kwarg or lookup_field}``.

        ``get_object()`` reads the value back from ``self.kwargs`` under
        ``lookup_url_kwarg`` when it is set, so the route must use that name.
        """
        lookup = getattr(viewset, "lookup_url_kwarg", None) or getattr(
            viewset, "lookup_field", "id"
        )
        return "{" + lookup + "}"
    
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
            add_slash_alias_routes(router)
            routers.append(router)

        return routers
    
    def _get_router_for_viewset(
        self,
        prefix: str,
        viewset: Type[ViewSet],
        basename: str,
    ) -> APIRouter:
        """Generate a FastAPI router for a ViewSet."""
        from zeeb_api.query import create_query_response_model, query_request_model
        
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
                action_partial_request_schema = None

                # Try to get action-specific serializer
                serializer_class = self._discover_serializer_class(viewset, action_name)
                if serializer_class:
                    if getattr(serializer_class, "ResponseSchema", None) is not None:
                        action_response_schema = serializer_class.ResponseSchema
                    if getattr(serializer_class, "RequestSchema", None) is not None:
                        action_request_schema = serializer_class.RequestSchema
                    action_partial_request_schema = getattr(
                        serializer_class, "PartialRequestSchema", None
                    )
                
                # Determine response model and request schema based on action
                action_response_model = None
                final_request_schema = None
                action_permission_classes = None
                # POST create returns 201 Created; everything else defaults to 200.
                action_status_code = 200
                # PATCH must apply only the keys the client actually sent, so its
                # body is dumped with exclude_unset. Create does the same so that
                # fields the client omitted are not sent as explicit None over the
                # model's own defaults. PUT keeps the full dump (full replace).
                endpoint_exclude_unset = False
                # Documentation-only response models (no runtime validation).
                doc_responses: dict[int | str, dict[str, Any]] = {}

                if action_name == "query":
                    # Query uses QueryRequest (bounded by DEFAULT_LIMIT /
                    # MAX_LIMIT) and QueryResponse
                    action_response_model = query_response_schema
                    final_request_schema = query_request_model()
                elif action_name in ("retrieve",) and action_response_schema:
                    action_response_model = action_response_schema
                elif action_name == "list":
                    # Documented, not enforced: the shape depends on the
                    # paginator, which get_pagination_class() may choose per
                    # request, and a list() override may return its own shape.
                    list_model = self._list_response_model(viewset, action_response_schema)
                    if list_model is not None:
                        doc_responses[200] = {
                            "model": list_model,
                            "description": "Successful Response",
                        }
                elif action_name == "destroy":
                    action_status_code = 204
                elif action_name == "create":
                    action_response_model = action_response_schema
                    final_request_schema = action_request_schema
                    action_status_code = 201
                    endpoint_exclude_unset = final_request_schema is not None
                elif action_name in ("update", "partial_update"):
                    # Type the write body so it appears in OpenAPI (needed for
                    # client codegen). PUT uses the full request schema; PATCH
                    # uses the all-optional variant and is dumped with
                    # exclude_unset so partial semantics are preserved.
                    action_response_model = action_response_schema
                    if action_name == "update":
                        final_request_schema = action_request_schema
                    else:
                        final_request_schema = (
                            action_partial_request_schema or action_request_schema
                        )
                        endpoint_exclude_unset = final_request_schema is not None
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
                    exclude_unset=endpoint_exclude_unset,
                    status_code=action_status_code,
                )

                secured, throttled = self._access_controls(
                    viewset, action_name, action_permission_classes
                )
                responses = self._error_responses(
                    detail=route.detail,
                    has_body=final_request_schema is not None or action_name == "query",
                    secured=secured,
                    throttled=throttled,
                )
                responses.update(doc_responses)

                # Register with FastAPI router
                route_name = route.name.format(basename=basename)
                
                router.add_api_route(
                    url,
                    endpoint,
                    methods=[method.upper()],
                    name=route_name,
                    response_model=action_response_model,
                    status_code=action_status_code,
                    responses=responses,
                    # Declares the HTTP bearer scheme in OpenAPI; auto_error is
                    # off, so it never rejects a request by itself.
                    dependencies=[Depends(bearer_scheme)] if secured else None,
                )

        return router
    
    @staticmethod
    def _list_response_model(viewset: Type[ViewSet], item_schema: Any) -> Any:
        """The documented body of GET on the collection: an envelope or a list."""
        if item_schema is None:
            return None
        paginated = False
        get_pagination_class = getattr(viewset, "get_pagination_class", None)
        if get_pagination_class is not None:
            try:
                paginated = viewset().get_pagination_class() is not None
            except Exception:
                paginated = getattr(viewset, "pagination_class", None) is not None
        if paginated:
            from zeeb_api.serializers.pydantic import create_list_response_schema

            return create_list_response_schema(item_schema)
        return list[item_schema]

    @staticmethod
    def _access_controls(
        viewset: Type[ViewSet], action_name: str, action_permission_classes: list | None
    ) -> tuple[bool, bool]:
        """Whether an action checks permissions (needs a token) and is throttled.

        Read off the effective classes at route build: the action's own
        permission classes, the viewset's, or ``DEFAULT_PERMISSION_CLASSES``;
        only ``AllowAny`` counts as open. Anything that cannot be resolved is
        treated as secured - the conservative answer for documentation.
        """
        from zeeb_api.permissions import AllowAny

        instance = viewset()
        instance.action = action_name
        if action_permission_classes is not None:
            instance._action_permission_classes = action_permission_classes
        try:
            permissions = instance.get_permissions()
        except Exception:
            permissions = [None]
        secured = any(not isinstance(p, AllowAny) for p in permissions)
        try:
            throttled = bool(instance.get_throttles())
        except Exception:
            throttled = True
        return secured, throttled

    @staticmethod
    def _error_responses(
        *, detail: bool, has_body: bool, secured: bool, throttled: bool
    ) -> dict[int | str, dict[str, Any]]:
        """The error envelopes an action can answer with, for OpenAPI."""
        from zeeb_api.exceptions import ErrorResponse

        responses: dict[int | str, dict[str, Any]] = {}
        if has_body:
            responses[400] = {"model": ErrorResponse, "description": "Validation error"}
        if secured:
            responses[401] = {"model": ErrorResponse, "description": "Not authenticated"}
            responses[403] = {"model": ErrorResponse, "description": "Permission denied"}
        if detail:
            responses[404] = {"model": ErrorResponse, "description": "Not found"}
        if throttled:
            responses[429] = {"model": ErrorResponse, "description": "Rate limit exceeded"}
        return responses

    @staticmethod
    def _discover_serializer_class(viewset: Type[ViewSet], action_name: str) -> Any:
        """The serializer class ``viewset`` uses for ``action_name``, for OpenAPI.

        Resolved on a request-less instance at route-build time. A viewset
        without a serializer is fine (None). The built-in
        ``get_serializer_class`` cannot fail otherwise, so any other error is
        raised - it would fail every request too. An *overridden*
        ``get_serializer_class`` may legitimately need the request (e.g. a
        per-user serializer); its error is logged as a warning naming the
        viewset and action - never silently dropped - and the class-level
        schemas are used for the docs.
        """
        get_serializer_class = getattr(viewset, "get_serializer_class", None)
        if get_serializer_class is None:
            return None
        temp_viewset = viewset()
        temp_viewset.action = action_name
        overridden = get_serializer_class is not getattr(
            GenericViewSet, "get_serializer_class", None
        )
        try:
            return temp_viewset.get_serializer_class()
        except ValueError:
            if getattr(viewset, "serializer_class", None) is None and not overridden:
                return None  # no serializer configured: nothing to document
            if not overridden:
                raise
        except Exception:
            if not overridden:
                raise
        logger.warning(
            "%s.get_serializer_class() failed for action %r while building "
            "routes (no request exists yet); the OpenAPI schema for that "
            "action falls back to the class-level serializer.",
            viewset.__name__,
            action_name,
            exc_info=True,
        )
        return None

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
        """The Python type of the viewset's ``lookup_field`` on its model.

        The path parameter is typed with it, so FastAPI validates and converts
        the URL segment. It used to be the primary key's type whatever
        ``lookup_field`` said, which made a slug lookup on a UUID-keyed model
        answer 422 for every slug. A foreign key resolves to the related
        primary key's type; a field the model does not have (or no model) is
        ``str`` — the most permissive — except that ``id``/``pk`` without a
        model keeps the historical UUID default.
        """
        from zeeb_orm.models.fields import ForeignKeyField

        model = getattr(viewset, "model", None)
        if model is None:
            queryset = getattr(viewset, "queryset", None)
            model = getattr(queryset, "model", None) if queryset is not None else None
        lookup_field = getattr(viewset, "lookup_field", "id") or "id"

        meta = getattr(model, "_meta", None)
        if meta is None:
            return uuid.UUID if lookup_field in ("id", "pk") else str

        field = meta.pk if lookup_field == "pk" else meta.get_field(lookup_field)
        if field is None:
            field = meta.get_field_by_column(lookup_field)
        if field is None:
            return str
        if isinstance(field, ForeignKeyField):
            try:
                field = field.get_target_model()._meta.pk
            except Exception:
                return str
        python_type = getattr(field, "_python_type", None)
        return python_type if python_type is not None else str
    
    def _create_endpoint(
        self,
        viewset_class: Type[ViewSet],
        action_name: str,
        detail: bool,
        lookup: str,
        request_schema: type | None = None,
        lookup_type: type = uuid.UUID,
        permission_classes: list | None = None,
        exclude_unset: bool = False,
        status_code: int = 200,
    ) -> Callable:
        """Create a FastAPI endpoint function for a ViewSet action."""
        from fastapi.encoders import jsonable_encoder
        from pydantic import BaseModel

        lookup_field = lookup.strip("{}")

        def _finish(result: Any) -> Any:
            # None is "no content". A route documented as 204 (destroy) that
            # still returns a body - an override - gets it sent with 200
            # rather than FastAPI blanking the body to fit the 204.
            if result is None:
                return Response(status_code=204)
            if status_code == 204 and not isinstance(result, Response):
                return JSONResponse(jsonable_encoder(result))
            return result

        def _dump_body(body: BaseModel) -> dict[str, Any]:
            # PATCH dumps with exclude_unset so only client-provided keys are
            # applied; everything else uses the full dump.
            return body.model_dump(exclude_unset=True) if exclude_unset else body.model_dump()

        def _adapt_action_kwargs(func: Callable, path_params: dict[str, Any]) -> dict[str, Any]:
            """Match path params to the action's signature.

            Custom actions conventionally accept ``pk`` (see the ``@action``
            docstring) while routes are generated with the viewset's
            ``lookup_field`` (default ``id``). Map between the two and drop
            params the action doesn't accept — handlers can always read
            ``self.kwargs`` instead.
            """
            import inspect

            try:
                sig = inspect.signature(func)
            except (TypeError, ValueError):
                return dict(path_params)
            params = sig.parameters
            if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
                return dict(path_params)
            adapted: dict[str, Any] = {}
            for key, value in path_params.items():
                if key in params:
                    adapted[key] = value
                elif key == "id" and "pk" in params:
                    adapted["pk"] = value
                elif key == "pk" and "id" in params:
                    adapted["id"] = value
            return adapted

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
                    
                    await viewset.perform_authentication(request)
                    await viewset.check_permissions(request)
                    viewset.version = getattr(request.state, "version", None)
                    await viewset.check_throttles(request)

                    # Store body data for action
                    viewset._request_body = _dump_body(body)

                    action = getattr(viewset, action_name)
                    result = await action(request, **_adapt_action_kwargs(action, path_params))
                    
                    return _finish(result)
                
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
                    
                    await viewset.perform_authentication(request)
                    await viewset.check_permissions(request)
                    viewset.version = getattr(request.state, "version", None)
                    await viewset.check_throttles(request)

                    action = getattr(viewset, action_name)
                    result = await action(request, **_adapt_action_kwargs(action, path_params))

                    return _finish(result)

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
                    
                    await viewset.perform_authentication(request)
                    await viewset.check_permissions(request)
                    viewset.version = getattr(request.state, "version", None)
                    await viewset.check_throttles(request)

                    # Store body data for serializer/query
                    viewset._request_body = _dump_body(body)

                    action = getattr(viewset, action_name)
                    result = await action(request)
                    
                    return _finish(result)
                
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
                    
                    await viewset.perform_authentication(request)
                    await viewset.check_permissions(request)
                    viewset.version = getattr(request.state, "version", None)
                    await viewset.check_throttles(request)

                    action = getattr(viewset, action_name)
                    result = await action(request)
                    
                    return _finish(result)
                
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
                basename: f"{base_url}/{prefix.strip('/')}"
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
                self._check_prefix_free(combined_prefix, viewset)
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
                add_slash_alias_routes(wrapper)
                urls.append(wrapper)
            else:
                add_slash_alias_routes(api_router)
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
