"""
FastAPI dependency for throttling plain (non-ViewSet) routes.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request

from zeeb_api.exceptions import RateLimitException
from zeeb_api.throttling.base import SimpleRateThrottle


def throttle(rate: str, scope: str = "default") -> Callable[[Request], Coroutine[Any, Any, None]]:
    """
    Build a FastAPI dependency that rate-limits a route.

    Requests are keyed by client IP. On limit, raises RateLimitException
    (429) with a Retry-After header.

    Usage:
        @app.post("/auth/login", dependencies=[Depends(throttle("5/min", scope="login"))])
        async def login(...):
            ...

    Args:
        rate: DRF-style rate string, e.g. "5/min".
        scope: Cache key namespace (use distinct scopes for distinct routes).
    """

    class _RouteThrottle(SimpleRateThrottle):
        def get_cache_key(self, request: Request, view: Any) -> str | None:
            return self.cache_format.format(scope=self.scope, ident=self.get_ident(request))

    _RouteThrottle.scope = scope
    _RouteThrottle.rate = rate

    async def throttle_dependency(request: Request) -> None:
        instance = _RouteThrottle()
        if not await instance.allow_request(request, None):
            wait = instance.wait()
            raise RateLimitException(
                retry_after=math.ceil(wait) if wait is not None else None,
            )

    return throttle_dependency
