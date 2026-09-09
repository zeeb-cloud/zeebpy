"""Route inventory reports what a module executes, not what it documents.

``apps/<app>/urls.py`` is generated with a worked ``router.register("blogs",
BlogViewSet)`` example in its module docstring. A textual scan counts that
example as a real registration, and the verification chain then compares the
served OpenAPI contract against a prefix no runtime will ever serve — so
``verify_project`` fails on ``missing_endpoints`` forever, on a project that is
in fact correct.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import zeeb_agents as agents
from zeeb_agents.schema import _scan_routes

SPEC = {
    "name": "blog",
    "entities": [
        {
            "name": "Post",
            "fields": [
                {"name": "title", "type": "string", "max_length": 200},
                {"name": "body", "type": "text"},
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def _isolate_global_state():
    """Snapshot/restore the process-global ORM registry and ``apps.*`` imports."""
    from zeeb_orm.models.base import _model_registry, metadata

    registry_before = dict(_model_registry)
    tables_before = set(metadata.tables)
    modules_before = set(sys.modules)
    path_before = list(sys.path)
    yield
    _model_registry.clear()
    _model_registry.update(registry_before)
    for name in set(metadata.tables) - tables_before:
        metadata.remove(metadata.tables[name])
    for name in set(sys.modules) - modules_before:
        if name == "apps" or name.startswith("apps.") or name == "settings":
            del sys.modules[name]
    sys.path[:] = path_before


def _prefixes(routes: list[dict]) -> list[str]:
    return [r["prefix"] for r in routes if r["type"] == "viewset"]


# ---------------------------------------------------------------------------
# Unit: what counts as a registration
# ---------------------------------------------------------------------------


def test_docstring_examples_are_not_routes():
    source = '''"""Blog URL configuration.

Registering a viewset here is what makes its endpoints exist:

    from .views import BlogViewSet

    router.register("blogs", BlogViewSet)
"""

from zeeb_api.routers import DefaultRouter
from .views import PostViewSet

router = DefaultRouter()
router.register("posts", PostViewSet)
'''
    assert _prefixes(_scan_routes("blog", "urls.py", source)) == ["posts"]


def test_commented_out_registrations_are_not_routes():
    source = (
        "router = DefaultRouter()\n"
        '# router.register("drafts", DraftViewSet)\n'
        'router.register("posts", PostViewSet)\n'
    )
    assert _prefixes(_scan_routes("blog", "urls.py", source)) == ["posts"]


def test_standalone_route_decorators_are_collected():
    source = (
        '@router.get("/health")\n'
        "async def health():\n"
        "    return {}\n"
    )
    routes = _scan_routes("ops", "views.py", source)
    assert routes == [
        {"app": "ops", "file": "views.py", "type": "route", "method": "GET", "path": "/health"}
    ]


def test_decorators_inside_docstrings_are_not_routes():
    source = '"""Example:\n\n    @router.get("/example")\n"""\n'
    assert _scan_routes("ops", "views.py", source) == []


def test_registrations_keep_source_order_with_viewsets_first():
    source = (
        '@router.get("/ping")\n'
        "async def ping():\n"
        "    return {}\n"
        'router.register("posts", PostViewSet)\n'
        'router.register("comments", CommentViewSet)\n'
    )
    routes = _scan_routes("blog", "urls.py", source)
    assert [r["type"] for r in routes] == ["viewset", "viewset", "route"]
    assert _prefixes(routes) == ["posts", "comments"]


def test_unparseable_file_falls_back_to_a_textual_scan():
    """A broken module still yields a best-effort inventory for diagnosis."""
    source = 'router.register("posts", PostViewSet)\ndef broken(\n'
    assert _prefixes(_scan_routes("blog", "urls.py", source)) == ["posts"]


# ---------------------------------------------------------------------------
# End to end: a real scaffolded project
# ---------------------------------------------------------------------------


async def test_generated_project_reports_only_its_real_prefixes(tmp_path: Path):
    created = await agents.create_project("demo", directory=str(tmp_path))
    assert created.success, created.message
    root = tmp_path / "demo"
    built = await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)
    assert built.success, built.message

    # Guard against a vacuous assertion: the bug only exists while the scaffold
    # documents a registration it does not perform.
    urls = (root / "apps" / "blog" / "urls.py").read_text(encoding="utf-8")
    assert 'router.register("blogs"' in urls

    res = await agents.list_all_routes(project_id=root)
    assert res.success, res.message
    prefixes = _prefixes(res.data["routes"])
    assert "posts" in prefixes
    assert "blogs" not in prefixes
