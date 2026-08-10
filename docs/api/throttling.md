# Throttling

Limit the rate of requests clients can make, with declarative throttle classes.

Throttles run on every ViewSet request right after permission checks. When a
limit is exceeded the API returns **429 Too Many Requests** with a
`Retry-After` header and the standardized error envelope
(`error.code == "RATE_LIMIT_EXCEEDED"`).

## What a generated project already has

`zeeb startproject` turns throttling on:

```python
DEFAULT_THROTTLE_CLASSES = [
    "zeeb_api.throttling.AnonRateThrottle",
    "zeeb_api.throttling.UserRateThrottle",
]
DEFAULT_THROTTLE_RATES = {"anon": "120/min", "user": "1200/min"}
```

These apply to every ViewSet that does not set its own `throttle_classes`.
Tune them from `.env` with `THROTTLE_ANON_RATE` and `THROTTLE_USER_RATE`, or
turn throttling off entirely with an empty `DEFAULT_THROTTLE_CLASSES=`.

`/login` and `/register` carry a separate per-client limit
(`AUTH_LOGIN_THROTTLE_RATE`, default `10/min`).

> The default cache is in-memory **per process**. Behind several workers the
> effective limit multiplies by the worker count — install a shared
> `BaseThrottleCache` with `set_throttle_cache()` before deploying more than one.

## Quick Start

```python
from zeeb_api.throttling import AnonRateThrottle, UserRateThrottle
from zeeb_api.viewsets import ModelViewSet


class ArticleViewSet(ModelViewSet):
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
    throttle_classes = [AnonRateThrottle, UserRateThrottle]
```

```python
# settings.py
DEFAULT_THROTTLE_RATES = {
    "anon": "100/min",
    "user": "1000/min",
}
```

Or apply throttling globally to all viewsets:

```python
# settings.py
DEFAULT_THROTTLE_CLASSES = [
    "zeeb_api.throttling.AnonRateThrottle",
]
DEFAULT_THROTTLE_RATES = {
    "anon": "100/min",
}
```

Viewsets with `throttle_classes = None` (the default) use
`DEFAULT_THROTTLE_CLASSES`; set `throttle_classes = []` to opt a viewset out
of global throttling.

## Rate Format

Rates use the format `"<number>/<period>"`:

| Rate | Meaning |
|------|---------|
| `"10/s"` or `"10/sec"` | 10 requests per second |
| `"100/min"` or `"100/m"` | 100 requests per minute |
| `"1000/hour"` or `"1000/h"` | 1000 requests per hour |
| `"10000/day"` or `"10000/d"` | 10000 requests per day |

A rate of `None` disables the throttle (no-op). Throttling uses a
sliding-window algorithm: a history of request timestamps is kept per client,
and requests are denied while the window already contains the allowed number
of requests.

## Built-in Throttle Classes

### AnonRateThrottle

Throttles **unauthenticated** requests only, keyed by client IP. Requests
with `request.state.user` set (e.g. via `JWTAuthMiddleware`) are never
throttled. Scope: `"anon"`.

### UserRateThrottle

Throttles **all** requests — keyed by user id when authenticated, by client
IP otherwise. Scope: `"user"`.

### ScopedRateThrottle

Throttles based on the viewset's `throttle_scope` attribute, so different
parts of the API can have different limits:

```python
from zeeb_api.throttling import ScopedRateThrottle


class UploadViewSet(ModelViewSet):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "uploads"


class SearchViewSet(ModelViewSet):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "search"
```

```python
# settings.py
DEFAULT_THROTTLE_RATES = {
    "uploads": "20/day",
    "search": "60/min",
}
```

Viewsets without a `throttle_scope` are not throttled.

## Throttling Plain Routes

For non-viewset routes (e.g. login), use the `throttle()` dependency:

```python
from fastapi import Depends
from zeeb_api.throttling import throttle


@app.post("/auth/login", dependencies=[Depends(throttle("5/min", scope="login"))])
async def login(credentials: LoginRequest):
    ...
```

Requests are keyed by client IP. Use a distinct `scope` per route so routes
don't share rate windows.

## Custom Throttles

Subclass `SimpleRateThrottle` and implement `get_cache_key()` (return `None`
to skip throttling for a request):

```python
from zeeb_api.throttling import SimpleRateThrottle


class APIKeyRateThrottle(SimpleRateThrottle):
    scope = "api_key"

    def get_cache_key(self, request, view):
        api_key = request.headers.get("X-API-Key")
        if api_key is None:
            return None  # not throttled by this class
        return self.cache_format.format(scope=self.scope, ident=api_key)
```

For full control (e.g. time-of-day rules), subclass `BaseThrottle` and
override `async allow_request(request, view)` and optionally `wait()`.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `DEFAULT_THROTTLE_CLASSES` | `[]` | Dotted paths of throttles applied to every viewset that does not set `throttle_classes` |
| `DEFAULT_THROTTLE_RATES` | `{"anon": None, "user": None}` | Rate per throttle scope |
| `THROTTLE_NUM_PROXIES` | `None` | Number of trusted proxies. When set, the client IP is taken `THROTTLE_NUM_PROXIES` entries **from the right** of `X-Forwarded-For` instead of the socket address — the leftmost entry is client-controlled and would let anyone forge unlimited distinct throttle keys |

## Caching Caveat (Multi-Process Deployments)

The default throttle cache (`InMemoryThrottleCache`) is **per process**:
behind multiple uvicorn/gunicorn workers, each worker counts requests
independently, so the effective limit is roughly `rate × workers`.

For accurate global limits, implement the cache interface on a shared store
and install it at startup:

```python
from zeeb_api.throttling import set_throttle_cache
from zeeb_api.throttling.cache import BaseThrottleCache


class RedisThrottleCache(BaseThrottleCache):
    def __init__(self, redis):
        self.redis = redis

    async def get_history(self, key: str) -> list[float]:
        raw = await self.redis.get(key)
        return json.loads(raw) if raw else []

    async def set_history(self, key: str, history: list[float], duration: float) -> None:
        await self.redis.set(key, json.dumps(history), ex=int(duration))


set_throttle_cache(RedisThrottleCache(redis))
```

In tests, install a fresh `InMemoryThrottleCache()` between tests to reset
state:

```python
from zeeb_api.throttling import InMemoryThrottleCache, set_throttle_cache

set_throttle_cache(InMemoryThrottleCache())
```
