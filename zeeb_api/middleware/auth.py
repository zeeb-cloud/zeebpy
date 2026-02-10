"""
JWT authentication middleware and FastAPI dependencies.

Moved from zeeb_api.auth.middleware for better organization.
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from zeeb_api.auth.jwt import (
    TokenPayload,
    decode_token,
    TokenError,
    TokenExpiredError,
    TokenInvalidError,
)
from zeeb_api.exceptions import AuthenticationException, ErrorCode


# Type for user loader function
UserLoaderFunc = Callable[[TokenPayload], Awaitable[Any]]


class AuthenticatedUser:
    """
    User object attached to request.state.user after authentication.
    
    This is a fallback when no user_loader is configured.
    When using database-backed auth, the actual User model instance is used instead.
    """
    
    def __init__(self, token_payload: TokenPayload):
        self.id = token_payload.sub
        self.token_payload = token_payload
        self.claims = token_payload.claims
        self.is_authenticated = True
    
    def __repr__(self) -> str:
        return f"<AuthenticatedUser id={self.id}>"
    
    @property
    def is_staff(self) -> bool:
        """Check if user has staff role (from claims)."""
        return self.claims.get("is_staff", False)
    
    @property
    def is_admin(self) -> bool:
        """Check if user has admin role (from claims)."""
        return self.claims.get("is_admin", False)
    
    @property
    def is_superuser(self) -> bool:
        """Check if user is superuser (from claims)."""
        return self.claims.get("is_superuser", False)


async def default_user_loader(payload: TokenPayload) -> Any:
    """
    Default user loader that fetches user from database.
    
    Uses get_user_model() to get the configured User model and
    queries by the user ID from the token.
    """
    from uuid import UUID
    from zeeb_api.auth.backends import get_user_model
    
    User = get_user_model()
    try:
        # Convert string UUID to UUID object for proper comparison
        user_id = payload.sub
        try:
            user_id = UUID(user_id)
        except (ValueError, TypeError):
            pass  # Not a UUID, use as-is
        
        user = await User.objects.get(id=user_id)
        # Attach token payload for access to claims
        user._token_payload = payload
        return user
    except Exception as e:
        # Log the actual exception for debugging
        import logging
        logging.getLogger(__name__).warning(f"Failed to load user {payload.sub}: {type(e).__name__}: {e}")
        # User not found or DB error - fall back to AuthenticatedUser
        return AuthenticatedUser(payload)


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that extracts and validates JWT Bearer tokens.
    
    Sets request.state.user to the User model instance if token is valid.
    Does NOT raise errors for missing/invalid tokens - use dependencies for that.
    
    Configuration via settings.py:
        MIDDLEWARE = [
            "zeeb_api.middleware.JWTAuthMiddleware",
        ]
        AUTH_LOAD_USER_FROM_DB = True  # Load user from database
    
    Direct usage:
        app.add_middleware(JWTAuthMiddleware, load_user_from_db=True)
    """
    
    def __init__(
        self,
        app: Any,
        user_loader: UserLoaderFunc | None = None,
        load_user_from_db: bool | None = None,
    ):
        """
        Initialize middleware.
        
        Args:
            app: FastAPI/Starlette app
            user_loader: Custom async function to load user from token payload.
            load_user_from_db: If True and no user_loader provided, loads user from DB.
                              If False, uses AuthenticatedUser (token claims only).
                              If None, reads from settings.AUTH_LOAD_USER_FROM_DB.
        """
        super().__init__(app)
        
        # Get load_user_from_db from settings if not specified
        if load_user_from_db is None:
            from zeeb_api.conf import settings
            load_user_from_db = getattr(settings, 'AUTH_LOAD_USER_FROM_DB', True)
        
        if user_loader:
            self.user_loader = user_loader
        elif load_user_from_db:
            self.user_loader = default_user_loader
        else:
            self.user_loader = None
    
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Initialize user as None
        request.state.user = None
        
        # Try to extract and validate token
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]  # Remove "Bearer " prefix
            
            try:
                payload = decode_token(token, token_type="access")
                
                # Load user (custom loader or default)
                if self.user_loader:
                    request.state.user = await self.user_loader(payload)
                else:
                    request.state.user = AuthenticatedUser(payload)
                    
            except TokenError:
                # Don't raise - let endpoints decide if auth is required
                pass
        
        return await call_next(request)


# FastAPI security scheme for OpenAPI docs
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser | None:
    """
    FastAPI dependency that returns the current user or None.
    
    Does not raise an error if not authenticated.
    Use this for endpoints that work with or without auth.
    
    Usage:
        @app.get("/items/")
        async def list_items(user: AuthenticatedUser | None = Depends(get_current_user_optional)):
            if user:
                # Authenticated user
            else:
                # Anonymous user
    """
    # First check if middleware already set the user
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    
    # If no middleware, try to decode from credentials
    if credentials is None:
        return None
    
    try:
        payload = decode_token(credentials.credentials, token_type="access")
        return AuthenticatedUser(payload)
    except TokenError:
        return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    """
    FastAPI dependency that requires authentication.
    
    Raises AuthenticationException if not authenticated.
    
    Usage:
        @app.get("/me/")
        async def get_me(user: AuthenticatedUser = Depends(get_current_user)):
            return {"id": user.id}
    """
    # First check if middleware already set the user
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    
    # No middleware or no user - try to decode from credentials
    if credentials is None:
        raise AuthenticationException(
            code=ErrorCode.AUTH_TOKEN_MISSING,
            message="Authentication required",
        )
    
    try:
        payload = decode_token(credentials.credentials, token_type="access")
        return AuthenticatedUser(payload)
    except TokenExpiredError:
        raise AuthenticationException(
            code=ErrorCode.AUTH_TOKEN_EXPIRED,
            message="Token has expired",
        )
    except TokenInvalidError as e:
        raise AuthenticationException(
            code=ErrorCode.AUTH_TOKEN_INVALID,
            message=str(e),
        )


def require_auth(
    roles: list[str] | None = None,
    any_role: bool = False,
) -> Callable:
    """
    Dependency factory for role-based authentication.
    
    Args:
        roles: List of required roles (from claims)
        any_role: If True, user needs any one of the roles. If False, needs all.
    
    Usage:
        @app.get("/admin/")
        async def admin_only(user: AuthenticatedUser = Depends(require_auth(roles=["admin"]))):
            return {"message": "Welcome admin"}
    """
    async def dependency(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if roles:
            user_roles = user.claims.get("roles", [])
            
            if any_role:
                # User needs at least one of the roles
                if not any(role in user_roles for role in roles):
                    raise AuthenticationException(
                        code=ErrorCode.PERM_INSUFFICIENT_ROLE,
                        message=f"Requires one of roles: {', '.join(roles)}",
                    )
            else:
                # User needs all roles
                missing = [r for r in roles if r not in user_roles]
                if missing:
                    raise AuthenticationException(
                        code=ErrorCode.PERM_INSUFFICIENT_ROLE,
                        message=f"Missing required roles: {', '.join(missing)}",
                    )
        
        return user
    
    return dependency
