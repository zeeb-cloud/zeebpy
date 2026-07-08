"""
Zeeb API - Django REST Framework-like API toolkit for FastAPI.

Provides:
- Serializers (ModelSerializer, validation, nested serializers)
- ViewSets (ModelViewSet, CRUD operations, custom actions)
- Routers (auto-generate FastAPI routes)
- Query (Q filter parsing, unified request/response)
- Permissions (IsAuthenticated, IsAdminUser, custom)
- Settings (Django-style settings management)
- Middleware (configurable via settings)
"""

from zeeb_api import permissions
from zeeb_api.app import create_app, get_asgi_application
from zeeb_api.auth import (
    # Models
    AbstractBaseUser,
    AbstractUser,
    AuthenticatedUser,
    JWTAuthMiddleware,
    JWTConfig,
    Permission,
    RefreshRequest,
    TokenPayload,
    TokenResponse,
    User,
    UserInfo,
    UserPermission,
    authenticate,
    check_password,
    configure_auth,
    configure_jwt,
    create_access_token,
    create_auth_router,
    create_refresh_token,
    create_superuser,
    create_token_pair,
    create_user,
    decode_token,
    get_current_user,
    get_current_user_optional,
    get_jwt_config,
    # Backends
    get_user_model,
    # Password hashing
    make_password,
    require_auth,
)
from zeeb_api.conf import configure_settings, get_settings, settings
from zeeb_api.exception_handlers import (
    get_error_responses,
    install_error_response_schema,
    install_exception_handlers,
)
from zeeb_api.exceptions import (
    APIException,
    AuthenticationException,
    AuthenticationFailed,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    ErrorMeta,
    ErrorResponse,
    ImproperlyConfigured,
    InsecureSecretError,
    MethodNotAllowed,
    MethodNotAllowedException,
    NotFound,
    PermissionDenied,
    PermissionException,
    QueryException,
    RateLimitException,
    ResourceConflictException,
    ResourceNotFoundException,
    ServerException,
    Throttled,
    ValidationError,
    ValidationException,
    ZeebException,
    field_error,
    validation_error,
)
from zeeb_api.logging import (
    ConsoleFormatter,
    JsonFormatter,
    configure_logging,
    get_logger,
    get_request_logger,
)
from zeeb_api.middleware import (
    CORSMiddleware,
    install_middleware,
)
from zeeb_api.query import (
    QFilterError,
    QueryRequest,
    QueryResponse,
    create_query_response_model,
    parse_q_filter,
)
from zeeb_api.response import Response
from zeeb_api.routers import DefaultRouter, include, load_urlconf
from zeeb_api.serializers import (
    ModelSerializer,
    NestedSerializer,
    PrimaryKeyRelatedField,
    Serializer,
    SerializerMethodField,
    SlugRelatedField,
    create_list_response_schema,
)
from zeeb_api.serializers.fields import (
    BooleanField,
    CharField,
    DateField,
    DateTimeField,
    DecimalField,
    DictField,
    EmailField,
    Field,
    FloatField,
    IntegerField,
    ListField,
    TimeField,
    URLField,
    UUIDField,
)
from zeeb_api.throttling import (
    AnonRateThrottle,
    BaseThrottle,
    ScopedRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
    set_throttle_cache,
    throttle,
)
from zeeb_api.versioning import (
    AcceptHeaderVersioning,
    BaseVersioning,
    HeaderVersioning,
    QueryParameterVersioning,
    URLPathVersioning,
    VersioningMiddleware,
    get_api_version,
)
from zeeb_api.viewsets import (
    ModelViewSet,
    QueryModelMixin,
    ReadOnlyModelViewSet,
    ViewSet,
    action,
)

# OAuth2/OIDC names are exported lazily (PEP 562): plain ``import zeeb_api``
# must not require httpx and must not register the auth_external_identities
# table in zeeb_orm metadata/migrations.
_OAUTH_EXPORTS = frozenset({
    "OAuth2Client",
    "OAuthTokenSet",
    "OAuthProvider",
    "ExternalClaims",
    "AzureADProvider",
    "GoogleProvider",
    "GitHubProvider",
    "ExternalIdentity",
    "create_oauth_router",
    "build_providers_from_settings",
    "get_oauth_provider",
    "ExternalTokenValidator",
})


