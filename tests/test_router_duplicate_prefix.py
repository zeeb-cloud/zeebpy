"""DefaultRouter duplicate-prefix guard (runtime backstop for G1).

FastAPI first-match routing silently shadows a second ViewSet registered at an
already-taken prefix; the router now refuses instead, both on direct
``register`` and when ``include`` merges another router's registrations.
"""

from __future__ import annotations

import pytest

from zeeb_api.routers import DefaultRouter


class PostViewSet:
    pass


class ArticleViewSet:
    pass


def test_register_duplicate_prefix_raises():
    router = DefaultRouter()
    router.register("posts", PostViewSet)
    with pytest.raises(ValueError, match="already registered"):
        router.register("posts", ArticleViewSet)


def test_register_duplicate_prefix_normalizes_slashes():
    router = DefaultRouter()
    router.register("posts", PostViewSet)
    with pytest.raises(ValueError, match="already registered"):
        router.register("/posts/", ArticleViewSet)


def test_include_duplicate_prefix_raises():
    parent, child = DefaultRouter(), DefaultRouter()
    parent.register("posts", PostViewSet)
    child.register("posts", ArticleViewSet)
    with pytest.raises(ValueError, match="already registered"):
        parent.include(child)


def test_distinct_prefixes_are_fine():
    router = DefaultRouter()
    router.register("posts", PostViewSet)
    router.register("articles", ArticleViewSet)
    child = DefaultRouter()
    child.register("comments", PostViewSet)
    router.include(child)
    assert [p for p, _, _ in router._registry] == ["posts", "articles", "comments"]
