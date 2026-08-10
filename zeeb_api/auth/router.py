"""
Authentication router with login, refresh, logout, register endpoints.
"""

from __future__ import annotations

from typing import Any, Callable, Awaitable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, EmailStr

from zeeb_api.auth.jwt import (
    create_token_pair,
    decode_token,
    get_jwt_config,
    TokenExpiredError,
    TokenInvalidError,
)
from zeeb_api.auth.schemas import (
    TokenResponse,
    RefreshRequest,
    UserInfo,
    LogoutResponse,
)
from zeeb_api.auth.middleware import (
    AuthenticatedUser,
    get_current_user,
)
from zeeb_api.exceptions import (
    AuthenticationException,
    ErrorCode,
    ErrorResponse,
    ValidationException,
    field_error,
)


# Type for user authentication callback
AuthenticateFunc = Callable[[Request, dict[str, Any]], Awaitable[tuple[str, dict[str, Any]] | None]]


class LoginRequest(BaseModel):
    """Login request body."""
    email: EmailStr = Field(description="User's email address")
    password: str = Field(min_length=1, description="User's password")


class RegisterRequest(BaseModel):
    """Registration request body."""
    email: EmailStr = Field(description="User's email address")
    password: str = Field(min_length=8, description="Password (min 8 characters)")
    first_name: str | None = Field(default=None, description="First name")
    last_name: str | None = Field(default=None, description="Last name")


class RegisterResponse(BaseModel):
    """Registration response."""
    id: str = Field(description="User ID")
    email: str = Field(description="User's email")
    message: str = Field(default="User registered successfully")


