"""
Stateless OAuth state tokens, PKCE helpers and nonce generation.

The state parameter is a short-lived HMAC-signed JWT (type ``oauth_state``)
carrying the nonce, redirect URI and optional post-login URL. No server-side
session storage is needed.

The PKCE code verifier is NEVER embedded in the state JWT (the state travels
through the IdP); it is stored in an HttpOnly, SameSite=Lax, Secure cookie
named ``zeeb_oauth_pkce_{provider}`` set by the authorize endpoint and
read + deleted by the callback endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt as pyjwt

from zeeb_api.auth.jwt import (
    TokenError,
    _ensure_secure_secret,
    decode_token,
    get_jwt_config,
)
from zeeb_api.exceptions import AuthenticationException, ErrorCode

STATE_TOKEN_TYPE = "oauth_state"
PKCE_COOKIE_TEMPLATE = "zeeb_oauth_pkce_{provider}"


def pkce_cookie_name(provider: str) -> str:
    """Cookie name carrying the PKCE verifier for ``provider``."""
    return PKCE_COOKIE_TEMPLATE.format(provider=provider)


def generate_nonce() -> str:
    """Random URL-safe nonce for OIDC replay protection."""
    return secrets.token_urlsafe(32)


def generate_pkce_pair() -> tuple[str, str]:
    """
    Generate an RFC 7636 (S256) PKCE pair.

    Returns:
        Tuple of (code_verifier, code_challenge).
    """
    code_verifier = secrets.token_urlsafe(64)  # 86 chars, within 43..128
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _state_ttl_seconds() -> int:
    try:
        from zeeb_api.conf import settings
        return int(getattr(settings, "OAUTH_STATE_TTL_SECONDS", 600))
    except Exception:
        return 600


def create_state_token(
    provider: str,
    *,
    nonce: str,
    redirect_uri: str,
    next_url: str | None = None,
) -> str:
    """
    Create a signed, stateless OAuth state token.

    The token is an HS256 JWT signed with the application JWT secret,
    satisfying ``decode_token``'s required-claims contract
    (sub/type/exp/iat/jti).
    """
    config = get_jwt_config()
    _ensure_secure_secret(config)
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": provider,
        "type": STATE_TOKEN_TYPE,
        "exp": now + timedelta(seconds=_state_ttl_seconds()),
        "iat": now,
        "jti": str(uuid.uuid4()),
        "claims": {
            "nonce": nonce,
            "redirect_uri": redirect_uri,
            "next": next_url,
        },
    }
    return pyjwt.encode(payload, config.secret_key, algorithm=config.algorithm)


def decode_state_token(token: str, provider: str) -> dict[str, Any]:
    """
    Decode and verify an OAuth state token for ``provider``.

    Returns:
        The state claims dict: ``{"nonce", "redirect_uri", "next"}``.

    Raises:
        AuthenticationException(AUTH_OAUTH_STATE_INVALID): On any failure
            (expired, tampered, wrong provider, wrong type).
    """
    try:
        payload = decode_token(token, token_type=STATE_TOKEN_TYPE)
    except TokenError as e:
        raise AuthenticationException(
            code=ErrorCode.AUTH_OAUTH_STATE_INVALID,
            message=f"Invalid OAuth state: {e.message}",
        ) from e

    if payload.sub != provider:
        raise AuthenticationException(
            code=ErrorCode.AUTH_OAUTH_STATE_INVALID,
            message="OAuth state was issued for a different provider",
        )

    return dict(payload.claims)
