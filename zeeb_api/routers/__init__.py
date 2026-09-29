"""
Routers - Auto-generate FastAPI routes from ViewSets.
"""

from zeeb_api.routers.default import (
    DefaultRouter,
    SimpleRouter,
    add_slash_alias_routes,
    declared_route,
    include,
    load_urlconf,
    served_routes,
)

__all__ = [
    "DefaultRouter",
    "SimpleRouter",
    "add_slash_alias_routes",
    "declared_route",
    "include",
    "load_urlconf",
    "served_routes",
]