def create_auth_router(
    authenticate: AuthenticateFunc | None = None,
    prefix: str = "/auth",
    tags: list[str] | None = None,
    on_logout: Callable[[str], Awaitable[None]] | None = None,
    enable_registration: bool = True,
    use_database: bool = True,
    login_throttle: str | None = None,
) -> APIRouter:
    """
    Create an authentication router with login, refresh, logout, register endpoints.

    Args:
        authenticate: Custom async function to validate credentials.
                     If None and use_database=True, uses database authentication.
        prefix: URL prefix for auth routes
        tags: OpenAPI tags
        on_logout: Optional async callback when user logs out
        enable_registration: Whether to include /register endpoint
        use_database: If True, uses database-backed authentication
        login_throttle: Rate limit for the credential endpoints (/login and
                     /register), e.g. ``"10/min"``. These are the two routes an
                     attacker can drive without a token, so they are throttled
                     per client independently of the global throttle settings.
                     ``/refresh``, ``/logout`` and ``/me`` are token-bound and
                     stay unthrottled — a limit there breaks a client with
                     several tabs open. ``None`` disables it.

    Returns:
        FastAPI APIRouter with auth endpoints

    Usage:
        # Database-backed auth (default)
        auth_router = create_auth_router()

        # Custom authentication
        async def my_auth(request, body):
            ...
        auth_router = create_auth_router(authenticate=my_auth)

        # Brake on credential stuffing
        auth_router = create_auth_router(login_throttle="10/min")
    """
    router = APIRouter(prefix=prefix, tags=tags or ["auth"])
    config = get_jwt_config()

    credential_deps = []
    if login_throttle:
        from zeeb_api.throttling import throttle

        credential_deps = [Depends(throttle(login_throttle, scope="auth_login"))]

    # Determine authentication function
    auth_func = authenticate
    if auth_func is None and use_database:
        # Use database-backed authentication
        async def db_authenticate(request: Request, body: dict) -> tuple[str, dict[str, Any]] | None:
            from zeeb_api.auth.backends import authenticate as db_auth
            
            email = body.get("email")
            password = body.get("password")
            
            user = await db_auth(email=email, password=password)
            if user is None:
                return None
            
            # Get claims from user
            claims = {}
            if hasattr(user, "get_claims"):
                claims = user.get_claims()
            
            # Update last login
            if hasattr(user, "update_last_login"):
                await user.update_last_login()
            
            return (str(user.id), claims)
        
        auth_func = db_authenticate
    
    # Login endpoint
    if auth_func:
        @router.post(
            "/login",
            response_model=TokenResponse,
            dependencies=credential_deps,
            responses={
                401: {"model": ErrorResponse, "description": "Invalid credentials"},
                429: {"model": ErrorResponse, "description": "Too many attempts"},
            },
            summary="Login",
            description="Authenticate with email/password and receive access/refresh tokens.",
        )
        async def login(request: Request, body: LoginRequest) -> TokenResponse:
            """
            Login with email and password.
            
            Returns access and refresh tokens on success.
            """
            result = await auth_func(request, body.model_dump())
            if result is None:
                raise AuthenticationException(
                    code=ErrorCode.AUTH_INVALID_CREDENTIALS,
                    message="Invalid email or password",
                )
            
            user_id, claims = result
            access_token, refresh_token = create_token_pair(user_id, claims)
            
            return TokenResponse(
                access_token=access_token,
                refresh_token=refresh_token,
                token_type="bearer",
                expires_in=config.access_token_expire_minutes * 60,
            )
    
    # Registration endpoint
    if enable_registration and use_database:
        @router.post(
            "/register",
            response_model=RegisterResponse,
            dependencies=credential_deps,
            responses={
                400: {"model": ErrorResponse, "description": "Validation error"},
                409: {"model": ErrorResponse, "description": "Email already exists"},
                429: {"model": ErrorResponse, "description": "Too many attempts"},
            },
            summary="Register",
            description="Create a new user account.",
        )
        async def register(body: RegisterRequest) -> RegisterResponse:
            """
            Register a new user.
            
            Creates a new user account with the provided email and password.
            """
            from zeeb_api.auth.backends import get_user_model, create_user

            # See the note in /refresh: `objects` is metaclass-added.
            User: Any = get_user_model()


            # Check if email already exists
            existing = await User.objects.filter(email=body.email).first()
            if existing:
                raise ValidationException(
                    message="Email already registered",
                    details=[
                        field_error("email", ErrorCode.FIELD_UNIQUE_CONSTRAINT, 
                                   "This email is already registered")
                    ],
                )
            
            # Create user
            extra_fields = {}
            if body.first_name:
                extra_fields["first_name"] = body.first_name
            if body.last_name:
                extra_fields["last_name"] = body.last_name
            
            user = await create_user(
                email=body.email,
                password=body.password,
                **extra_fields,
            )
            
            return RegisterResponse(
                id=str(user.id),
                email=user.email,
                message="User registered successfully",
            )
    
    @router.post(
        "/refresh",
        response_model=TokenResponse,
        responses={
            401: {"model": ErrorResponse, "description": "Invalid or expired refresh token"},
        },
        summary="Refresh Token",
        description="Get a new access token using a refresh token.",
    )
    async def refresh(body: RefreshRequest) -> TokenResponse:
        """
        Refresh access token.

        Redeems a valid refresh token for a new access/refresh pair. The
        presented refresh token is *rotated*: it is single-use, and replaying
        it is rejected. With database-backed auth the account is re-validated
        (a deleted or deactivated user cannot refresh) and claims are reloaded
        from the authoritative source so a refreshed token never carries stale
        privileges.
        """
        from datetime import datetime, timezone

        from zeeb_api.auth.refresh_store import get_refresh_token_store

        try:
            payload = decode_token(body.refresh_token, token_type="refresh")
        except TokenExpiredError:
            raise AuthenticationException(
                code=ErrorCode.AUTH_TOKEN_EXPIRED,
                message="Refresh token has expired",
            )
        except TokenInvalidError as e:
            raise AuthenticationException(
                code=ErrorCode.AUTH_TOKEN_INVALID,
                message=str(e),
            )

        store = get_refresh_token_store()

        # Reuse detection: a rotated (already redeemed) refresh token is invalid.
        if payload.jti and await store.is_consumed(payload.jti):
            raise AuthenticationException(
                code=ErrorCode.AUTH_TOKEN_INVALID,
                message="Refresh token has already been used",
            )

        user_id = payload.sub
        claims = payload.claims or {}

        # Re-validate the account and refresh claims from the database. A user
        # that was deleted or deactivated after the token was issued cannot
        # obtain new tokens.
        if use_database:
            from uuid import UUID
            from zeeb_api.auth.backends import get_user_model

            # get_user_model() is typed as `type`; `objects` is added by the
            # model metaclass, so the manager is only visible at runtime.
            User: Any = get_user_model()
            lookup_id: Any = user_id
            try:
                lookup_id = UUID(user_id)
            except (ValueError, TypeError):
                pass

            user = await User.objects.filter(id=lookup_id).first()
            if user is None or not getattr(user, "is_active", True):
                raise AuthenticationException(
                    code=ErrorCode.AUTH_TOKEN_INVALID,
                    message="User no longer exists or is inactive",
                )
            if hasattr(user, "get_claims"):
                claims = user.get_claims()

        # Rotate: consume the presented token for the remainder of its lifetime
        # so it cannot be replayed.
        if payload.jti:
            ttl = (payload.exp - datetime.now(timezone.utc)).total_seconds()
            await store.consume(payload.jti, ttl)

        access_token, refresh_token = create_token_pair(user_id, claims)

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=config.access_token_expire_minutes * 60,
        )
    
    @router.post(
        "/logout",
        response_model=LogoutResponse,
        summary="Logout",
        description="Logout and optionally invalidate tokens.",
    )
    async def logout(
        user: Any = Depends(get_current_user),
    ) -> LogoutResponse:
        """
        Logout current user.
        
        If token blacklist is configured, the current token will be invalidated.
        """
        if on_logout:
            # Get token JTI from user
            token_payload = getattr(user, "_token_payload", None) or getattr(user, "token_payload", None)
            if token_payload:
                await on_logout(token_payload.jti)
        
        return LogoutResponse(
            success=True,
            message="Successfully logged out",
        )
    
    @router.get(
        "/me",
        response_model=UserInfo,
        responses={
            401: {"model": ErrorResponse, "description": "Not authenticated"},
        },
        summary="Get Current User",
        description="Get information about the currently authenticated user.",
    )
    async def get_me(
        user: Any = Depends(get_current_user),
    ) -> UserInfo:
        """
        Get current user info.
        
        Returns user information. If using database auth, returns full user data.
        """
        # Handle both DB user model and AuthenticatedUser
        user_id = str(getattr(user, "id", ""))
        
        # Build claims from user attributes or get_claims method
        claims = {}
        if hasattr(user, "get_claims"):
            claims = user.get_claims()
        elif hasattr(user, "claims"):
            claims = user.claims
        else:
            # Build from common attributes
            for attr in ["email", "username", "first_name", "last_name", 
                        "is_staff", "is_superuser", "is_active"]:
                if hasattr(user, attr):
                    claims[attr] = getattr(user, attr)
        
        return UserInfo(
            id=user_id,
            is_authenticated=True,
            claims=claims,
        )

    # Canonical paths are slash-less; serve trailing-slash variants directly
    # (no 307 redirect, which browsers reject on CORS-preflighted requests).
    from zeeb_api.routers.default import add_slash_alias_routes

    add_slash_alias_routes(router)

    return router
