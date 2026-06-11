"""
Generic OAuth2/OIDC provider.

A provider is configured either from an OIDC discovery URL
(``server_metadata_url``) or from manually supplied endpoints, and exposes
the full authorization-code flow: authorization URL building, code exchange,
token refresh, claims extraction (validated ID token preferred, userinfo as
fallback) and external bearer token validation.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from zeeb_api.auth.oauth.client import (
    OAuth2Client,
    OAuthTokenSet,
    OAuthValidationError,
    OIDCMetadata,
)
from zeeb_api.auth.oauth.jwks import JWKSCache, validate_id_token

DEFAULT_SCOPES = ["openid", "email", "profile"]
DEFAULT_CLAIM_MAPPING = {
    "subject": "sub",
    "email": "email",
    "name": "name",
}


class ExternalClaims(BaseModel):
    """Normalized identity claims extracted from an external IdP."""

    subject: str
    email: str | None = None
    email_verified: bool | None = None
    name: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _dotted_lookup(data: dict[str, Any], path: str) -> Any:
    """Resolve a dotted path (``"a.b.c"``) inside a nested dict."""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


class OAuthProvider:
    """
    Generic OAuth2/OIDC provider.

    Args:
        client_id: OAuth client id (required).
        client_secret: OAuth client secret (None for public/PKCE-only clients).
        name: Provider name used in URLs, cookies and ExternalIdentity rows.
        server_metadata_url: OIDC discovery document URL.
        authorization_endpoint/token_endpoint/userinfo_endpoint/jwks_uri/issuer:
            Manual endpoint configuration; overrides discovered values.
        scopes: OAuth scopes (default ``openid email profile``).
        redirect_uri: Fixed redirect URI (default: derived per request).
        claim_mapping: Maps ExternalClaims fields to raw claim names; values
            support dotted-path lookups into nested claims.
        use_pkce: Use PKCE (S256) in the authorization-code flow.
        validate_id_token: Validate the ID token signature/claims via JWKS.
        auto_create_user: Auto-provision local users (None = use settings).
        link_by_email: Link identities to existing users by email
            (None = use settings).
        extra_authorize_params: Extra query params for the authorization URL.
        http_client: Optional ``httpx.AsyncClient`` (test seam).
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str | None = None,
        name: str | None = None,
        server_metadata_url: str | None = None,
        authorization_endpoint: str | None = None,
        token_endpoint: str | None = None,
        userinfo_endpoint: str | None = None,
        jwks_uri: str | None = None,
        issuer: str | None = None,
        scopes: list[str] | None = None,
        redirect_uri: str | None = None,
        claim_mapping: dict[str, str] | None = None,
        use_pkce: bool = True,
        validate_id_token: bool = True,
        auto_create_user: bool | None = None,
        link_by_email: bool | None = None,
        extra_authorize_params: dict[str, str] | None = None,
        http_client: Any | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.name = name or "oauth"
        self.server_metadata_url = server_metadata_url
        self.authorization_endpoint = authorization_endpoint
        self.token_endpoint = token_endpoint
        self.userinfo_endpoint = userinfo_endpoint
        self.jwks_uri = jwks_uri
        self.issuer = issuer
        self.scopes = list(scopes) if scopes is not None else list(DEFAULT_SCOPES)
        self.redirect_uri = redirect_uri
        self.claim_mapping = {**DEFAULT_CLAIM_MAPPING, **(claim_mapping or {})}
        self.use_pkce = use_pkce
        self.validate_id_token = validate_id_token
        self.auto_create_user = auto_create_user
        self.link_by_email = link_by_email
        self.extra_authorize_params = dict(extra_authorize_params or {})

        self.client = OAuth2Client(http_client=http_client)
        self._metadata: OIDCMetadata | None = None
        self._jwks: JWKSCache | None = None

    # Metadata / endpoints

    async def get_metadata(self) -> OIDCMetadata:
        """Resolve provider metadata (cached). Manual endpoints win."""
        if self._metadata is not None:
            return self._metadata

        if self.server_metadata_url:
            metadata = await self.client.fetch_metadata(self.server_metadata_url)
        else:
            if not (self.authorization_endpoint and self.token_endpoint):
                raise OAuthValidationError(
                    f"Provider {self.name!r} needs either server_metadata_url or "
                    "manual authorization_endpoint + token_endpoint configuration"
                )
            metadata = OIDCMetadata(
                issuer=self.issuer or "",
                authorization_endpoint=self.authorization_endpoint,
                token_endpoint=self.token_endpoint,
                userinfo_endpoint=self.userinfo_endpoint,
                jwks_uri=self.jwks_uri,
            )

        # Manual endpoint configuration overrides discovery.
        overrides = {
            "authorization_endpoint": self.authorization_endpoint,
            "token_endpoint": self.token_endpoint,
            "userinfo_endpoint": self.userinfo_endpoint,
            "jwks_uri": self.jwks_uri,
            "issuer": self.issuer,
        }
        for key, value in overrides.items():
            if value:
                setattr(metadata, key, value)

        self._metadata = metadata
        return metadata

    async def _get_jwks(self) -> JWKSCache:
        if self._jwks is None:
            metadata = await self.get_metadata()
            if not metadata.jwks_uri:
                raise OAuthValidationError(
                    f"Provider {self.name!r} has no jwks_uri; cannot validate tokens"
                )
            self._jwks = JWKSCache(self.client, metadata.jwks_uri)
        return self._jwks

    # Flow steps

    async def build_authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str | None = None,
    ) -> str:
        """Build the IdP authorization URL for the code flow."""
        metadata = await self.get_metadata()
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.scopes),
            "state": state,
            "nonce": nonce,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        params.update(self.extra_authorize_params)
        return f"{metadata.authorization_endpoint}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> OAuthTokenSet:
        """Exchange an authorization code for tokens."""
        metadata = await self.get_metadata()
        return await self.client.exchange_code(
            metadata.token_endpoint,
            code=code,
            redirect_uri=redirect_uri,
            client_id=self.client_id,
            client_secret=self.client_secret,
            code_verifier=code_verifier,
        )

    async def refresh_tokens(self, refresh_token: str) -> OAuthTokenSet:
        """Refresh tokens at the IdP."""
        metadata = await self.get_metadata()
        return await self.client.refresh_token(
            metadata.token_endpoint,
            refresh_token=refresh_token,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

    # Claims

    def _issuer_validator(self, iss: str) -> bool:
        """
        Validate the ``iss`` claim. Override for non-trivial schemes
        (e.g. Azure AD multi-tenant).
        """
        expected = self.issuer
        if not expected and self._metadata is not None:
            expected = self._metadata.issuer
        if not expected:
            return True
        return iss == expected

    def map_claims(self, raw: dict[str, Any]) -> ExternalClaims:
        """Map raw IdP claims to :class:`ExternalClaims` via ``claim_mapping``."""
        subject = _dotted_lookup(raw, self.claim_mapping["subject"])
        if subject is None:
            raise OAuthValidationError(
                f"Provider {self.name!r}: claim "
                f"{self.claim_mapping['subject']!r} (subject) missing from token"
            )
        email = _dotted_lookup(raw, self.claim_mapping.get("email", "email"))
        name_ = _dotted_lookup(raw, self.claim_mapping.get("name", "name"))
        email_verified = raw.get("email_verified")
        return ExternalClaims(
            subject=str(subject),
            email=str(email) if email else None,
            email_verified=bool(email_verified) if email_verified is not None else None,
            name=str(name_) if name_ else None,
            raw=raw,
        )

    async def _validate_id_token_claims(
        self, id_token: str, nonce: str | None = None
    ) -> dict[str, Any]:
        metadata = await self.get_metadata()
        jwks = await self._get_jwks()
        return await validate_id_token(
            id_token,
            jwks=jwks,
            client_id=self.client_id,
            issuer_validator=self._issuer_validator,
            nonce=nonce,
            allowed_algs=metadata.id_token_signing_alg_values_supported,
        )

    async def get_claims(
        self, tokens: OAuthTokenSet, nonce: str | None = None
    ) -> ExternalClaims:
        """
        Extract identity claims from a token set.

        Prefers validated ID token claims; merges userinfo as fallback for
        claims the ID token does not carry.
        """
        raw: dict[str, Any] = {}

        if self.validate_id_token and tokens.id_token:
            raw = await self._validate_id_token_claims(tokens.id_token, nonce=nonce)

        needs_userinfo = not raw or _dotted_lookup(
            raw, self.claim_mapping.get("email", "email")
        ) is None
        metadata = await self.get_metadata()
        if needs_userinfo and metadata.userinfo_endpoint and tokens.access_token:
            userinfo = await self.client.fetch_userinfo(
                metadata.userinfo_endpoint, tokens.access_token
            )
            # ID token claims win over userinfo.
            raw = {**userinfo, **raw}

        if not raw:
            raise OAuthValidationError(
                f"Provider {self.name!r} returned neither a usable id_token nor userinfo"
            )
        return self.map_claims(raw)

    async def validate_external_token(self, token: str) -> ExternalClaims:
        """
        Validate an externally-issued JWT (SPA bearer mode) against this
        provider's JWKS and return the mapped claims.
        """
        metadata = await self.get_metadata()
        jwks = await self._get_jwks()
        claims = await validate_id_token(
            token,
            jwks=jwks,
            client_id=self.client_id,
            issuer_validator=self._issuer_validator,
            allowed_algs=metadata.id_token_signing_alg_values_supported,
        )
        return self.map_claims(claims)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} client_id={self.client_id!r}>"
