"""
Authentication router with login, refresh, logout, register endpoints.
"""

from __future__ import annotations

from typing import Annotated, Any, Callable, Awaitable

from fastapi import APIRouter, Body, Depends, Request
from pydantic import AfterValidator, BaseModel, EmailStr, Field

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
    ValidationException,
    error_response_doc,
    field_error,
)


# Type for user authentication callback
AuthenticateFunc = Callable[[Request, dict[str, Any]], Awaitable[tuple[str, dict[str, Any]] | None]]


def _password_within_bcrypt_limit(value: str) -> str:
    """Refuse a password bcrypt cannot hash (over 72 UTF-8 bytes) as a field error.

    bcrypt >= 5 raises ValueError instead of truncating, which made /register
    answer 500. ``string_too_long`` maps to ``FIELD_TOO_LONG`` in the envelope.
    """
    from pydantic_core import PydanticCustomError

    from zeeb_api.auth.hashers import MAX_PASSWORD_BYTES, password_too_long

    if password_too_long(value):
        raise PydanticCustomError(
            "string_too_long",
            "Password must be at most {max_length} bytes (UTF-8 encoded)",
            {"max_length": MAX_PASSWORD_BYTES},
        )
    return value


_BcryptPassword = Annotated[str, AfterValidator(_password_within_bcrypt_limit)]


class LoginRequest(BaseModel):
    """Login request body."""
    email: EmailStr = Field(description="User's email address")
    password: _BcryptPassword = Field(
        min_length=1, description="User's password (at most 72 bytes, UTF-8)"
    )


class RegisterRequest(BaseModel):
    """Registration request body."""
    email: EmailStr = Field(description="User's email address")
    password: _BcryptPassword = Field(
        min_length=8, description="Password (min 8 characters, at most 72 bytes UTF-8)"
    )
    first_name: str | None = Field(default=None, description="First name")
    last_name: str | None = Field(default=None, description="Last name")


class LogoutRequest(BaseModel):
    """Logout request body (optional)."""
    refresh_token: str | None = Field(
        default=None, description="The session's refresh token, to revoke it"
    )


async def _revoke_refresh_token(token: str, user: Any) -> None:
    """Consume *token* and revoke its family, if it is *user*'s valid refresh token."""
    from datetime import datetime, timezone

    from zeeb_api.auth.jwt import TokenError
    from zeeb_api.auth.refresh_store import get_refresh_token_store

    try:
        payload = decode_token(token, token_type="refresh")
    except TokenError:
        return
    if payload.sub != str(getattr(user, "id", "")):
        return
    store = get_refresh_token_store()
    ttl = (payload.exp - datetime.now(timezone.utc)).total_seconds()
    await store.consume(payload.jti, ttl)
    await store.revoke_family(
        payload.family or payload.jti, get_jwt_config().refresh_token_expire_days * 86400
    )


class RegisterResponse(BaseModel):
    """Registration response."""
    id: str = Field(description="User ID")
    email: str = Field(description="User's email")
    message: str = Field(default="User registered successfully")


class _SameAsLogin:
    """Sentinel: ``refresh_throttle`` defaults to the ``login_throttle`` rate."""

    def __repr__(self) -> str:
        return "<same as login_throttle>"


_SAME_AS_LOGIN = _SameAsLogin()


def create_auth_router(
    authenticate: AuthenticateFunc | None = None,
    prefix: str = "/auth",
    tags: list[str] | None = None,
    on_logout: Callable[[str], Awaitable[None]] | None = None,
    enable_registration: bool = True,
    use_database: bool = True,
    login_throttle: str | None = None,
    refresh_throttle: str | None | _SameAsLogin = _SAME_AS_LOGIN,
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
                     /register), e.g. ``"10/min"``. These are the routes an
                     attacker can drive without an access token, so they are
                     throttled per client independently of the global throttle
                     settings. ``None`` disables it.
        refresh_throttle: Rate limit for ``/refresh``, which is equally
                     reachable without an access token (a stolen or guessed
                     refresh token is all it takes). Defaults to the
                     ``login_throttle`` rate, counted in its own bucket so
                     refreshes never use up the login budget. A client
                     refreshes once per access-token lifetime, far below any
                     sane limit. ``None`` disables it. ``/logout`` and ``/me``
                     need a valid access token and stay unthrottled.

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

    from zeeb_api.throttling import throttle

    credential_deps = []
    if login_throttle:
        credential_deps = [Depends(throttle(login_throttle, scope="auth_login"))]
    if isinstance(refresh_throttle, _SameAsLogin):
        refresh_throttle = login_throttle
    refresh_deps = []
    if refresh_throttle:
        refresh_deps = [Depends(throttle(refresh_throttle, scope="auth_refresh"))]

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
                401: error_response_doc(
                    401,
                    "Invalid credentials",
                    code=ErrorCode.AUTH_INVALID_CREDENTIALS.value,
                    message="Invalid email or password",
                ),
                429: error_response_doc(429, "Too many attempts"),
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
                400: error_response_doc(400),
                409: error_response_doc(
                    409, "Email already exists", message="A user with this email already exists"
                ),
                429: error_response_doc(429, "Too many attempts"),
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
            
            # Create user. Name fields are passed only when the user model
            # has them: a custom AUTH_USER_MODEL need not, and the model
            # constructor rejects unknown keyword arguments.
            extra_fields = {}
            if body.first_name and hasattr(User, "first_name"):
                extra_fields["first_name"] = body.first_name
            if body.last_name and hasattr(User, "last_name"):
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
        dependencies=refresh_deps,
        responses={
            401: error_response_doc(
                401,
                "Invalid or expired refresh token",
                code=ErrorCode.AUTH_TOKEN_INVALID.value,
                message="Invalid refresh token",
            ),
            429: error_response_doc(429, "Too many attempts"),
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
        # Tokens minted before families existed carry no ``fam``: each is its
        # own family.
        family = payload.family or payload.jti
        ttl = (payload.exp - datetime.now(timezone.utc)).total_seconds()

        if await store.is_family_revoked(family):
            raise AuthenticationException(
                code=ErrorCode.AUTH_TOKEN_INVALID,
                message="Refresh token has been revoked",
            )

        # Rotate: consume the presented token in one atomic step (check and
        # write together, so two concurrent replays cannot both succeed).
        if not await store.consume_once(payload.jti, ttl):
            # Reuse of a rotated token: someone holds a copy. The owner's and
            # the thief's descendants are indistinguishable, so the whole
            # family goes - for the longest lifetime a descendant can have.
            await store.revoke_family(family, config.refresh_token_expire_days * 86400)
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

        access_token, refresh_token = create_token_pair(user_id, claims, family=family)

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
        description=(
            "Logout. Send the session's refresh token in the body to revoke it "
            "(and every token rotated from it); the access token is handed to "
            "the on_logout hook."
        ),
    )
    async def logout(
        body: LogoutRequest | None = Body(default=None),
        user: Any = Depends(get_current_user),
    ) -> LogoutResponse:
        """
        Logout current user.
        
        A refresh token in the body is consumed and its family revoked, so it
        cannot mint new access tokens after logout. A token that is invalid,
        expired or belongs to another user is ignored. If a token blacklist is
        configured (``on_logout``), the current access token is handed to it.
        """
        if body is not None and body.refresh_token:
            await _revoke_refresh_token(body.refresh_token, user)

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
            401: error_response_doc(401),
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
