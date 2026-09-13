"""
Routers - Auto-generate FastAPI routes from ViewSets.
"""

from zeeb_api.routers.default import (
    DefaultRouter,
    SimpleRouter,
    add_slash_alias_routes,
    include,
    load_urlconf,
)

__all__ = [
    "DefaultRouter",
    "SimpleRouter",
    "add_slash_alias_routes",
    "include",
    "load_urlconf",
]
