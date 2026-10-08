"""Base ViewSet classes."""

from __future__ import annotations

from typing import Any, ClassVar, Sequence, TYPE_CHECKING
from fastapi import Request, HTTPException

if TYPE_CHECKING:
    from zeeb_api.serializers import Serializer
    from zeeb_api.permissions.base import BasePermission
    from zeeb_api.authentication.base import BaseAuthentication
    from zeeb_api.pagination.base import BasePagination
    from zeeb_api.filters.base import BaseFilter
    from zeeb_api.throttling.base import BaseThrottle


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _declared(instance: Any, name: str, default_owner: type) -> tuple[bool, Any]:
    """Whether *name* was set on *instance* or a class below *default_owner*.

    ``default_owner`` is the class holding the framework default. Anything a
    subclass (or the instance) declares - ``None`` and ``[]`` included - is an
    explicit choice; only an untouched default falls through to the
    ``DEFAULT_*`` settings.
    """
    if name in getattr(instance, "__dict__", {}):
        return True, instance.__dict__[name]
    for klass in type(instance).__mro__:
        if klass is default_owner:
            break
        if name in klass.__dict__:
            return True, klass.__dict__[name]
    return False, None


_setting_classes_cache: dict[tuple[str, tuple[Any, ...]], list[type]] = {}


def _classes_from_setting(name: str) -> list[type]:
    """``settings.<name>`` (dotted paths or classes) resolved to classes, cached."""
    from zeeb_api.conf import settings

    value = getattr(settings, name, None) or []
    if not isinstance(value, (list, tuple)):
        value = [value]
    key = (name, tuple(value))
    cached = _setting_classes_cache.get(key)
    if cached is None:
        from zeeb_api.middleware.loader import import_string

        cached = [import_string(v) if isinstance(v, str) else v for v in value]
        _setting_classes_cache[key] = cached
    return list(cached)


