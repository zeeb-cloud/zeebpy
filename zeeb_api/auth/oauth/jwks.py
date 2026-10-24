"""
Async JWKS caching and OIDC ID token validation.

Deliberately does NOT use ``jwt.PyJWKClient`` (it performs blocking I/O);
key sets are fetched through the async :class:`~zeeb_api.auth.oauth.client.OAuth2Client`.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

import jwt

from zeeb_api.auth.oauth.client import OAuth2Client, OAuthValidationError

# Algorithms that are never acceptable for ID token signatures.
_FORBIDDEN_ALG_PREFIXES = ("HS",)
_FORBIDDEN_ALGS = ("none",)


class JWKSCache:
    """
    TTL cache for a provider's JSON Web Key Set.

    An unknown ``kid`` triggers one forced refetch (the standard behaviour for
    key rollover) before failing, but forced refetches are rate-limited: the
    ``kid`` is read from an *unverified* token header, so without a limit any
    anonymous client could make the server fetch the JWKS once per request.

    - At most one forced refetch per ``refetch_cooldown_seconds`` (default
      60). An unknown ``kid`` arriving inside the cooldown fails without a
      fetch; the first one after it refetches, so real key rotation is still
      picked up.
    - A ``kid`` that a fetch confirmed absent is remembered for
      ``unknown_kid_ttl_seconds`` (default 300) in a bounded negative cache
      (``max_unknown_kids`` entries, oldest evicted first) and fails without
      a fetch until it expires.
    - Concurrent fetches are coalesced into one (an ``asyncio.Lock``).

    ``clock`` is a test seam (defaults to :func:`time.monotonic`).
    """

    def __init__(
        self,
        client: OAuth2Client,
        jwks_uri: str,
        ttl_seconds: int = 3600,
        *,
        refetch_cooldown_seconds: float = 60.0,
        unknown_kid_ttl_seconds: float = 300.0,
        max_unknown_kids: int = 1024,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.client = client
        self.jwks_uri = jwks_uri
        self.ttl_seconds = ttl_seconds
        self.refetch_cooldown_seconds = refetch_cooldown_seconds
        self.unknown_kid_ttl_seconds = unknown_kid_ttl_seconds
        self.max_unknown_kids = max_unknown_kids
        self._clock = clock or time.monotonic
        self._key_set: jwt.PyJWKSet | None = None
        self._fetched_at: float = 0.0
        self._last_forced_at: float | None = None
        self._unknown_kids: OrderedDict[str, float] = OrderedDict()
        self._lock = asyncio.Lock()

    async def _refetch(self) -> None:
        data = await self.client.fetch_jwks(self.jwks_uri)
        try:
            self._key_set = jwt.PyJWKSet.from_dict(data)
        except Exception as e:
            raise OAuthValidationError(f"Invalid JWKS from {self.jwks_uri}: {e}") from e
        self._fetched_at = self._clock()

    def _find_key(self, kid: str) -> jwt.PyJWK | None:
        if self._key_set is None:
            return None
        for key in self._key_set.keys:
            if key.key_id == kid:
                return key
        return None

    def _is_stale(self) -> bool:
        return self._key_set is None or (
            self._clock() - self._fetched_at
        ) > self.ttl_seconds

    def _in_cooldown(self) -> bool:
        return self._last_forced_at is not None and (
            self._clock() - self._last_forced_at
        ) < self.refetch_cooldown_seconds

    def _known_unknown(self, kid: str) -> bool:
        seen_at = self._unknown_kids.get(kid)
        if seen_at is None:
            return False
        if (self._clock() - seen_at) < self.unknown_kid_ttl_seconds:
            return True
        del self._unknown_kids[kid]
        return False

    def _remember_unknown(self, kid: str) -> None:
        self._unknown_kids.pop(kid, None)
        self._unknown_kids[kid] = self._clock()
        while len(self._unknown_kids) > self.max_unknown_kids:
            self._unknown_kids.popitem(last=False)

    @staticmethod
    def _not_found(kid: str) -> OAuthValidationError:
        return OAuthValidationError(f"Signing key with kid={kid!r} not found in JWKS")

    async def get_signing_key(self, kid: str) -> jwt.PyJWK:
        """Return the signing key for ``kid``, refetching if stale or unknown."""
        if self._is_stale():
            async with self._lock:
                if self._is_stale():  # another request may have refreshed it
                    await self._refetch()

        key = self._find_key(kid)
        if key is not None:
            return key

        # Unknown kid: at most one forced refetch (key rollover) per cooldown.
        if self._known_unknown(kid) or self._in_cooldown():
            raise self._not_found(kid)
        async with self._lock:
            key = self._find_key(kid)  # a coalesced fetch may have brought it
            if key is not None:
                return key
            if self._known_unknown(kid) or self._in_cooldown():
                raise self._not_found(kid)
            # Start the cooldown before fetching, so a failing fetch is
            # rate-limited too.
            self._last_forced_at = self._clock()
            await self._refetch()
            key = self._find_key(kid)
            if key is None:
                self._remember_unknown(kid)
                raise self._not_found(kid)
            return key


async def validate_id_token(
    id_token: str,
    *,
    jwks: JWKSCache,
    client_id: str,
    issuer: str | None = None,
    issuer_validator: Callable[[str], bool] | None = None,
    nonce: str | None = None,
    allowed_algs: list[str] | None = None,
) -> dict[str, Any]:
    """
    Validate an OIDC ID token (signature, audience, expiry, issuer, nonce).

    Args:
        id_token: The raw JWT.
        jwks: JWKS cache used to resolve the signing key.
        client_id: Expected audience (``aud``).
        issuer: Expected issuer (exact match). Ignored when ``issuer_validator``
            is given.
        issuer_validator: Hook for non-trivial issuer validation (e.g. Azure AD
            multi-tenant). Takes the ``iss`` claim, returns True if acceptable.
        nonce: Expected ``nonce`` claim (checked post-decode when not None).
        allowed_algs: Permitted signature algorithms (asymmetric only;
            ``none``/``HS*`` are always rejected). Defaults to ``["RS256"]``.

    Returns:
        The verified claims dict.

    Raises:
        OAuthValidationError: On any validation failure.
    """
    algs = list(allowed_algs or ["RS256"])
    for alg in algs:
        if alg.lower() in _FORBIDDEN_ALGS or alg.upper().startswith(_FORBIDDEN_ALG_PREFIXES):
            raise OAuthValidationError(f"Algorithm {alg!r} is not allowed for ID tokens")

    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as e:
        raise OAuthValidationError(f"Malformed ID token: {e}") from e

    alg = header.get("alg")
    if alg not in algs:
        raise OAuthValidationError(f"ID token signed with disallowed algorithm {alg!r}")

    kid = header.get("kid")
    if not kid:
        raise OAuthValidationError("ID token header is missing 'kid'")

    key = await jwks.get_signing_key(kid)

    try:
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key=key.key,
            algorithms=algs,  # pinned - never none/HS*
            audience=client_id,
            options={
                "require": ["exp", "iat", "aud", "iss"],
                "verify_iss": False,  # issuer is checked below (hook support)
            },
        )
    except jwt.PyJWTError as e:
        raise OAuthValidationError(f"ID token validation failed: {e}") from e

    iss = claims.get("iss", "")
    if issuer_validator is not None:
        if not issuer_validator(iss):
            raise OAuthValidationError(f"ID token issuer {iss!r} rejected")
    elif issuer is not None:
        if iss != issuer:
            raise OAuthValidationError(
                f"ID token issuer mismatch: expected {issuer!r}, got {iss!r}"
            )

    if nonce is not None and claims.get("nonce") != nonce:
        raise OAuthValidationError("ID token nonce mismatch")

    return claims
