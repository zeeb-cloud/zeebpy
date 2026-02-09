"""
JWT Authentication module for Zeeb API.

Provides:
- JWT token creation and verification
- Bearer token middleware
- Auth endpoints (login, refresh, logout, register)
- User and Permission models
- Password hashing
- FastAPI dependencies for protected routes
"""

from zeeb_api.auth.jwt import (
    JWTConfig,
    TokenPayload,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    get_jwt_config,
    configure_jwt,
)
from zeeb_api.auth.schemas import (
    TokenResponse,
    RefreshRequest,
    UserInfo,
)
from zeeb_api.auth.middleware import (
    JWTAuthMiddleware,
    AuthenticatedUser,
    get_current_user,
    get_current_user_optional,
    require_auth,
)
from zeeb_api.auth.router import create_auth_router
from zeeb_api.auth.hashers import (
    make_password,
    check_password,
    is_password_usable,
)
from zeeb_api.auth.models import (
    AbstractBaseUser,
    AbstractUser,
    User,
    Permission,
    UserPermission,
)
from zeeb_api.auth.backends import (
    get_user_model,
    configure_auth,
    authenticate,
    create_user,
    create_superuser,
    clear_user_model_cache,
)

__all__ = [
    # JWT
    "JWTConfig",
    "TokenPayload",
    "create_access_token",
    "create_refresh_token",
    "create_token_pair",
    "decode_token",
    "get_jwt_config",
    "configure_jwt",
    # Schemas
    "TokenResponse",
    "RefreshRequest",
    "UserInfo",
    # Middleware
    "JWTAuthMiddleware",
    "AuthenticatedUser",
    "get_current_user",
    "get_current_user_optional",
    "require_auth",
    # Router
    "create_auth_router",
    # Password hashing
    "make_password",
    "check_password",
    "is_password_usable",
    # Models
    "AbstractBaseUser",
    "AbstractUser",
    "User",
    "Permission",
    "UserPermission",
    # Backends
    "get_user_model",
    "configure_auth",
    "authenticate",
    "create_user",
    "create_superuser",
    "clear_user_model_cache",
]
