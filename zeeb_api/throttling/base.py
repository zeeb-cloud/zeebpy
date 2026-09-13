"""
DRF-style throttle classes.

Throttles limit the rate of requests a client may make. They are checked by
ViewSets (via ``throttle_classes`` / ``check_throttles()``) after permission
checks, and can also be used on plain FastAPI routes via
:func:`zeeb_api.throttling.dependency.throttle`.

Rates use the DRF format ``"<num>/<period>"`` where period is one of
``s``/``sec``, ``m``/``min``, ``h``/``hour``, ``d``/``day``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, ClassVar

from fastapi import Request

from zeeb_api.exceptions import ImproperlyConfigured
from zeeb_api.throttling.cache import get_throttle_cache

# Cache of resolved DEFAULT_THROTTLE_CLASSES, keyed by the dotted paths.
_default_throttle_classes_cache: tuple[tuple[str, ...], list[type[BaseThrottle]]] | None = None


def get_default_throttle_classes() -> list[type[BaseThrottle]]:
    """
    Resolve settings.DEFAULT_THROTTLE_CLASSES (dotted paths) to classes.

    The result is cached and re-resolved only when the setting changes.
    """
    global _default_throttle_classes_cache

    from zeeb_api.conf import settings

    paths = tuple(getattr(settings, "DEFAULT_THROTTLE_CLASSES", []) or [])

    cache = _default_throttle_classes_cache
    if cache is None or cache[0] != paths:
        from zeeb_api.middleware.loader import import_string

        cache = (paths, [import_string(path) for path in paths])
        _default_throttle_classes_cache = cache

    return list(cache[1])


class BaseThrottle:
    """
    Base class for all throttles.

    Subclasses override :meth:`allow_request` and optionally :meth:`wait`.
    """

    async def allow_request(self, request: Request, view: Any) -> bool:
        """Return True if the request should be allowed."""
        return True

    def wait(self) -> float | None:
        """
        Return the recommended number of seconds to wait before the next
        request, or None if unknown.
        """
        return None

    def get_ident(self, request: Request) -> str:
        """
        Identify the machine making the request.

        When ``settings.THROTTLE_NUM_PROXIES`` is set, the app runs behind that
        many trusted reverse proxies, each of which *appends* the address it saw
        to ``X-Forwarded-For``. The real client is therefore the
        ``num_proxies``-th entry counted **from the right** — the last hops are
        the ones written by infrastructure we control. Reading the *leftmost*
        entry (as this used to) is unsafe: it is fully attacker-controlled, so a
        client could forge unlimited distinct throttle keys (bypass) or spoof a
        victim's address (poisoning). This mirrors DRF's ``BaseThrottle.get_ident``.

        With no configured proxy count, the directly connected client address is
        used.
        """
        from zeeb_api.conf import settings

        num_proxies = getattr(settings, "THROTTLE_NUM_PROXIES", None)
        if num_proxies:
            forwarded_for = request.headers.get("X-Forwarded-For")
            if forwarded_for:
                addrs = [a.strip() for a in forwarded_for.split(",") if a.strip()]
                if addrs:
                    # Clamp so a short/absent proxy chain still yields the
                    # left-most present hop rather than an IndexError.
                    return addrs[-min(num_proxies, len(addrs))]

        client = getattr(request, "client", None)
        if client is not None and client.host:
            return client.host
        return "unknown"


class SimpleRateThrottle(BaseThrottle):
    """
    Sliding-window rate throttle (DRF semantics).

    A history of request timestamps is kept per cache key. Timestamps older
    than the rate's duration are dropped; if the remaining history is at
    capacity the request is throttled.

    Configure via the ``rate`` class attribute (e.g. ``"100/min"``) or via
    ``scope`` + settings.DEFAULT_THROTTLE_RATES. A rate of None makes the
    throttle a no-op.
    """

    scope: ClassVar[str | None] = None
    rate: str | None = None
    cache_format: ClassVar[str] = "throttle:{scope}:{ident}"

    # Injectable clock (class attribute so tests can substitute a fake).
    timer: Callable[[], float] = staticmethod(time.monotonic)

    def __init__(self) -> None:
        if self.rate is None:
            self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)
        self.history: list[float] = []
        self.now: float = 0.0

    def get_rate(self) -> str | None:
        """Look up the rate for this throttle's scope in settings."""
        if self.scope is None:
            return None

        from zeeb_api.conf import settings

        rates = getattr(settings, "DEFAULT_THROTTLE_RATES", {}) or {}
        return rates.get(self.scope)

    def parse_rate(self, rate: str | None) -> tuple[int | None, int | None]:
        """
        Parse a rate string into (num_requests, duration_seconds).

        "100/min" -> (100, 60). Accepts s/sec, m/min, h/hour, d/day.
        """
        if rate is None:
            return (None, None)

        try:
            num, period = rate.split("/")
            num_requests = int(num)
        except ValueError:
            raise ImproperlyConfigured(
                f"Invalid throttle rate: {rate!r}. Expected '<number>/<period>'."
            ) from None

        durations = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        try:
            duration = durations[period[0]]
        except (IndexError, KeyError):
            raise ImproperlyConfigured(
                f"Invalid throttle rate period: {rate!r}. "
                "Expected one of s/sec, m/min, h/hour, d/day."
            ) from None

        return (num_requests, duration)

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        """
        Return the cache key for this request, or None to skip throttling.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement get_cache_key()"
        )

    async def allow_request(self, request: Request, view: Any) -> bool:
        if self.rate is None:
            return True

        key = self.get_cache_key(request, view)
        if key is None:
            return True

        cache = get_throttle_cache()
        self.history = await cache.get_history(key)
        self.now = self.timer()

        # Drop timestamps outside the sliding window (oldest are last).
        while self.history and self.history[-1] <= self.now - self.duration:
            self.history.pop()

        if len(self.history) >= self.num_requests:
            return False

        self.history.insert(0, self.now)
        await cache.set_history(key, self.history, self.duration)
        return True

    def wait(self) -> float | None:
        """Seconds until a request slot becomes available."""
        if self.duration is None or self.num_requests is None:
            return None

        if self.history:
            remaining_duration = self.duration - (self.now - self.history[-1])
        else:
            remaining_duration = float(self.duration)

        available_requests = self.num_requests - len(self.history) + 1
        if available_requests <= 0:
            return None

        return remaining_duration / float(available_requests)

    @staticmethod
    def _get_user(request: Request) -> Any:
        state = getattr(request, "state", None)
        return getattr(state, "user", None) if state is not None else None


class AnonRateThrottle(SimpleRateThrottle):
    """
    Throttle unauthenticated requests only, keyed by client IP.

    Authenticated requests (request.state.user set) are never throttled.
    """

    scope = "anon"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        if self._get_user(request) is not None:
            return None
        return self.cache_format.format(scope=self.scope, ident=self.get_ident(request))


class UserRateThrottle(SimpleRateThrottle):
    """
    Throttle all requests, keyed by user id when authenticated and by
    client IP otherwise.
    """

    scope = "user"

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        user = self._get_user(request)
        if user is not None and getattr(user, "id", None) is not None:
            ident = str(user.id)
        else:
            ident = self.get_ident(request)
        return self.cache_format.format(scope=self.scope, ident=ident)


class ScopedRateThrottle(SimpleRateThrottle):
    """
    Throttle based on the view's ``throttle_scope`` attribute.

    The scope is read from the view at request time and looked up in
    settings.DEFAULT_THROTTLE_RATES. Views without a ``throttle_scope``
    are not throttled.
    """

    scope_attr = "throttle_scope"

    def __init__(self) -> None:
        # Rate resolution is deferred to allow_request(), once the view's
        # scope is known. Initialize bookkeeping attributes only.
        self.num_requests: int | None = None
        self.duration: int | None = None
        self.history = []
        self.now = 0.0

    async def allow_request(self, request: Request, view: Any) -> bool:
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True

        self.rate = self.get_rate()
        if self.rate is None:
            return True
        self.num_requests, self.duration = self.parse_rate(self.rate)

        return await super().allow_request(request, view)

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        user = self._get_user(request)
        if user is not None and getattr(user, "id", None) is not None:
            ident = str(user.id)
        else:
            ident = self.get_ident(request)
        return self.cache_format.format(scope=self.scope, ident=ident)
