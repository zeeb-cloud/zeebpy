"""
DRF-style per-viewset authentication classes.

A ViewSet may set ``authentication_classes = [...]`` to override the global
authentication middleware for its endpoints. The router runs the listed
authenticators in order before the permission check; the first one that
returns a user wins.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request


def _bearer_token(request: Request) -> str | None:
    """Extract the Bearer token from the Authorization header, if any."""
    header = request.headers.get("Authorization")
    if header and header.startswith("Bearer "):
        return header[7:]
    return None


class BaseAuthentication:
    """
    Base class for per-viewset authentication.

    Subclasses implement ``authenticate``:

    - return a user object on success (it becomes ``request.state.user``);
    - return None when the request carries no credentials this authenticator
      understands, so the next class in ``authentication_classes`` can try;
    - raise ``zeeb_api.exceptions.AuthenticationException`` to hard-reject the
      request (401) without trying further authenticators.

    The built-in Bearer authenticators below record the failure reason on
    ``request.state.auth_error`` and return None instead of raising, so that
    e.g. ``[JWTAuthentication, OAuth2BearerAuthentication]`` composes: a token
    the first class cannot validate may still be valid for the second.
    """

    async def authenticate(self, request: Request) -> Any | None:
        raise NotImplementedError(
            f"{type(self).__name__} must implement authenticate()"
        )


class JWTAuthentication(BaseAuthentication):
    """
    Authenticate locally-issued JWT Bearer tokens.

    Uses the same token validation and user loading as ``JWTAuthMiddleware``:
    a valid access token loads the user from the database (falling back to a
    claims-only ``AuthenticatedUser`` if the lookup fails). Expired/invalid
    tokens record ``request.state.auth_error`` (so a later permission denial
    yields a precise 401) and return None.
    """

    load_user_from_db: bool = True

    async def authenticate(self, request: Request) -> Any | None:
        from zeeb_api.auth.jwt import TokenError, TokenExpiredError, decode_token
        from zeeb_api.exceptions import ErrorCode

        token = _bearer_token(request)
        if token is None:
            return None
        try:
            payload = decode_token(token, token_type="access")
        except TokenExpiredError:
            request.state.auth_error = ErrorCode.AUTH_TOKEN_EXPIRED
            return None
        except TokenError:
            request.state.auth_error = ErrorCode.AUTH_TOKEN_INVALID
            return None
        if self.load_user_from_db:
            from zeeb_api.auth.middleware import default_user_loader

            return await default_user_loader(payload)
        from zeeb_api.auth.middleware import AuthenticatedUser

        return AuthenticatedUser(payload)


class JWTStatelessAuthentication(JWTAuthentication):
    """``JWTAuthentication`` without the database user lookup (claims only)."""

    load_user_from_db = False


class OAuth2BearerAuthentication(BaseAuthentication):
    """
    Authenticate externally-issued OAuth2/OIDC bearer tokens (e.g. Azure AD).

    Validates against the JWKS of the configured providers via
    ``zeeb_api.auth.oauth.bearer.ExternalTokenValidator``. With
    ``providers=None`` (the default) the provider list comes from
    ``settings.OAUTH_ACCEPT_EXTERNAL_TOKENS`` — the same setting the global
    middleware honors. Validators never raise; an unrecognized token returns
    None so the next authenticator (or the permission layer) decides.
    """

    providers: list[str] | None = None

    async def authenticate(self, request: Request) -> Any | None:
        token = _bearer_token(request)
        if token is None:
            return None
        for validator in self._get_validators():
            try:
                user = await validator(token)
            except Exception:
                continue
            if user is not None:
                return user
        return None

    def _get_validators(self) -> list[Any]:
        # Lazy import: the oauth package needs the "oauth" extra and registers
        # DB tables on import.
        from zeeb_api.auth.oauth.bearer import build_validators_from_settings

        if self.providers is None:
            return build_validators_from_settings()
        from zeeb_api.auth.oauth.bearer import ExternalTokenValidator
        from zeeb_api.auth.oauth.registry import get_oauth_provider

        return [ExternalTokenValidator(get_oauth_provider(n)) for n in self.providers]
