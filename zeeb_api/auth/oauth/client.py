"""
Async OAuth2/OIDC HTTP client.

All HTTP traffic for the OAuth layer goes through one injectable
``httpx.AsyncClient`` (the test seam). httpx is imported lazily so that
plain ``import zeeb_api`` works without the "oauth" extra installed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _httpx() -> Any:
    """Import httpx lazily with a helpful error message."""
    try:
        import httpx
    except ImportError as e:  # pragma: no cover - depends on environment
        raise ImportError(
            "httpx is required for OAuth2/OIDC support. "
            "Install it with: pip install zeebpy[oauth]"
        ) from e
    return httpx


class OAuthError(Exception):
    """Base error for the OAuth client layer.

    These module-local errors are translated to
    :class:`zeeb_api.exceptions.AuthenticationException` at the router boundary.
    """


class OAuthExchangeError(OAuthError):
    """Token endpoint (or other IdP HTTP call) failed."""


class OAuthValidationError(OAuthError):
    """ID token / external token validation failed."""


class OIDCMetadata(BaseModel):
    """OpenID Connect discovery document (subset; extra keys are kept)."""

    model_config = ConfigDict(extra="allow")

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str | None = None
    jwks_uri: str | None = None
    id_token_signing_alg_values_supported: list[str] = Field(
        default_factory=lambda: ["RS256"]
    )


class OAuthTokenSet(BaseModel):
    """Tokens returned by the IdP token endpoint."""

    model_config = ConfigDict(extra="allow")

    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None
    id_token: str | None = None
    scope: str | None = None


class OAuth2Client:
    """
    Thin async HTTP client for OAuth2/OIDC flows.

    Args:
        http_client: Optional ``httpx.AsyncClient`` (injectable for tests).
            When None, a client is created lazily on first use.
        timeout: Default timeout for the lazily created client.
    """

    def __init__(self, http_client: Any | None = None, timeout: float = 10.0) -> None:
        self._http_client = http_client
        self._timeout = timeout
        self._owns_client = http_client is None

    @property
    def http(self) -> Any:
        """The underlying ``httpx.AsyncClient`` (created lazily)."""
        if self._http_client is None:
            httpx = _httpx()
            self._http_client = httpx.AsyncClient(timeout=self._timeout)
        return self._http_client

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this instance created it."""
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        try:
            response = await self.http.get(url, headers=headers)
        except Exception as e:
            raise OAuthExchangeError(f"Request to {url} failed: {e}") from e
        if response.status_code < 200 or response.status_code >= 300:
            raise OAuthExchangeError(
                f"Request to {url} returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            return response.json()
        except Exception as e:
            raise OAuthExchangeError(f"Non-JSON response from {url}") from e

    async def fetch_metadata(self, url: str) -> OIDCMetadata:
        """Fetch and parse an OIDC discovery document."""
        data = await self._get_json(url)
        return OIDCMetadata(**data)

    async def fetch_jwks(self, jwks_uri: str) -> dict[str, Any]:
        """Fetch the JSON Web Key Set."""
        data = await self._get_json(jwks_uri)
        if not isinstance(data, dict) or "keys" not in data:
            raise OAuthExchangeError(f"Invalid JWKS document from {jwks_uri}")
        return data

    async def fetch_userinfo(self, endpoint: str, access_token: str) -> dict[str, Any]:
        """Fetch the OIDC userinfo (or any bearer-authenticated JSON endpoint)."""
        data = await self._get_json(
            endpoint,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
        return data

    async def _post_token(self, token_endpoint: str, data: dict[str, str]) -> OAuthTokenSet:
        """POST a form-encoded request to the token endpoint."""
        try:
            response = await self.http.post(
                token_endpoint,
                data=data,
                headers={"Accept": "application/json"},
            )
        except Exception as e:
            raise OAuthExchangeError(f"Token request to {token_endpoint} failed: {e}") from e

        if response.status_code < 200 or response.status_code >= 300:
            raise OAuthExchangeError(
                f"Token endpoint returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        try:
            payload = response.json()
        except Exception as e:
            raise OAuthExchangeError("Token endpoint returned a non-JSON response") from e
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise OAuthExchangeError(
                f"Token endpoint response missing access_token: {payload!r}"
            )
        return OAuthTokenSet(**payload)

    async def exchange_code(
        self,
        token_endpoint: str,
        *,
        code: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str | None = None,
        code_verifier: str | None = None,
    ) -> OAuthTokenSet:
        """Exchange an authorization code for tokens (authorization_code grant)."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
        }
        if client_secret:
            data["client_secret"] = client_secret
        if code_verifier:
            data["code_verifier"] = code_verifier
        return await self._post_token(token_endpoint, data)

    async def refresh_token(
        self,
        token_endpoint: str,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: str | None = None,
        scope: str | None = None,
    ) -> OAuthTokenSet:
        """Refresh tokens using a refresh token (refresh_token grant)."""
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret:
            data["client_secret"] = client_secret
        if scope:
            data["scope"] = scope
        return await self._post_token(token_endpoint, data)