def _permission_type_for_method(method: str) -> str:
    """Object-permission type implied by an HTTP method (read/change/delete)."""
    method = method.upper()
    if method in _SAFE_METHODS:
        return "read"
    if method == "DELETE":
        return "delete"
    return "change"


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
    # None means "unset": the user set by the global auth middleware (if any)
    # stands. A non-empty list takes ownership of authentication for this
    # viewset (first authenticator to return a user wins).
    authentication_classes: ClassVar[list[type[BaseAuthentication]] | None] = None
    # None means "use settings.DEFAULT_THROTTLE_CLASSES"; [] disables throttling.
    throttle_classes: ClassVar[list[type[BaseThrottle]] | None] = None

    def __init__(self, request: Request | None = None, **kwargs: Any) -> None:
        self.request = request
        self.kwargs = kwargs
        self.action: str | None = None
        self.version: str | None = None
        self._action_permission_classes: list[type[BasePermission]] | None = None

    def get_permissions(self) -> list[BasePermission]:
        """
        Get permission instances for this view.
        
        If the current action has specific permissions set via @action decorator,
        those take precedence over the class-level permission_classes. A
        viewset that declares no ``permission_classes`` at all uses
        ``settings.DEFAULT_PERMISSION_CLASSES`` (default: none - every request
        is allowed, as before the setting existed).
        """
        # Use action-specific permissions if set
        if self._action_permission_classes is not None:
            return [permission() for permission in self._action_permission_classes]
        return [permission() for permission in self.get_permission_classes()]

    def get_permission_classes(self) -> list[type[BasePermission]]:
        """The permission classes in force when no ``@action`` overrides them."""
        explicit, value = _declared(self, "permission_classes", ViewSet)
        if explicit:
            return list(value or [])
        return _classes_from_setting("DEFAULT_PERMISSION_CLASSES")

    def get_authenticators(self) -> list[BaseAuthentication]:
        """Instances of authentication_classes ([] when unset or empty)."""
        return [auth() for auth in (self.authentication_classes or [])]

    async def perform_authentication(self, request: Request) -> None:
        """
        Run view-level authenticators (first success wins) before permissions.

        Unset/empty authentication_classes → no-op: the global auth
        middleware's ``request.state.user`` stands. A non-empty list takes
        ownership: the middleware's user/auth_error are discarded and only the
        listed authenticators may authenticate this request. If none succeeds
        the request proceeds unauthenticated and the permission layer decides.
        """
        authenticators = self.get_authenticators()
        if not authenticators:
            return
        request.state.user = None
        request.state.auth_error = None
        for authenticator in authenticators:
            user = await authenticator.authenticate(request)  # may raise 401
            if user is not None:
                request.state.user = user
                return


    def _deny(self, request: Request, message: str | None) -> None:
        """Raise 401 when the caller is unauthenticated, else 403.

        A failed permission check means one of two very different things to a
        client: "log in / refresh your token" (401) or "you may not do this"
        (403). Distinguishing them lets a frontend branch correctly (the
        documented flow refreshes on 401, shows a no-access state on 403).
        We follow DRF's heuristic: no authenticated user on the request →
        AuthenticationException (401); otherwise PermissionDenied (403).
        """
        from zeeb_api.exceptions import (
            AuthenticationException,
            ErrorCode,
            PermissionDenied,
        )

        user = getattr(request.state, "user", None)
        authenticated = user is not None and getattr(user, "is_authenticated", True)
        if not authenticated:
            # If JWTAuthMiddleware recorded *why* the token failed, preserve it:
            # an expired token should tell the client to refresh, not re-login.
            auth_error = getattr(request.state, "auth_error", None)
            if auth_error == ErrorCode.AUTH_TOKEN_EXPIRED:
                raise AuthenticationException(
                    code=ErrorCode.AUTH_TOKEN_EXPIRED, message="Token has expired"
                )
            if auth_error == ErrorCode.AUTH_TOKEN_INVALID:
                raise AuthenticationException(
                    code=ErrorCode.AUTH_TOKEN_INVALID,
                    message="Invalid authentication token",
                )
            raise AuthenticationException(
                message=message or "Authentication credentials were not provided"
            )
        raise PermissionDenied(message or "Permission denied")

    async def check_permissions(self, request: Request) -> None:
        """Check if the request should be permitted."""
        for permission in self.get_permissions():
            if not await permission.has_permission(request, self):
                self._deny(request, getattr(permission, "message", None))

    async def check_object_permissions(self, request: Request, obj: Any) -> None:
        """Check if the request should be permitted for a specific object."""
        for permission in self.get_permissions():
            if not await permission.has_object_permission(request, self, obj):
                self._deny(request, getattr(permission, "message", None))

    def get_throttles(self) -> list[BaseThrottle]:
        """
        Get throttle instances for this view.

        Uses the class-level throttle_classes if set, otherwise falls back
        to settings.DEFAULT_THROTTLE_CLASSES (dotted paths, resolved and
        cached).
        """
        throttle_classes = self.throttle_classes
        if throttle_classes is None:
            from zeeb_api.throttling.base import get_default_throttle_classes
            throttle_classes = get_default_throttle_classes()
        return [throttle() for throttle in throttle_classes]

    async def check_throttles(self, request: Request) -> None:
        """
        Check if the request should be throttled.

        Raises RateLimitException (429, Retry-After) if any throttle denies
        the request.
        """
        throttle_durations: list[float] = []
        throttled = False

        for throttle in self.get_throttles():
            if not await throttle.allow_request(request, self):
                throttled = True
                wait = throttle.wait()
                if wait is not None:
                    throttle_durations.append(wait)

        if throttled:
            import math

            from zeeb_api.exceptions import RateLimitException

            retry_after = math.ceil(max(throttle_durations)) if throttle_durations else None
            raise RateLimitException(retry_after=retry_after)

    def get_action_request_body(self) -> dict[str, Any] | None:
        """
        Get the validated request body for the current action.
        
        Returns None if no request body was provided or validated.
        """
        return getattr(self, "_request_body", None)

    def get_action_request_model(self) -> Any:
        """
        Get the validated request body for the current action, as its model.

        The instance of the route's ``request_schema`` (``@action`` or
        ``@extend_schema``) the router validated. Unlike
        :meth:`get_action_request_body`, which is its ``model_dump()``, it keeps
        field aliases, which fields the client actually sent
        (``model_fields_set``) and the model's own methods. Returns None when
        the route declares no request schema.
        """
        return getattr(self, "_request_model", None)


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
    
    # Map actions to permission types for queryset filtering. Actions not
    # listed here (custom @action methods, anything a subclass routes itself)
    # are resolved by _get_permission_type_for_action: an explicit
    # ``@action(permission_type=...)`` wins, otherwise the HTTP method decides.
    _action_permission_map: ClassVar[dict[str, str]] = {
        "list": "read",
        "retrieve": "read",
        "query": "read",
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
        """The ``use_object_permissions`` rule type the current action is scoped by.

        The built-in actions map through ``_action_permission_map``. Anything
        else — a custom ``@action``, or an action name a subclass routes itself
        — must not come back ``None``, because ``None`` means "no scoping" and
        would hand the action the unfiltered queryset. So:

        1. an ``@action(permission_type=...)`` declaration wins;
        2. otherwise the HTTP method decides: GET/HEAD/OPTIONS read, DELETE
           deletes, anything else (POST/PUT/PATCH) changes;
        3. without a request, the action's declared methods decide the same way.

        Returns None only when no action is set (the viewset is not serving a
        request).
        """
        if self.action is None:
            return None
        mapped = self._action_permission_map.get(self.action)
        if mapped is not None:
            return mapped

        config = getattr(getattr(type(self), self.action, None), "_action_config", None) or {}
        declared = config.get("permission_type")
        if declared:
            return declared

        method = getattr(self.request, "method", None) if self.request is not None else None
        if method:
            return _permission_type_for_method(method)
        methods = [m.upper() for m in config.get("methods", [])]
        if methods and all(m in _SAFE_METHODS for m in methods):
            return "read"
        if methods and all(m == "DELETE" for m in methods):
            return "delete"
        return "change"
    
    def _get_user_from_request(self) -> Any:
        """Extract the authenticated user, or None for anonymous.

        Reads ``request.state.user`` — where ``JWTAuthMiddleware`` and
        view-level authenticators put the resolved user (and what the
        permission classes read). Falls back to Starlette's ``request.user``,
        guarding the ``AssertionError`` it raises when no ``AuthenticationMiddleware``
        is installed (``getattr`` would otherwise not swallow it).
        """
        if self.request is None:
            return None
        state = getattr(self.request, "state", None)
        user = getattr(state, "user", None) if state is not None else None
        if user is not None:
            return user
        try:
            return getattr(self.request, "user", None)
        except AssertionError:
            return None
    
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

        # Normalize to a fresh QuerySet per request: a Manager (Model.objects)
        # is not awaitable, and a shared class-level QuerySet would serve its
        # cached results across requests.
        if hasattr(queryset, "all"):
            queryset = queryset.all()

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
            from zeeb_api.exceptions import NotFound
            raise NotFound("Object not found")
        
        # Build filter
        filter_kwargs = {self.lookup_field: lookup_value}

        # Get object. A malformed lookup value is a 400, a genuinely absent row
        # is a 404, and a real database error must surface as a 500 rather than
        # being silently masked as "not found".
        from zeeb_api.exceptions import NotFound, ValidationError

        try:
            obj = await queryset.filter(**filter_kwargs).first()
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                {self.lookup_field: [f"Invalid lookup value: {exc}"]}
            ) from exc

        if obj is None:
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
        # Check class-level permissions. Route the denial through _deny so an
        # unauthenticated caller gets 401 (and an expired-token hint) rather than
        # a flat 403 — consistent with the collection-level check_permissions.
        for permission in self.get_permissions():
            if not await permission.has_object_permission(request, self, obj):
                self._deny(request, getattr(permission, "message", None))

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
            from zeeb_api.exceptions import PermissionDenied
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
            from zeeb_api.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to create this object")
    
    def get_pagination_class(self) -> type[BasePagination] | None:
        """The paginator for list responses.

        An explicit ``pagination_class`` wins - ``None`` included, which turns
        pagination off for this viewset. Otherwise
        ``settings.DEFAULT_PAGINATION_CLASS`` applies (default ``None``: the
        whole queryset is returned, as before the setting existed).
        """
        explicit, value = _declared(self, "pagination_class", GenericViewSet)
        if explicit:
            return value
        classes = _classes_from_setting("DEFAULT_PAGINATION_CLASS")
        return classes[0] if classes else None
    
    async def paginate_queryset(self, queryset: Any) -> tuple[list[Any], dict[str, Any]]:
        """
        Paginate the queryset if pagination is configured.
        
        Returns (paginated_items, pagination_info)
        """
        pagination_class = self.get_pagination_class()
        if pagination_class is None:
            # zeeb_orm QuerySet has no .all() (that's a Manager method); awaiting the
            # queryset materializes it via __await__ -> _fetch_all(). Calling .all()
            # here raised AttributeError and 500'd every unpaginated GET collection.
            items = await queryset
            return items, {}
        
        paginator = pagination_class()
        return await paginator.paginate_queryset(queryset, self.request)
    
    def get_filter_backends(self) -> list[type[BaseFilter]]:
        """``filter_backends``, or ``settings.DEFAULT_FILTER_BACKENDS`` when unset."""
        explicit, value = _declared(self, "filter_backends", GenericViewSet)
        if explicit:
            return list(value or [])
        return _classes_from_setting("DEFAULT_FILTER_BACKENDS")

    def filter_queryset(self, queryset: Any) -> Any:
        """Apply filters to queryset."""
        for backend_class in self.get_filter_backends():
            backend = backend_class()
            queryset = backend.filter_queryset(self.request, queryset, self)
        return queryset