def __getattr__(name: str):
    if name in _OAUTH_EXPORTS:
        from zeeb_api.auth import oauth
        value = getattr(oauth, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _OAUTH_EXPORTS)


__version__ = "0.1.0"

__all__ = [
    # App factory
    "create_app",
    "get_asgi_application",
    # Settings
    "settings",
    "get_settings",
    "configure_settings",
    # Middleware
    "install_middleware",
    "CORSMiddleware",
    # Throttling
    "BaseThrottle",
    "SimpleRateThrottle",
    "AnonRateThrottle",
    "UserRateThrottle",
    "ScopedRateThrottle",
    "set_throttle_cache",
    "throttle",
    # Versioning
    "BaseVersioning",
    "QueryParameterVersioning",
    "HeaderVersioning",
    "AcceptHeaderVersioning",
    "URLPathVersioning",
    "VersioningMiddleware",
    "get_api_version",
    # Serializers
    "Serializer",
    "ModelSerializer",
    "SerializerMethodField",
    "PrimaryKeyRelatedField",
    "SlugRelatedField",
    "NestedSerializer",
    "create_list_response_schema",
    # Fields
    "Field",
    "CharField",
    "IntegerField",
    "FloatField",
    "DecimalField",
    "BooleanField",
    "DateTimeField",
    "DateField",
    "TimeField",
    "EmailField",
    "URLField",
    "UUIDField",
    "ListField",
    "DictField",
    # ViewSets
    "ViewSet",
    "ModelViewSet",
    "ReadOnlyModelViewSet",
    "action",
    "QueryModelMixin",
    # Routers
    "DefaultRouter",
    "include",
    "load_urlconf",
    # Permissions
    "permissions",
    # Query
    "QueryRequest",
    "QueryResponse",
    "create_query_response_model",
    "parse_q_filter",
    "QFilterError",
    # Response
    "Response",
    # DRF-compatible exceptions (canonical home: zeeb_api.exceptions)
    "APIException",
    "ValidationError",
    "NotFound",
    "PermissionDenied",
    "AuthenticationFailed",
    "MethodNotAllowed",
    "Throttled",
    # Configuration errors
    "ImproperlyConfigured",
    "InsecureSecretError",
    # Exceptions (new standardized)
    "ErrorCode",
    "ErrorDetail",
    "ErrorMeta",
    "ErrorBody",
    "ErrorResponse",
    "ZeebException",
    "ValidationException",
    "AuthenticationException",
    "PermissionException",
    "ResourceNotFoundException",
    "ResourceConflictException",
    "QueryException",
    "RateLimitException",
    "ServerException",
    "MethodNotAllowedException",
    "field_error",
    "validation_error",
    # Exception handlers
    "install_exception_handlers",
    "install_error_response_schema",
    "get_error_responses",
    # Authentication
    "JWTConfig",
    "TokenPayload",
    "create_access_token",
    "create_refresh_token",
    "create_token_pair",
    "decode_token",
    "get_jwt_config",
    "configure_jwt",
    "TokenResponse",
    "RefreshRequest",
    "UserInfo",
    "JWTAuthMiddleware",
    "AuthenticatedUser",
    "get_current_user",
    "get_current_user_optional",
    "require_auth",
    "create_auth_router",
    # Password hashing
    "make_password",
    "check_password",
    # User models
    "AbstractBaseUser",
    "AbstractUser",
    "User",
    "Permission",
    "UserPermission",
    # Auth backends
    "get_user_model",
    "configure_auth",
    "authenticate",
    "create_user",
    "create_superuser",
    # Logging
    "configure_logging",
    "get_logger",
    "get_request_logger",
    "JsonFormatter",
    "ConsoleFormatter",
    # OAuth2/OIDC (lazy - see __getattr__)
    *sorted(_OAUTH_EXPORTS),
]
