"""
Routers - Auto-generate FastAPI routes from ViewSets.
"""

from zeeb_api.routers.default import DefaultRouter, SimpleRouter, include, load_urlconf

__all__ = ["DefaultRouter", "SimpleRouter", "include", "load_urlconf"]
