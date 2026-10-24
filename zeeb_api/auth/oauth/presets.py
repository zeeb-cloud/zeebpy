"""
Provider presets: Azure AD (Microsoft Entra ID), Google, GitHub.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from zeeb_api.auth.oauth.client import OAuthError, OAuthValidationError
from zeeb_api.auth.oauth.provider import ExternalClaims, OAuthProvider, parse_email_verified
from zeeb_api.exceptions import ImproperlyConfigured

AZURE_AUTHORITY_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
)
# Issuer for multi-tenant apps: a concrete tenant GUID is substituted into
# the {tenantid} placeholder; the GUID is captured so it can be checked
# against the provider's tenant allow-list.
AZURE_MULTI_TENANT_ISSUER_RE = re.compile(
    r"^https://login\.microsoftonline\.com/([0-9a-f-]{36})/v2\.0$"
)
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
#: Pseudo-tenants whose sign-in endpoint accepts users from many tenants.
AZURE_MULTI_TENANT_PSEUDO_TENANTS = frozenset({"common", "organizations", "consumers"})

logger = logging.getLogger(__name__)


def azure_issuer_tenant(iss: Any) -> str | None:
    """The tenant GUID in an Azure AD v2.0 issuer, or None if it is not one."""
    if not isinstance(iss, str):
        return None
    match = AZURE_MULTI_TENANT_ISSUER_RE.match(iss)
    return match.group(1) if match else None


class AzureADProvider(OAuthProvider):
    """
    Azure AD / Microsoft Entra ID (v2.0 endpoints).

    Args:
        tenant: Tenant GUID, verified domain, or one of the pseudo-tenants
            ``common`` / ``organizations`` / ``consumers``.
        client_id: Application (client) ID from the app registration.
        client_secret: Client secret (None for public clients using PKCE).
        allowed_tenants: Tenant GUIDs (``tid``) whose tokens are accepted.
            Required for a multi-tenant provider unless ``allow_any_tenant``
            is set; when given it also narrows a single-tenant provider.
        allow_any_tenant: Accept a token from **any** Azure AD tenant on a
            multi-tenant provider. Tokens accepted this way never carry roles
            or staff flags for an unlinked user, even with
            ``trust_token_roles`` on: any tenant's admin can assign
            themselves your app's roles.

    Issuer validation:
        - ``issuer=`` override: exact match.
        - Concrete tenant GUID: exact issuer match
          (``https://login.microsoftonline.com/{tenant}/v2.0``).
        - Verified domain: exact match against the discovered issuer (it
          carries the domain's tenant GUID).
        - common/organizations/consumers: the issuer's tenant GUID must be in
          ``allowed_tenants``, or ``allow_any_tenant=True``. With neither,
          every token is refused (fail closed) with an error naming the two
          options.

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
        allowed_tenants: Iterable[str] | None = None,
        allow_any_tenant: bool = False,
        **kwargs: Any,
    ) -> None:
        self.tenant = tenant
        if isinstance(allowed_tenants, str):
            allowed_tenants = [allowed_tenants]
        self.allowed_tenants: frozenset[str] | None = None
        if allowed_tenants is not None:
            tenants = [str(t).strip() for t in allowed_tenants]
            bad = [t for t in tenants if not _GUID_RE.match(t)]
            if bad:
                raise ImproperlyConfigured(
                    f"AzureADProvider allowed_tenants must be tenant GUIDs (the "
                    f"'tid' claim), got {bad!r}"
                )
            self.allowed_tenants = frozenset(t.lower() for t in tenants)
        self.allow_any_tenant = bool(allow_any_tenant)
        kwargs.setdefault("name", "azure")
        kwargs.setdefault(
            "server_metadata_url", AZURE_AUTHORITY_TEMPLATE.format(tenant=tenant)
        )
        kwargs.setdefault("scopes", ["openid", "profile", "email", "offline_access"])
        super().__init__(client_id=client_id, client_secret=client_secret, **kwargs)

    @property
    def is_multi_tenant(self) -> bool:
        """Whether ``tenant`` is a pseudo-tenant (common/organizations/consumers)."""
        return self.tenant.lower() in AZURE_MULTI_TENANT_PSEUDO_TENANTS

    def _tenant_allowed(self, iss: str) -> bool:
        if self.allowed_tenants is None:
            return True
        tid = azure_issuer_tenant(iss)
        return tid is not None and tid.lower() in self.allowed_tenants

    def _issuer_validator(self, iss: str) -> bool:
        if self.issuer:  # explicit override wins
            return iss == self.issuer and self._tenant_allowed(iss)
        if _GUID_RE.match(self.tenant):
            expected = f"https://login.microsoftonline.com/{self.tenant.lower()}/v2.0"
            return iss == expected and self._tenant_allowed(iss)
        if not self.is_multi_tenant:
            # Verified domain: discovery resolves it to one concrete tenant.
            discovered = self._metadata.issuer if self._metadata is not None else ""
            if discovered and "{" not in discovered:
                return iss == discovered and self._tenant_allowed(iss)
        # common / organizations / consumers: the issuer carries the concrete
        # tenant GUID, which must be one this app accepts.
        if azure_issuer_tenant(iss) is None:
            return False
        if self.allowed_tenants is not None:
            return self._tenant_allowed(iss)
        if self.allow_any_tenant:
            return True
        raise OAuthValidationError(
            f"Azure AD provider {self.name!r} uses the multi-tenant endpoint "
            f"{self.tenant!r} but accepts no tenant: set allowed_tenants=[<tenant "
            "GUID>, ...] or, to accept every Azure AD tenant, allow_any_tenant=True"
        )

    def trusts_token_roles(self, claims: ExternalClaims) -> bool:
        """Never for a tenant outside ``allowed_tenants`` or an any-tenant provider."""
        if not super().trusts_token_roles(claims):
            return False
        iss = claims.raw.get("iss", "")
        if self.allowed_tenants is not None:
            return self._tenant_allowed(iss)
        if self.is_multi_tenant:
            logger.warning(
                "Azure AD provider %r accepts any tenant; ignoring roles and staff "
                "flags in its tokens despite trust_token_roles (set allowed_tenants)",
                self.name,
            )
            return False
        return True

    def map_claims(self, raw: dict[str, Any]) -> ExternalClaims:
        subject = raw.get("oid") or raw.get("sub")
        if subject is None:
            return super().map_claims(raw)
        email = raw.get("email") or raw.get("preferred_username")
        email_verified = raw.get("email_verified")
        return ExternalClaims(
            subject=str(subject),
            email=str(email) if email else None,
            email_verified=parse_email_verified(email_verified),
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
            email_verified=parse_email_verified(email_verified),
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
            raw["email_verified"] = parse_email_verified(entry.get("verified")) is True
