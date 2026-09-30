"""served_routes() reads an app's routes back, or says loudly that it cannot.

It relies on FastAPI's undocumented ``iter_route_contexts`` (0.137+ includes
routers lazily). If that internal disappears or changes shape, an inventory
that quietly lacks the included routes would make ``inspect``/``showurls``
report an empty app. The fallback walker keeps reading included routers, and
anything it still cannot read raises ``RouteInventoryError``.
"""

import pytest
from fastapi import APIRouter, FastAPI
from starlette.routing import BaseRoute

import zeeb_api.routers.default as router_module
from zeeb_api.exceptions import RouteInventoryError
from zeeb_api.routers import declared_route, served_routes


def _app() -> FastAPI:
    inner = APIRouter()

    @inner.get("/")
    async def index():
        return {}

    @inner.get("/items/{item_id}")
    async def item(item_id: int):
        return {}

    middle = APIRouter()
    middle.include_router(inner, prefix="/inner")

    app = FastAPI()
    app.include_router(middle, prefix="/api")

    @app.get("/health")
    async def health():
        return {}

    return app


EXPECTED = {"/api/inner/", "/api/inner/items/{item_id}", "/health"}


def _paths(routes) -> set[str]:
    return {r.path for r in routes if getattr(declared_route(r), "endpoint", None) is not None}


def test_reads_nested_included_routers():
    assert EXPECTED <= _paths(served_routes(_app().routes))


def test_fallback_walker_without_iter_route_contexts(monkeypatch):
    monkeypatch.setattr(router_module, "_iter_route_contexts", None)
    served = served_routes(_app().routes)
    assert EXPECTED <= _paths(served)
    item = next(r for r in served if r.path == "/api/inner/items/{item_id}")
    assert declared_route(item).path == "/items/{item_id}"


def test_fallback_walker_when_iter_route_contexts_breaks(monkeypatch):
    def broken(routes):
        raise TypeError("internal signature changed")

    monkeypatch.setattr(router_module, "_iter_route_contexts", broken)
    assert EXPECTED <= _paths(served_routes(_app().routes))


def test_unreadable_route_shape_fails_loudly(monkeypatch):
    """A future lazily-included router the walker does not understand must
    raise - never be dropped from the inventory."""

    class _FutureIncludedRouter(BaseRoute):
        def __init__(self):
            self.wrapped = APIRouter()

    monkeypatch.setattr(router_module, "_iter_route_contexts", None)
    with pytest.raises(RouteInventoryError, match="_FutureIncludedRouter"):
        served_routes([*_app().routes, _FutureIncludedRouter()])


def test_iter_route_contexts_returning_pathless_entries_fails_loudly(monkeypatch):
    class _Opaque:
        path = None
        original_route = object()

    monkeypatch.setattr(router_module, "_iter_route_contexts", lambda routes: [_Opaque()])
    with pytest.raises(RouteInventoryError):
        served_routes(_app().routes)
