"""
Consumed refresh-token store for refresh-token rotation / reuse detection.

When a refresh token is redeemed at ``/auth/refresh`` it is *rotated*: a fresh
pair is issued and the presented token's ``jti`` is recorded here so the same
refresh token cannot be redeemed twice. Presenting an already-consumed refresh
token (replay of a stolen or intercepted token) is rejected, and the whole token
*family* — every refresh token descended from the same login, tracked by the
``fam`` claim — is revoked with it: after a replay nobody can tell the thief's
copy from the owner's, so both must log in again.

The default backend is per-process and in-memory — best-effort reuse detection
that does not survive a restart and is not shared across workers. For a
deployment that needs a hard guarantee across processes, implement
:class:`BaseRefreshTokenStore` on top of a shared store (e.g. Redis) and install
it with :func:`set_refresh_token_store`. Follows the same pluggable pattern as
``zeeb_api.throttling.cache``.
"""

from __future__ import annotations

import asyncio
import time


class BaseRefreshTokenStore:
    """Interface for tracking consumed (rotated) refresh tokens and revoked families.

    Implement :meth:`is_consumed` and :meth:`consume`; the rest have working
    defaults built on those two. Override :meth:`consume_once` with an atomic
    primitive (e.g. Redis ``SET key 1 NX EX ttl``) — the default is a check
    followed by a write, which two concurrent requests can both pass.
    """

    async def is_consumed(self, jti: str) -> bool:
        """Return True if ``jti`` was already redeemed (and not yet expired)."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement is_consumed()"
        )

    async def consume(self, jti: str, ttl_seconds: float) -> None:
        """Mark ``jti`` consumed for ``ttl_seconds`` (its remaining lifetime)."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement consume()"
        )

    async def consume_once(self, jti: str, ttl_seconds: float) -> bool:
        """Mark ``jti`` consumed; return False if it already was.

        ``/auth/refresh`` redeems a token only when this returns True, so the
        check and the write must be one atomic step: otherwise two requests
        replaying the same token can both be issued a fresh pair.
        """
        if await self.is_consumed(jti):
            return False
        await self.consume(jti, ttl_seconds)
        return True

    async def revoke_family(self, family: str, ttl_seconds: float) -> None:
        """Revoke every refresh token descended from one login (``fam`` claim)."""
        await self.consume(_family_key(family), ttl_seconds)

    async def is_family_revoked(self, family: str) -> bool:
        """Whether the token family was revoked (reuse detected, or logout)."""
        return await self.is_consumed(_family_key(family))


def _family_key(family: str) -> str:
    return f"family:{family}"


class InMemoryRefreshTokenStore(BaseRefreshTokenStore):
    """
    Per-process in-memory consumed-jti set with TTL expiry.

    State is local to the process and lost on restart; behind multiple workers
    each process tracks reuse independently. Use a shared backend for a global
    guarantee.
    """

    # Prune expired entries at most every N writes.
    _prune_every = 64

    def __init__(self) -> None:
        # jti -> expires_at (monotonic seconds)
        self._data: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._writes = 0

    async def is_consumed(self, jti: str) -> bool:
        """Return True if ``jti`` was redeemed and its TTL has not run out."""
        async with self._lock:
            return self._live(jti)

    async def consume(self, jti: str, ttl_seconds: float) -> None:
        """Mark ``jti`` consumed for ``ttl_seconds``, pruning expired entries as it goes."""
        async with self._lock:
            self._write(jti, ttl_seconds)

    async def consume_once(self, jti: str, ttl_seconds: float) -> bool:
        """Atomic check-and-consume: one lock section, no await in between."""
        async with self._lock:
            if self._live(jti):
                return False
            self._write(jti, ttl_seconds)
            return True

    def _live(self, jti: str) -> bool:
        """Whether ``jti`` is recorded and unexpired (caller must hold the lock)."""
        expires_at = self._data.get(jti)
        if expires_at is None:
            return False
        if expires_at <= time.monotonic():
            del self._data[jti]
            return False
        return True

    def _write(self, jti: str, ttl_seconds: float) -> None:
        """Record ``jti`` (caller must hold the lock)."""
        self._writes += 1
        if self._writes % self._prune_every == 0:
            self._prune()
        self._data[jti] = time.monotonic() + max(ttl_seconds, 0.0)

    def _prune(self) -> None:
        """Drop expired entries (caller must hold the lock)."""
        now = time.monotonic()
        expired = [jti for jti, expires_at in self._data.items() if expires_at <= now]
        for jti in expired:
            del self._data[jti]


# Module-level default store (per-process).
_refresh_token_store: BaseRefreshTokenStore = InMemoryRefreshTokenStore()


def get_refresh_token_store() -> BaseRefreshTokenStore:
    """Return the globally configured refresh-token store."""
    return _refresh_token_store


def set_refresh_token_store(store: BaseRefreshTokenStore | None) -> None:
    """
    Install a custom refresh-token store (e.g. a Redis-backed implementation,
    or a fresh InMemoryRefreshTokenStore between tests). ``None`` restores a
    fresh default in-memory store.
    """
    global _refresh_token_store
    _refresh_token_store = store if store is not None else InMemoryRefreshTokenStore()
