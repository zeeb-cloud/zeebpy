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

from zeeb_api.auth.backends import (
    authenticate,
    clear_user_model_cache,
    configure_auth,
    create_superuser,
    create_user,
    get_user_model,
)
from zeeb_api.auth.hashers import (
    check_password,
    is_password_usable,
    make_password,
)
from zeeb_api.auth.jwt import (
    JWTConfig,
    TokenPayload,
    configure_jwt,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    get_jwt_config,
)
from zeeb_api.auth.middleware import (
    AuthenticatedUser,
    JWTAuthMiddleware,
    get_current_user,
    get_current_user_optional,
    require_auth,
)
from zeeb_api.auth.models import (
    AbstractBaseUser,
    AbstractUser,
    Permission,
    User,
    UserPermission,
)
from zeeb_api.auth.router import create_auth_router
from zeeb_api.auth.schemas import (
    RefreshRequest,
    TokenResponse,
    UserInfo,
)

# OAuth2/OIDC names (zeeb_api.auth.oauth) are exported lazily via PEP 562
# __getattr__: importing them requires the "oauth" extra (httpx), and
# ExternalIdentity registers a database table on import. Plain
# ``import zeeb_api.auth`` must do neither.
_OAUTH_EXPORTS = frozenset({
    "OAuth2Client",
    "OAuthTokenSet",
    "OIDCMetadata",
    "OAuthError",
    "OAuthExchangeError",
    "OAuthValidationError",
    "JWKSCache",
    "OAuthProvider",
    "ExternalClaims",
    "AzureADProvider",
    "GoogleProvider",
    "GitHubProvider",
    "ExternalIdentity",
    "get_or_create_user_for_identity",
    "create_oauth_router",
    "build_providers_from_settings",
    "get_oauth_provider",
    "ExternalTokenValidator",
    "ExternalAuthenticatedUser",
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
    # OAuth2/OIDC (lazy - see __getattr__)
    *sorted(_OAUTH_EXPORTS),
]
