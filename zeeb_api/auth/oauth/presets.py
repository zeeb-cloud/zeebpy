"""
Provider presets: Azure AD (Microsoft Entra ID), Google, GitHub.
"""

from __future__ import annotations

import re
from typing import Any

from zeeb_api.auth.oauth.client import OAuthError
from zeeb_api.auth.oauth.provider import ExternalClaims, OAuthProvider

AZURE_AUTHORITY_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
)
# Issuer for multi-tenant apps: a concrete tenant GUID is substituted into
# the {tenantid} placeholder, so any GUID tenant issuer is acceptable.
AZURE_MULTI_TENANT_ISSUER_RE = re.compile(
    r"^https://login\.microsoftonline\.com/[0-9a-f-]{36}/v2\.0$"
)
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class AzureADProvider(OAuthProvider):
    """
    Azure AD / Microsoft Entra ID (v2.0 endpoints).

    Args:
        tenant: Tenant GUID, verified domain, or one of the pseudo-tenants
            ``common`` / ``organizations`` / ``consumers``.
        client_id: Application (client) ID from the app registration.
        client_secret: Client secret (None for public clients using PKCE).

    Issuer validation:
        - Concrete tenant GUID: exact issuer match
          (``https://login.microsoftonline.com/{tenant}/v2.0``).
        - common/organizations/consumers: any GUID-tenant issuer on
          ``login.microsoftonline.com`` is accepted (multi-tenant apps).

    Claim mapping:
        - subject: ``oid`` (stable per user across apps in a tenant) when
          present, else ``sub``.
        - email: ``email``, falling back to ``preferred_username``.
    """

    def __init__(
        self,
        tenant: str = "common",
        *,
        client_id: str,
        client_secret: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.tenant = tenant
        kwargs.setdefault("name", "azure")
        kwargs.setdefault(
            "server_metadata_url", AZURE_AUTHORITY_TEMPLATE.format(tenant=tenant)
        )
        kwargs.setdefault("scopes", ["openid", "profile", "email", "offline_access"])
        super().__init__(client_id=client_id, client_secret=client_secret, **kwargs)

    def _issuer_validator(self, iss: str) -> bool:
        if self.issuer:  # explicit override wins
            return iss == self.issuer
        if _GUID_RE.match(self.tenant):
            return iss == f"https://login.microsoftonline.com/{self.tenant}/v2.0"
        # common / organizations / consumers (or a verified domain): the
        # issuer carries the concrete tenant GUID.
        return AZURE_MULTI_TENANT_ISSUER_RE.match(iss) is not None

    def map_claims(self, raw: dict[str, Any]) -> ExternalClaims:
        subject = raw.get("oid") or raw.get("sub")
        if subject is None:
            return super().map_claims(raw)
        email = raw.get("email") or raw.get("preferred_username")
        email_verified = raw.get("email_verified")
        return ExternalClaims(
            subject=str(subject),
            email=str(email) if email else None,
            email_verified=bool(email_verified) if email_verified is not None else None,
            name=raw.get("name"),
            raw=raw,
        )


class GoogleProvider(OAuthProvider):
    """Google Sign-In (pure OIDC via discovery)."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("name", "google")
        kwargs.setdefault(
            "server_metadata_url",
            "https://accounts.google.com/.well-known/openid-configuration",
        )
        super().__init__(client_id=client_id, client_secret=client_secret, **kwargs)


class GitHubProvider(OAuthProvider):
    """
    GitHub OAuth (plain OAuth2; GitHub does not implement OIDC).

    Uses manual endpoints, disables ID token validation and resolves the
    user via the REST API.

    ``/user`` carries no verification flag, so ``/user/emails`` is always
    queried (requires the ``user:email`` scope, requested by default) — both
    to fill in a private primary address and to learn whether the address is
    verified. Without that call ``email_verified`` stays ``None``, which
    ``OAUTH_REQUIRE_VERIFIED_EMAIL`` treats as unverified.
    """

    EMAILS_ENDPOINT = "https://api.github.com/user/emails"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("name", "github")
        kwargs.setdefault(
            "authorization_endpoint", "https://github.com/login/oauth/authorize"
        )
        kwargs.setdefault("token_endpoint", "https://github.com/login/oauth/access_token")
        kwargs.setdefault("userinfo_endpoint", "https://api.github.com/user")
        kwargs.setdefault("scopes", ["read:user", "user:email"])
        kwargs.setdefault("validate_id_token", False)
        super().__init__(client_id=client_id, client_secret=client_secret, **kwargs)

    def map_claims(self, raw: dict[str, Any]) -> ExternalClaims:
        email_verified = raw.get("email_verified")
        return ExternalClaims(
            subject=str(raw["id"]),
            email=raw.get("email"),
            email_verified=bool(email_verified) if email_verified is not None else None,
            name=raw.get("name") or raw.get("login"),
            raw=raw,
        )

    async def get_claims(self, tokens: Any, nonce: str | None = None) -> ExternalClaims:
        metadata = await self.get_metadata()
        raw = await self.client.fetch_userinfo(
            metadata.userinfo_endpoint, tokens.access_token
        )
        self._apply_email_verification(raw, await self._fetch_emails(tokens))
        return self.map_claims(raw)

    async def _fetch_emails(self, tokens: Any) -> list[dict[str, Any]] | None:
        """The ``/user/emails`` payload, or None when it is unavailable.

        A token without the ``user:email`` scope makes GitHub reject this
        call; that must not turn an otherwise working login into a 500, so
        the failure degrades to "verification unknown".
        """
        try:
            emails = await self.client.fetch_userinfo(
                self.EMAILS_ENDPOINT, tokens.access_token
            )
        except OAuthError:
            return None
        return emails if isinstance(emails, list) else None

    @staticmethod
    def _apply_email_verification(
        raw: dict[str, Any], emails: list[dict[str, Any]] | None
    ) -> None:
        """Fill ``raw["email"]``/``raw["email_verified"]`` from ``/user/emails``.

        ``/user`` only exposes the public primary address and never says
        whether it is verified, so the flag can only come from here.
        """
        if emails is None:
            return

        address = raw.get("email")
        if not address:
            # Private primary address: adopt the best verified entry.
            entry = next(
                (e for e in emails if e.get("primary") and e.get("verified")), None
            ) or next((e for e in emails if e.get("verified")), None)
            if entry:
                raw["email"] = entry.get("email")
                raw["email_verified"] = True
            return

        entry = next(
            (
                e
                for e in emails
                if isinstance(e.get("email"), str)
                and e["email"].lower() == str(address).lower()
            ),
            None,
        )
        if entry is not None:
            raw["email_verified"] = bool(entry.get("verified"))
