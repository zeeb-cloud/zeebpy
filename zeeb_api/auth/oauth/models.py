"""
ExternalIdentity model linking local users to external IdP identities.

IMPORTANT: Importing this module registers the ``auth_external_identities``
table with zeeb_orm's model registry/metadata, which makes it visible to
migration autodetection. The OAuth package therefore exposes
``ExternalIdentity`` lazily (PEP 562 ``__getattr__``) so that projects not
using OAuth never see this table. If you use OAuth, run ``makemigrations`` /
``migrate`` after importing the OAuth layer to create the table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from zeeb_api.exceptions import AuthenticationException, ErrorCode
from zeeb_orm import Model, fields

if TYPE_CHECKING:
    from zeeb_api.auth.oauth.provider import ExternalClaims


def _get_user_model() -> type:
    """Lazy resolver for the configured user model (callable FK target).

    Same pattern as ``zeeb_api.auth.models.UserPermission.user`` so the FK
    always points at whatever ``AUTH_USER_MODEL`` resolves to.
    """
    from zeeb_api.auth.backends import get_user_model
    return get_user_model()


class ExternalIdentity(Model):
    """
    Link between a local user and an identity at an external IdP.

    Uniqueness is per ``(provider, subject)``: one row per external account.
    A user may have many identities (Azure AD + Google + ...).
    """

    user = fields.ForeignKey(_get_user_model, related_name="external_identities")
    provider = fields.CharField(max_length=64)
    subject = fields.CharField(max_length=255)
    email = fields.EmailField(null=True)
    extra_data = fields.JSONField(null=True)
    created_at = fields.DateTimeField(null=True)
    last_login_at = fields.DateTimeField(null=True)

    class Meta:
        table_name = "auth_external_identities"
        unique_together = [("provider", "subject")]

    def __repr__(self) -> str:
        return (
            f"<ExternalIdentity provider={self.provider!r} subject={self.subject!r} "
            f"user_id={self.user_id}>"
        )


def _claim_is_true(value: Any) -> bool:
    """A recorded ``email_verified`` claim that asserts verification."""
    from zeeb_api.auth.oauth.provider import parse_email_verified

    return parse_email_verified(value) is True


async def local_email_is_verified(user: Any, email: str) -> bool:
    """Whether ``user`` is known to own ``email`` — the gate for email linking.

    A local account made through ``/register`` never proves it owns its
    address, so linking an IdP login to it by email would hand the account to
    whoever registered the address first (pre-registration hijack). Ownership
    counts as established when:

    - the user model carries an ``email_verified`` attribute (a column your
      own verification flow sets) and it is ``True``; or
    - the user already has an ``ExternalIdentity`` for this address whose
      provider asserted ``email_verified`` when it was linked.

    Anything else is unverifiable, and linking is refused.
    """
    flag = getattr(user, "email_verified", None)
    if isinstance(flag, bool):
        return flag
    identities = await ExternalIdentity.objects.filter(user_id=user.id).all()
    for identity in identities:
        if (identity.email or "").lower() != email.lower():
            continue
        if _claim_is_true((identity.extra_data or {}).get("email_verified")):
            return True
    return False


def _refuse_existing_account(provider: str) -> AuthenticationException:
    return AuthenticationException(
        code=ErrorCode.AUTH_OAUTH_ACCOUNT_EXISTS,
        message=(
            f"A local account already uses this {provider} identity's email "
            "address and cannot be linked to it automatically; sign in to "
            "that account first"
        ),
    )


async def get_or_create_user_for_identity(
    provider: str,
    claims: ExternalClaims,
    *,
    auto_create: bool,
    link_by_email: bool,
    require_verified_email: bool = True,
) -> tuple[Any, ExternalIdentity, bool]:
    """
    Resolve (or provision) the local user for an external identity.

    Resolution order:
        1. Existing ``ExternalIdentity(provider, subject)`` -> its user
           (updates ``last_login_at`` / ``email`` / ``extra_data``). A
           deactivated user is refused (``AUTH_ACCOUNT_DISABLED``).
        2. ``link_by_email`` and the claims carry a **verified** email ->
           existing user with that email gets a new identity attached, but
           only when that local account is known to own the address
           (:func:`local_email_is_verified`); otherwise
           ``AUTH_OAUTH_ACCOUNT_EXISTS``.

           SECURITY: email linking means whoever controls that email at the
           IdP gains access to the matching local account (account takeover
           if the IdP does not verify email ownership), and whoever created
           the local account gains the IdP login (pre-registration hijack if
           the local account never proved it owns the address). When
           ``require_verified_email`` is set (the default), the IdP must
           assert ``email_verified`` is true before an email is used to link
           to an existing account or to auto-provision a new one. Only disable
           it for providers you fully trust to have verified the address.
        3. ``auto_create`` -> create a new user (no usable password) with
           ``date_joined`` set. A local account already holding the email is
           never shadowed: without a permitted link that is
           ``AUTH_OAUTH_ACCOUNT_EXISTS``.
        4. Otherwise raise
           ``AuthenticationException(AUTH_OAUTH_USER_NOT_PROVISIONED)``.

    Returns:
        Tuple of (user, identity, created) where ``created`` is True when a
        new identity row was created (cases 2 and 3).
    """
    from zeeb_api.auth.backends import get_user_model

    user_model = get_user_model()
    now = datetime.now(timezone.utc)

    identity = await ExternalIdentity.objects.filter(
        provider=provider, subject=claims.subject
    ).first()

    if identity is not None:
        user = await user_model.objects.filter(id=identity.user_id).first()
        if user is None or not getattr(user, "is_active", True):
            raise AuthenticationException(
                code=ErrorCode.AUTH_ACCOUNT_DISABLED,
                message="The account linked to this identity is disabled",
            )
        identity.last_login_at = now
        if claims.email:
            identity.email = claims.email
        if claims.raw:
            identity.extra_data = claims.raw
        await identity.save()
        return user, identity, False

    # An email may only be trusted to identify/provision an account when the
    # IdP has verified ownership. ``email_verified is None`` (claim absent) is
    # treated as unverified: fail safe. A returning identity (handled above)
    # is exempt because ownership was already established at first link.
    email_is_trusted = (not require_verified_email) or (claims.email_verified is True)
    if claims.email and not email_is_trusted:
        raise AuthenticationException(
            code=ErrorCode.AUTH_OAUTH_EMAIL_UNVERIFIED,
            message=(
                f"The {provider} identity's email address is not verified; "
                "refusing to link or provision a local account from an "
                "unverified email"
            ),
        )

    user = None
    existing = None
    if claims.email:
        existing = await user_model.objects.filter(email=claims.email).first()
    if existing is not None:
        if not (link_by_email and await local_email_is_verified(existing, claims.email)):
            raise _refuse_existing_account(provider)
        if not getattr(existing, "is_active", True):
            raise AuthenticationException(
                code=ErrorCode.AUTH_ACCOUNT_DISABLED,
                message="The account with this email address is disabled",
            )
        user = existing

    if user is None:
        if not auto_create:
            raise AuthenticationException(
                code=ErrorCode.AUTH_OAUTH_USER_NOT_PROVISIONED,
                message=(
                    f"No local account is linked to this {provider} identity "
                    "and automatic provisioning is disabled"
                ),
            )
        if not claims.email:
            raise AuthenticationException(
                code=ErrorCode.AUTH_OAUTH_USER_NOT_PROVISIONED,
                message=(
                    f"The {provider} identity does not include an email address; "
                    "cannot auto-create a local account"
                ),
            )
        extra: dict[str, Any] = {}
        if claims.name:
            first, _, last = claims.name.partition(" ")
            if hasattr(user_model, "first_name"):
                extra["first_name"] = first or None
            if hasattr(user_model, "last_name"):
                extra["last_name"] = last or None
        user = user_model(email=claims.email, **extra)
        # OAuth-provisioned users have no usable local password.
        if hasattr(user, "password"):
            user.password = None
        if hasattr(user, "date_joined"):
            user.date_joined = now
        if hasattr(user, "last_login"):
            user.last_login = now
        await user.save()

    identity = ExternalIdentity(
        user_id=user.id,
        provider=provider,
        subject=claims.subject,
        email=claims.email,
        extra_data=claims.raw or None,
        created_at=now,
        last_login_at=now,
    )
    await identity.save()
    return user, identity, True
