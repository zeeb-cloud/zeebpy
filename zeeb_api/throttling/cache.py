"""
Throttle history caches.

Throttles store a sliding window of request timestamps per cache key.
The default backend is an in-process, asyncio-safe dict cache. For
multi-process deployments, implement :class:`BaseThrottleCache` on top of a
shared store (e.g. Redis) and install it with :func:`set_throttle_cache`.
"""

from __future__ import annotations

import asyncio
import time


class BaseThrottleCache:
    """
    Interface for throttle history storage.

    A "history" is a list of monotonic timestamps (newest first) of the
    requests made under a given cache key.
    """

    async def get_history(self, key: str) -> list[float]:
        """Return the stored history for ``key`` (empty list if absent)."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement get_history()"
        )

    async def set_history(self, key: str, history: list[float], duration: float) -> None:
        """Store ``history`` for ``key``, expiring after ``duration`` seconds."""
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement set_history()"
        )


class InMemoryThrottleCache(BaseThrottleCache):
    """
    Per-process in-memory throttle cache.

    Uses an asyncio.Lock for safe concurrent access and opportunistically
    prunes expired entries on writes.

    Note: state is local to the process. Behind multiple workers each
    process throttles independently; use a shared cache backend for
    globally accurate limits.
    """

    # Prune expired entries at most every N writes
    _prune_every = 64

    def __init__(self) -> None:
        # key -> (history, expires_at)
        self._data: dict[str, tuple[list[float], float]] = {}
        self._lock = asyncio.Lock()
        self._writes = 0

    async def get_history(self, key: str) -> list[float]:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return []
            history, expires_at = entry
            if expires_at <= time.monotonic():
                del self._data[key]
                return []
            return list(history)

    async def set_history(self, key: str, history: list[float], duration: float) -> None:
        async with self._lock:
            self._writes += 1
            if self._writes % self._prune_every == 0:
                self._prune()
            self._data[key] = (list(history), time.monotonic() + duration)

    def _prune(self) -> None:
        """Drop expired entries (caller must hold the lock)."""
        now = time.monotonic()
        expired = [key for key, (_, expires_at) in self._data.items() if expires_at <= now]
        for key in expired:
            del self._data[key]


# Module-level default cache (per-process).
_throttle_cache: BaseThrottleCache = InMemoryThrottleCache()


def get_throttle_cache() -> BaseThrottleCache:
    """Return the globally configured throttle cache."""
    return _throttle_cache


def set_throttle_cache(cache: BaseThrottleCache) -> None:
    """
    Install a custom throttle cache (e.g. a Redis-backed implementation,
    or a fresh InMemoryThrottleCache between tests).
    """
    global _throttle_cache
    _throttle_cache = cache
