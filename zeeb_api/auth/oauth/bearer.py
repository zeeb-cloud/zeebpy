"""
External bearer token validation (SPA mode).

Lets ``JWTAuthMiddleware`` accept JWTs issued directly by an external IdP
(e.g. an Azure AD ID/access token acquired by MSAL in a SPA) in addition to
locally-issued tokens. Validators NEVER raise: any failure yields None and
the request proceeds unauthenticated.
"""

from __future__ import annotations

import logging
from typing import Any

from zeeb_api.auth.oauth.provider import ExternalClaims, OAuthProvider

logger = logging.getLogger(__name__)


class ExternalAuthenticatedUser:
    """
    Lightweight user object for externally-validated tokens without a local
    database user (mirrors ``zeeb_api.auth.middleware.AuthenticatedUser``).
    """

    def __init__(self, claims: ExternalClaims, provider: str) -> None:
        self.id = claims.subject
        self.email = claims.email
        self.provider = provider
        self.external_claims = claims
        self.claims = claims.raw
        self.is_authenticated = True

    @property
    def is_staff(self) -> bool:
        return bool(self.claims.get("is_staff", False))

    @property
    def is_superuser(self) -> bool:
        return bool(self.claims.get("is_superuser", False))

    def __repr__(self) -> str:
        return f"<ExternalAuthenticatedUser provider={self.provider!r} id={self.id}>"


class ExternalTokenValidator:
    """
    Validate externally-issued bearer tokens against a provider's JWKS.

    Usage:
        app.add_middleware(
            JWTAuthMiddleware,
            external_validators=[ExternalTokenValidator(azure_provider)],
        )

    Or via settings (no code):
        OAUTH_ACCEPT_EXTERNAL_TOKENS = ["azure"]

    Args:
        provider: The OAuth provider whose JWKS validates the tokens.
        create_users: Auto-provision a local user + ExternalIdentity on first
            sight of a valid token (default False: fall back to a lightweight
            ``ExternalAuthenticatedUser`` when no identity exists).
    """

    def __init__(self, provider: OAuthProvider, *, create_users: bool = False) -> None:
        self.provider = provider
        self.create_users = create_users

    async def __call__(self, token: str) -> Any | None:
        """Return a user object for ``token`` or None on any failure."""
        try:
            claims = await self.provider.validate_external_token(token)
        except Exception:
            return None

        try:
            # Lazy import: pulls in the ExternalIdentity table registration.
            from zeeb_api.auth.oauth.models import (
                ExternalIdentity,
                get_or_create_user_for_identity,
            )

            identity = await ExternalIdentity.objects.filter(
                provider=self.provider.name, subject=claims.subject
            ).first()
            if identity is not None:
                from zeeb_api.auth.backends import get_user_model
                return await get_user_model().objects.get(id=identity.user_id)

            if self.create_users:
                user, _identity, _created = await get_or_create_user_for_identity(
                    self.provider.name,
                    claims,
                    auto_create=True,
                    link_by_email=self._resolved_link_by_email(),
                )
                return user
        except Exception as e:
            logger.debug(
                "External identity lookup failed for provider %s: %s",
                self.provider.name,
                e,
            )

        return ExternalAuthenticatedUser(claims, self.provider.name)

    def _resolved_link_by_email(self) -> bool:
        if self.provider.link_by_email is not None:
            return self.provider.link_by_email
        try:
            from zeeb_api.conf import settings
            return bool(getattr(settings, "OAUTH_LINK_BY_EMAIL", True))
        except Exception:
            return True


def build_validators_from_settings() -> list[ExternalTokenValidator]:
    """
    Build validators from ``settings.OAUTH_ACCEPT_EXTERNAL_TOKENS``
    (list of provider names from the OAUTH_PROVIDERS registry).
    """
    from zeeb_api.auth.oauth.registry import get_oauth_provider

    try:
        from zeeb_api.conf import settings
        names = list(getattr(settings, "OAUTH_ACCEPT_EXTERNAL_TOKENS", []) or [])
    except Exception:
        return []

    validators: list[ExternalTokenValidator] = []
    for name in names:
        try:
            validators.append(ExternalTokenValidator(get_oauth_provider(name)))
        except Exception as e:
            logger.warning(
                "OAUTH_ACCEPT_EXTERNAL_TOKENS references unknown or invalid "
                "provider %r: %s",
                name,
                e,
            )
    return validators
