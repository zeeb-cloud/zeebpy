"""
Consumed refresh-token store for refresh-token rotation / reuse detection.

When a refresh token is redeemed at ``/auth/refresh`` it is *rotated*: a fresh
pair is issued and the presented token's ``jti`` is recorded here so the same
refresh token cannot be redeemed twice. Presenting an already-consumed refresh
token (replay of a stolen or intercepted token) is rejected.

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
    """Interface for tracking consumed (rotated) refresh-token ``jti`` values."""

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
            expires_at = self._data.get(jti)
            if expires_at is None:
                return False
            if expires_at <= time.monotonic():
                del self._data[jti]
                return False
            return True

    async def consume(self, jti: str, ttl_seconds: float) -> None:
        """Mark ``jti`` consumed for ``ttl_seconds``, pruning expired entries as it goes."""
        async with self._lock:
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


def set_refresh_token_store(store: BaseRefreshTokenStore) -> None:
    """
    Install a custom refresh-token store (e.g. a Redis-backed implementation,
    or a fresh InMemoryRefreshTokenStore between tests).
    """
    global _refresh_token_store
    _refresh_token_store = store
