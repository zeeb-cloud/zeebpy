"""
OAuth2/OIDC router: authorize / callback / token / providers endpoints.

Flows:
    - Browser flow: GET /{provider}/authorize/ redirects to the IdP;
      GET or POST (form_post) /{provider}/callback/ completes the login and
      returns a JSON TokenResponse or redirects with tokens in the URL
      fragment.
    - SPA flow: the SPA performs the redirect itself and POSTs the
      authorization code to /{provider}/token/, always receiving JSON.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse, RedirectResponse, Response

from zeeb_api.auth.jwt import create_token_pair, get_jwt_config
from zeeb_api.auth.oauth.client import (
    OAuthError,
    OAuthValidationError,
)
from zeeb_api.auth.oauth.provider import ExternalClaims, OAuthProvider
from zeeb_api.auth.oauth.state import (
    create_state_token,
    decode_state_token,
    generate_nonce,
    generate_pkce_pair,
    pkce_cookie_name,
)
from zeeb_api.auth.schemas import TokenResponse
from zeeb_api.exceptions import (
    AuthenticationException,
    ErrorCode,
    ErrorResponse,
    ZeebException,
)

# Custom user-resolution hook: (provider_name, claims) -> (user, identity, created)
UpsertFunc = Callable[[str, ExternalClaims], Awaitable[tuple[Any, Any, bool]]]
# Post-login hook: (user, identity, created)
OnLoginFunc = Callable[[Any, Any, bool], Awaitable[None]]


class TokenExchangeRequest(BaseModel):
    """SPA code-exchange request body for POST /{provider}/token/."""

    code: str = Field(description="Authorization code returned by the IdP")
    redirect_uri: str = Field(description="Redirect URI used in the authorization request")
    code_verifier: str | None = Field(default=None, description="PKCE code verifier")
    state: str | None = Field(
        default=None,
        description="State token from the authorize endpoint (enables nonce checking)",
    )


class ProviderInfo(BaseModel):
    """Public info about a configured provider."""

    name: str
    authorize_url: str


def _provider_not_found(name: str) -> ZeebException:
    return ZeebException(
        code=ErrorCode.AUTH_OAUTH_PROVIDER_NOT_FOUND,
        message=f"OAuth provider {name!r} is not configured",
        status_code=404,
    )


def _is_safe_next(url: str, allowed_hosts: set[str]) -> bool:
    """Whether a user-supplied ``next`` redirect target is safe to honor.

    Relative paths (no scheme/host, single leading ``/``) are always allowed.
    Absolute URLs are allowed only with an http(s) scheme AND a host present in
    ``allowed_hosts``. Everything else is rejected so attacker-controlled origins
    cannot receive the tokens appended in the redirect fragment.
    """
    if not url or any(c in url for c in ("\n", "\r", "\t")):
        return False
    # Browsers treat backslashes as forward slashes; normalize before parsing.
    url = url.replace("\\", "/")
    parsed = urlparse(url)
    if not parsed.scheme and not parsed.netloc:
        # Relative path only; reject protocol-relative "//host".
        return url.startswith("/") and not url.startswith("//")
    return parsed.scheme in ("http", "https") and parsed.hostname in allowed_hosts


def _translate_oauth_error(error: OAuthError) -> AuthenticationException:
    if isinstance(error, OAuthValidationError):
        code = ErrorCode.AUTH_OAUTH_ID_TOKEN_INVALID
    else:
        code = ErrorCode.AUTH_OAUTH_EXCHANGE_FAILED
    return AuthenticationException(code=code, message=str(error))


def create_oauth_router(
    providers: dict[str, OAuthProvider] | None = None,
    prefix: str = "/auth",
    tags: list[str] | None = None,
    get_or_create_user: UpsertFunc | None = None,
    on_login: OnLoginFunc | None = None,
    success_redirect: str | None = None,
) -> APIRouter:
    """
    Create the OAuth router.

    Args:
        providers: Explicit ``{name: OAuthProvider}`` mapping. None uses the
            settings-driven registry (``OAUTH_PROVIDERS``).
        prefix: URL prefix.
        tags: OpenAPI tags.
        get_or_create_user: Custom async hook
            ``(provider_name, claims) -> (user, identity, created)`` replacing
            the default ExternalIdentity-based upsert.
        on_login: Optional async post-login hook ``(user, identity, created)``.
        success_redirect: Browser-flow redirect target after login (tokens are
            appended in the URL fragment). Falls back to
            ``settings.OAUTH_SUCCESS_REDIRECT``; None returns JSON.
    """
    router = APIRouter(prefix=prefix, tags=tags or ["oauth"])

    def _get_providers() -> dict[str, OAuthProvider]:
        if providers is not None:
            return providers
        from zeeb_api.auth.oauth.registry import get_registered_providers
        return get_registered_providers()

    def _get_provider(name: str) -> OAuthProvider:
        try:
            return _get_providers()[name]
        except KeyError:
            raise _provider_not_found(name)

    def _resolve_redirect_uri(request: Request, provider: OAuthProvider, name: str) -> str:
        if provider.redirect_uri:
            return provider.redirect_uri
        from zeeb_api.conf import settings
        configured = getattr(settings, "OAUTH_REDIRECT_URI", None)
        if configured:
            return configured
        return str(request.url_for("oauth_callback", provider=name))

    def _resolve_success_redirect(state_next: str | None) -> str | None:
        from zeeb_api.conf import settings
        if state_next:
            allowed = set(getattr(settings, "OAUTH_ALLOWED_REDIRECT_HOSTS", []) or [])
            # Only honor a user-supplied `next` that is safe; otherwise fall
            # through to the developer-configured (trusted) redirect so tokens
            # can't be leaked to an attacker-controlled origin.
            if _is_safe_next(state_next, allowed):
                return state_next
        if success_redirect:
            return success_redirect
        return getattr(settings, "OAUTH_SUCCESS_REDIRECT", None)

    async def _upsert_user(
        provider: OAuthProvider, name: str, claims: ExternalClaims
    ) -> tuple[Any, Any, bool]:
        if get_or_create_user is not None:
            return await get_or_create_user(name, claims)

        from zeeb_api.auth.oauth.models import get_or_create_user_for_identity
        from zeeb_api.conf import settings

        auto_create = provider.auto_create_user
        if auto_create is None:
            auto_create = bool(getattr(settings, "OAUTH_AUTO_CREATE_USERS", True))
        link_by_email = provider.link_by_email
        if link_by_email is None:
            link_by_email = bool(getattr(settings, "OAUTH_LINK_BY_EMAIL", True))

        return await get_or_create_user_for_identity(
            name, claims, auto_create=auto_create, link_by_email=link_by_email
        )

    def _issue_tokens(user: Any) -> TokenResponse:
        claims: dict[str, Any] = {}
        if hasattr(user, "get_claims"):
            claims = user.get_claims()
        access_token, refresh_token = create_token_pair(str(user.id), claims)
        config = get_jwt_config()
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            expires_in=config.access_token_expire_minutes * 60,
        )

    async def _complete_login(
        provider: OAuthProvider,
        name: str,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None,
        nonce: str | None,
    ) -> TokenResponse:
        try:
            tokens = await provider.exchange_code(
                code=code, redirect_uri=redirect_uri, code_verifier=code_verifier
            )
            claims = await provider.get_claims(tokens, nonce=nonce)
        except OAuthError as e:
            raise _translate_oauth_error(e)

        user, identity, created = await _upsert_user(provider, name, claims)
        if on_login is not None:
            await on_login(user, identity, created)
        return _issue_tokens(user)

    @router.get(
        "/providers/",
        response_model=list[ProviderInfo],
        summary="List OAuth Providers",
        description="List configured OAuth providers and their authorize URLs.",
    )
    async def oauth_providers(request: Request) -> list[ProviderInfo]:
        return [
            ProviderInfo(
                name=name,
                authorize_url=str(request.url_for("oauth_authorize", provider=name)),
            )
            for name in _get_providers()
        ]

    @router.get(
        "/{provider}/authorize/",
        name="oauth_authorize",
        responses={404: {"model": ErrorResponse, "description": "Unknown provider"}},
        summary="Start OAuth Login",
        description=(
            "Redirects the browser to the identity provider. Carries a signed "
            "state token and (when PKCE is enabled) sets the PKCE verifier "
            "cookie consumed by the callback."
        ),
    )
    async def oauth_authorize(
        request: Request, provider: str, next: str | None = None
    ) -> Response:
        provider_name = provider
        provider_obj = _get_provider(provider_name)
        redirect_uri = _resolve_redirect_uri(request, provider_obj, provider_name)

        nonce = generate_nonce()
        state = create_state_token(
            provider_name, nonce=nonce, redirect_uri=redirect_uri, next_url=next
        )

        code_challenge = None
        code_verifier = None
        if provider_obj.use_pkce:
            code_verifier, code_challenge = generate_pkce_pair()

        url = await provider_obj.build_authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=code_challenge,
        )
        response = RedirectResponse(url, status_code=307)
        if code_verifier:
            from zeeb_api.conf import settings
            response.set_cookie(
                pkce_cookie_name(provider_name),
                code_verifier,
                max_age=int(getattr(settings, "OAUTH_STATE_TTL_SECONDS", 600)),
                httponly=True,
                secure=True,
                samesite="lax",
                path="/",
            )
        return response

    @router.api_route(
        "/{provider}/callback/",
        methods=["GET", "POST"],
        name="oauth_callback",
        responses={
            401: {"model": ErrorResponse, "description": "OAuth login failed"},
            404: {"model": ErrorResponse, "description": "Unknown provider"},
        },
        summary="OAuth Callback",
        description=(
            "Completes the browser login. Accepts the standard GET redirect "
            "and the form_post response mode (POST, used by Azure AD). "
            "Returns a JSON TokenResponse, or redirects to the configured "
            "success URL with tokens in the URL fragment."
        ),
    )
    async def oauth_callback(request: Request, provider: str) -> Response:
        provider_name = provider
        provider_obj = _get_provider(provider_name)

        if request.method == "POST":
            form = await request.form()
            params: dict[str, Any] = dict(form)
        else:
            params = dict(request.query_params)

        if params.get("error"):
            raise AuthenticationException(
                code=ErrorCode.AUTH_OAUTH_EXCHANGE_FAILED,
                message=(
                    f"Identity provider returned an error: {params.get('error')} "
                    f"({params.get('error_description', '')})"
                ),
            )

        state = params.get("state")
        code = params.get("code")
        if not state or not code:
            raise AuthenticationException(
                code=ErrorCode.AUTH_OAUTH_STATE_INVALID,
                message="Missing state or code in OAuth callback",
            )

        state_claims = decode_state_token(str(state), provider_name)
        nonce = state_claims.get("nonce")
        redirect_uri = state_claims.get("redirect_uri") or _resolve_redirect_uri(
            request, provider_obj, provider_name
        )

        code_verifier = None
        if provider_obj.use_pkce:
            code_verifier = request.cookies.get(pkce_cookie_name(provider_name))

        token_response = await _complete_login(
            provider_obj,
            provider_name,
            code=str(code),
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            nonce=nonce,
        )

        target = _resolve_success_redirect(state_claims.get("next"))
        if target:
            fragment = urlencode(
                {
                    "access_token": token_response.access_token,
                    "refresh_token": token_response.refresh_token,
                    "token_type": token_response.token_type,
                    "expires_in": token_response.expires_in,
                }
            )
            # Tokens travel in the URL FRAGMENT: fragments are not sent to
            # servers and do not end up in logs or Referer headers.
            response: Response = RedirectResponse(f"{target}#{fragment}", status_code=303)
        else:
            response = JSONResponse(token_response.model_dump())

        response.delete_cookie(pkce_cookie_name(provider_name), path="/")
        return response

    @router.post(
        "/{provider}/token/",
        response_model=TokenResponse,
        responses={
            401: {"model": ErrorResponse, "description": "OAuth login failed"},
            404: {"model": ErrorResponse, "description": "Unknown provider"},
        },
        summary="Exchange OAuth Code (SPA)",
        description=(
            "SPA flow: the frontend handles the IdP redirect itself and posts "
            "the authorization code (plus PKCE verifier) here. Always returns "
            "a JSON TokenResponse with locally-issued tokens."
        ),
    )
    async def oauth_token(provider: str, body: TokenExchangeRequest) -> TokenResponse:
        provider_name = provider
        provider_obj = _get_provider(provider_name)

        nonce = None
        if body.state:
            state_claims = decode_state_token(body.state, provider_name)
            nonce = state_claims.get("nonce")

        return await _complete_login(
            provider_obj,
            provider_name,
            code=body.code,
            redirect_uri=body.redirect_uri,
            code_verifier=body.code_verifier,
            nonce=nonce,
        )

    # Serve both slash variants directly (no 307 redirect). Canonical OAuth
    # paths keep their trailing slash: url_for("oauth_callback") feeds the
    # redirect_uri that IdP app registrations match exactly, so the canonical
    # form must not change.
    from zeeb_api.routers.default import add_slash_alias_routes

    add_slash_alias_routes(router)

    return router
