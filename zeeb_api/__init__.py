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

from zeeb_api.serializers import (
    Serializer,
    ModelSerializer,
    SerializerMethodField,
    PrimaryKeyRelatedField,
    SlugRelatedField,
    NestedSerializer,
    create_list_response_schema,
)
from zeeb_api.serializers.fields import (
    Field,
    CharField,
    IntegerField,
    FloatField,
    DecimalField,
    BooleanField,
    DateTimeField,
    DateField,
    TimeField,
    EmailField,
    URLField,
    UUIDField,
    ListField,
    DictField,
)
from zeeb_api.viewsets import (
    ViewSet,
    ModelViewSet,
    ReadOnlyModelViewSet,
    action,
    QueryModelMixin,
)
from zeeb_api.routers import DefaultRouter, include, load_urlconf
from zeeb_api import permissions
from zeeb_api.query import (
    QueryRequest,
    QueryResponse,
    create_query_response_model,
    parse_q_filter,
    QFilterError,
)
from zeeb_api.response import Response
from zeeb_api.exceptions import (
    APIException,
    ValidationError,
    NotFound,
    PermissionDenied,
    AuthenticationFailed,
    MethodNotAllowed,
    Throttled,
    ImproperlyConfigured,
    InsecureSecretError,
    ErrorCode,
    ErrorDetail,
    ErrorMeta,
    ErrorBody,
    ErrorResponse,
    ZeebException,
    ValidationException,
    AuthenticationException,
    PermissionException,
    ResourceNotFoundException,
    ResourceConflictException,
    QueryException,
    RateLimitException,
    ServerException,
    MethodNotAllowedException,
    field_error,
    validation_error,
)
from zeeb_api.exception_handlers import (
    install_exception_handlers,
    get_error_responses,
)
from zeeb_api.auth import (
    JWTConfig,
    TokenPayload,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    get_jwt_config,
    configure_jwt,
    TokenResponse,
    RefreshRequest,
    UserInfo,
    JWTAuthMiddleware,
    AuthenticatedUser,
    get_current_user,
    get_current_user_optional,
    require_auth,
    create_auth_router,
    # Password hashing
    make_password,
    check_password,
    # Models
    AbstractBaseUser,
    AbstractUser,
    User,
    Permission,
    UserPermission,
    # Backends
    get_user_model,
    configure_auth,
    authenticate,
    create_user,
    create_superuser,
)
from zeeb_api.logging import (
    configure_logging,
    get_logger,
    get_request_logger,
    JsonFormatter,
    ConsoleFormatter,
)
from zeeb_api.conf import settings, get_settings, configure_settings
from zeeb_api.middleware import (
    install_middleware,
    CORSMiddleware,
)
from zeeb_api.app import create_app, get_asgi_application

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
]
