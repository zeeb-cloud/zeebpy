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
)
from zeeb_api.exceptions import AuthenticationException, ErrorCode, PermissionException


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

    SECURITY: this loader must fail *closed*. It never falls back to a
    claims-only ``AuthenticatedUser``, because that would keep a deleted or
    deactivated user authenticated (with the ``is_staff``/``is_superuser``
    baked into the token) and would authenticate every request from stale
    token claims during a database outage. Instead:
      - user not found (deleted) or inactive -> return None (anonymous);
      - any other error (e.g. DB unavailable) -> propagate, so the request
        fails rather than authenticating from unverifiable claims.
    """
    import logging
    from uuid import UUID
    from zeeb_api.auth.backends import get_user_model

    User = get_user_model()

    # Convert string UUID to UUID object for proper comparison
    user_id = payload.sub
    try:
        user_id = UUID(user_id)
    except (ValueError, TypeError):
        pass  # Not a UUID, use as-is

    try:
        user = await User.objects.get(id=user_id)
    except (User.DoesNotExist, User.MultipleObjectsReturned):
        # The subject no longer resolves to exactly one account: treat as
        # anonymous rather than trusting the token's embedded claims.
        logging.getLogger(__name__).info(
            "Token subject %s does not resolve to a user; treating as anonymous",
            payload.sub,
        )
        return None

    # Deactivated accounts must not stay authenticated for the token's lifetime.
    if not getattr(user, "is_active", True):
        logging.getLogger(__name__).info(
            "User %s is inactive; treating as anonymous", payload.sub
        )
        return None

    # Attach token payload for access to claims
    user._token_payload = payload
    return user


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
        external_validators: list[Any] | None = None,
    ):
        """
        Initialize middleware.

        Args:
            app: FastAPI/Starlette app
            user_loader: Custom async function to load user from token payload.
            load_user_from_db: If True and no user_loader provided, loads user from DB.
                              If False, uses AuthenticatedUser (token claims only).
                              If None, reads from settings.AUTH_LOAD_USER_FROM_DB.
            external_validators: Optional list of async callables
                ``(token) -> user | None`` tried (first non-None wins) when the
                token is not a locally-issued JWT (e.g. Azure AD tokens). None
                (the default) lazily self-configures from
                ``settings.OAUTH_ACCEPT_EXTERNAL_TOKENS`` on first request
                (``install_middleware`` passes no kwargs); pass ``[]`` to
                disable explicitly.
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

        # None = lazily resolve from settings on first request; [] = disabled.
        self._external_validators = external_validators

    def _get_external_validators(self) -> list[Any]:
        """Resolve external token validators (lazy settings-based config)."""
        if self._external_validators is None:
            validators: list[Any] = []
            try:
                from zeeb_api.conf import settings
                names = getattr(settings, "OAUTH_ACCEPT_EXTERNAL_TOKENS", []) or []
                if names:
                    from zeeb_api.auth.oauth.bearer import build_validators_from_settings
                    validators = build_validators_from_settings()
            except Exception:
                validators = []
            self._external_validators = validators
        return self._external_validators

    async def _try_external_validators(self, token: str) -> Any | None:
        """Try external validators (first non-None wins). Never raises."""
        for validator in self._get_external_validators():
            try:
                user = await validator(token)
            except Exception:
                continue
            if user is not None:
                return user
        return None

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Initialize user as None
        request.state.user = None
        # The dependencies below read this: once the middleware has decided a
        # request is anonymous, they must not decode the token again and
        # re-authenticate what the middleware refused (a deactivated user).
        request.state.auth_resolved = True

        token = _bearer_token(request)
        if token is not None:
            user, error = await _authenticate_bearer(
                token, self.user_loader, self._try_external_validators
            )
            request.state.user = user
            if error is not None:
                request.state.auth_error = error

        return await call_next(request)


def _bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


async def _authenticate_bearer(
    token: str,
    user_loader: UserLoaderFunc | None,
    try_external: Callable[[str], Awaitable[Any]],
) -> tuple[Any | None, ErrorCode | None]:
    """Resolve a Bearer token to ``(user, None)`` or ``(None, error_code)``.

    The one place a token becomes a user, shared by the middleware and by the
    dependencies when no middleware is installed, so both refuse the same
    tokens:

    - a local access token is decoded and handed to *user_loader* (the
      database loader, which returns None for a deleted or deactivated
      account); with no loader a claims-only ``AuthenticatedUser`` is built;
    - a token that is not a valid local JWT is offered to the external
      validators (e.g. Azure AD);
    - a valid token whose account no longer resolves is ``AUTH_TOKEN_INVALID``,
      an expired one ``AUTH_TOKEN_EXPIRED``.

    Database errors from the loader propagate: they fail the request instead
    of authenticating it from claims nobody could verify.
    """
    try:
        payload = decode_token(token, token_type="access")
    except TokenError as exc:
        # Not a valid locally-issued token. Give externally-issued tokens a
        # chance, still never raising.
        user = await try_external(token)
        if user is not None:
            return user, None
        # Record why local validation failed so a downstream denial can tell
        # an EXPIRED token (the client should refresh) from an invalid one.
        if isinstance(exc, TokenExpiredError):
            return None, ErrorCode.AUTH_TOKEN_EXPIRED
        return None, ErrorCode.AUTH_TOKEN_INVALID

    if user_loader is None:
        return AuthenticatedUser(payload), None
    user = await user_loader(payload)
    if user is None:
        return None, ErrorCode.AUTH_TOKEN_INVALID
    return user, None


def _settings_user_loader() -> UserLoaderFunc | None:
    """The loader ``JWTAuthMiddleware`` would use with no arguments."""
    from zeeb_api.conf import settings

    if getattr(settings, "AUTH_LOAD_USER_FROM_DB", True):
        return default_user_loader
    return None


