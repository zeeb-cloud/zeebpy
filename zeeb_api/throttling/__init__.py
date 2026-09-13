"""
DRF-style throttling for Zeeb API.

Provides:
- BaseThrottle / SimpleRateThrottle base classes
- AnonRateThrottle, UserRateThrottle, ScopedRateThrottle
- throttle() FastAPI dependency for plain routes
- Pluggable throttle cache (per-process in-memory by default)
"""

from zeeb_api.throttling.base import (
    AnonRateThrottle,
    BaseThrottle,
    ScopedRateThrottle,
    SimpleRateThrottle,
    UserRateThrottle,
    get_default_throttle_classes,
)
from zeeb_api.throttling.cache import (
    BaseThrottleCache,
    InMemoryThrottleCache,
    get_throttle_cache,
    set_throttle_cache,
)
from zeeb_api.throttling.dependency import throttle

__all__ = [
    "AnonRateThrottle",
    "BaseThrottle",
    "BaseThrottleCache",
    "InMemoryThrottleCache",
    "ScopedRateThrottle",
    "SimpleRateThrottle",
    "UserRateThrottle",
    "get_default_throttle_classes",
    "get_throttle_cache",
    "set_throttle_cache",
    "throttle",
]