async def _settings_external_validators(token: str) -> Any | None:
    """``OAUTH_ACCEPT_EXTERNAL_TOKENS`` validators, first non-None wins."""
    from zeeb_api.conf import settings

    if not (getattr(settings, "OAUTH_ACCEPT_EXTERNAL_TOKENS", []) or []):
        return None
    from zeeb_api.auth.oauth.bearer import build_validators_from_settings

    for validator in build_validators_from_settings():
        try:
            user = await validator(token)
        except Exception:
            continue
        if user is not None:
            return user
    return None


async def _resolve_request_user(
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> tuple[Any | None, ErrorCode | None]:
    """The request's user for the dependencies, as the middleware would see it.

    With ``JWTAuthMiddleware`` installed its verdict stands, including "this
    token authenticates nobody". Without it, the token is resolved here with
    the same loader and validators the middleware would use — never as a
    claims-only user while ``AUTH_LOAD_USER_FROM_DB`` is on, which is what kept
    a deactivated account authenticated for the token's lifetime.
    """
    state = request.state
    user = getattr(state, "user", None)
    if user is not None:
        return user, None
    if getattr(state, "auth_resolved", False):
        return None, getattr(state, "auth_error", None)
    if credentials is None:
        return None, None
    return await _authenticate_bearer(
        credentials.credentials, _settings_user_loader(), _settings_external_validators
    )


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
    user, _error = await _resolve_request_user(request, credentials)
    return user


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    """
    FastAPI dependency that requires authentication.
    
    Raises AuthenticationException if not authenticated.
    
    Usage:
        @app.get("/me")
        async def get_me(user: AuthenticatedUser = Depends(get_current_user)):
            return {"id": user.id}
    """
    user, error = await _resolve_request_user(request, credentials)
    if user is not None:
        return user
    if error == ErrorCode.AUTH_TOKEN_EXPIRED:
        raise AuthenticationException(
            code=ErrorCode.AUTH_TOKEN_EXPIRED,
            message="Token has expired",
        )
    if error is not None:
        raise AuthenticationException(
            code=ErrorCode.AUTH_TOKEN_INVALID,
            message="Invalid authentication token",
        )
    raise AuthenticationException(
        code=ErrorCode.AUTH_TOKEN_MISSING,
        message="Authentication required",
    )


def get_user_roles(user: Any) -> set[str]:
    """The roles ``require_auth(roles=...)`` checks a user against.

    The contract, in order:

    - ``user.get_roles()`` when the user model defines it (override this on a
      custom user model to source roles from anywhere, e.g. group membership);
    - a ``roles`` attribute on the user, or a ``roles`` claim in its token
      (``claims["roles"]``, which is also where Azure AD puts app roles);
    - the account flags: ``"staff"`` for ``is_staff``, ``"superuser"`` for
      ``is_superuser``, and ``"admin"`` for anyone ``IsAdminUser`` admits
      (``is_staff``, ``is_admin`` or ``is_superuser``).

    ``AbstractUser.get_roles()`` returns exactly the flag-derived set, and
    ``AbstractUser.get_claims()`` emits it as the ``roles`` claim, so a
    token-only ``AuthenticatedUser`` sees the same roles.
    """
    roles: set[str] = set()
    get_roles = getattr(user, "get_roles", None)
    if callable(get_roles):
        roles.update(str(r) for r in (get_roles() or ()))

    declared = getattr(user, "roles", None)
    if isinstance(declared, (list, tuple, set, frozenset)):
        roles.update(str(r) for r in declared)
    claims = getattr(user, "claims", None)
    if not isinstance(claims, dict):
        payload = getattr(user, "_token_payload", None)
        claims = getattr(payload, "claims", None)
    if isinstance(claims, dict):
        claimed = claims.get("roles")
        if isinstance(claimed, str):
            roles.add(claimed)
        elif isinstance(claimed, (list, tuple)):
            roles.update(str(r) for r in claimed)

    is_staff = bool(getattr(user, "is_staff", False))
    is_superuser = bool(getattr(user, "is_superuser", False))
    if is_staff:
        roles.add("staff")
    if is_superuser:
        roles.add("superuser")
    if is_staff or is_superuser or bool(getattr(user, "is_admin", False)):
        roles.add("admin")
    return roles


def require_auth(
    roles: list[str] | None = None,
    any_role: bool = False,
) -> Callable:
    """
    Dependency factory for role-based authentication.
    
    Args:
        roles: Required roles, as :func:`get_user_roles` reports them.
        any_role: If True, user needs any one of the roles. If False, needs all.

    An unauthenticated request is refused with 401; an authenticated user
    lacking a role with 403 ``PERM_INSUFFICIENT_ROLE``.
    
    Usage:
        @app.get("/admin/")
        async def admin_only(user: AuthenticatedUser = Depends(require_auth(roles=["admin"]))):
            return {"message": "Welcome admin"}
    """
    async def dependency(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if roles:
            user_roles = get_user_roles(user)
            
            if any_role:
                # User needs at least one of the roles
                if not any(role in user_roles for role in roles):
                    raise PermissionException(
                        code=ErrorCode.PERM_INSUFFICIENT_ROLE,
                        message=f"Requires one of roles: {', '.join(roles)}",
                    )
            else:
                # User needs all roles
                missing = [r for r in roles if r not in user_roles]
                if missing:
                    raise PermissionException(
                        code=ErrorCode.PERM_INSUFFICIENT_ROLE,
                        message=f"Missing required roles: {', '.join(missing)}",
                    )
        
        return user
    
    return dependency
