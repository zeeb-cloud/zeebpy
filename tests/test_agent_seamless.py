"""Tests for the seamless-DX layer added to ``zeeb_agents``:

auto-wiring (create_app registers + includes the app so it is served),
orientation tools (describe_project / get_started), opt-in idempotency
(if_exists), one-shot generate_crud(migrate=True), and the additive naming
aliases (app=, delete_field, update_signal_receiver).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import zeeb_agents as agents


@pytest.fixture(autouse=True)
def _isolate_global_state():
    """Snapshot/restore the global model registry, metadata and scaffolded
    ``apps.*`` imports.

    ``make_migrations`` / ``generate_crud(migrate=True)`` import and register the
    scaffolded models into zeeb_orm's flat, process-global registry; without
    cleanup they leak into unrelated ORM/migration tests. Mirrors the fixture in
    ``test_model_ref_resolution.py``.
    """
    from zeeb_orm.models.base import _model_registry, metadata

    registry_before = dict(_model_registry)
    tables_before = set(metadata.tables)
    modules_before = set(sys.modules)
    path_before = list(sys.path)
    yield
    # Fully restore the flat, class-name-keyed registry: scaffolded models reuse
    # common names (Post, User, …) and would otherwise *overwrite* another test
    # module's same-named model — a delete-only cleanup misses that.
    _model_registry.clear()
    _model_registry.update(registry_before)
    for name in set(metadata.tables) - tables_before:
        metadata.remove(metadata.tables[name])
    for name in set(sys.modules) - modules_before:
        if name == "apps" or name.startswith("apps.") or name == "settings":
            del sys.modules[name]
    sys.path[:] = path_before


@pytest.fixture
async def root(tmp_path: Path) -> Path:
    res = await agents.create_project("demo", directory=str(tmp_path))
    assert res.success, res.message
    return tmp_path / "demo"


def _settings(root: Path) -> str:
    return (root / "demo" / "settings.py").read_text()


def _urls(root: Path) -> str:
    return (root / "demo" / "urls.py").read_text()


# ---------------------------------------------------------------------------
# A — create_app auto-wiring
# ---------------------------------------------------------------------------


async def test_create_app_auto_wires_installed_apps_and_urls(root: Path):
    res = await agents.create_app("blog", project_id=root)
    assert res.success, res.message
    assert res.data["installed_apps_updated"] is True
    assert res.data["urls_wired"] is True
    assert '"apps.blog",' in _settings(root)
    urls = _urls(root)
    assert "from apps.blog.urls import router as blog_router" in urls
    assert "router.include(blog_router)" in urls


async def test_create_app_wire_false_leaves_project_untouched(root: Path):
    res = await agents.create_app("blog", wire=False, project_id=root)
    assert res.success and res.data["installed_apps_updated"] is False
    assert res.data["urls_wired"] is False
    assert '"apps.blog"' not in _settings(root)
    assert "blog_router" not in _urls(root)


async def test_install_app_and_wire_app_urls_are_idempotent(root: Path):
    await agents.create_app("blog", wire=False, project_id=root)
    r1 = await agents.install_app("blog", project_id=root)
    assert r1.success and r1.data["changed"] is True
    r2 = await agents.install_app("blog", project_id=root)
    assert r2.success and r2.data["changed"] is False  # idempotent

    w1 = await agents.wire_app_urls("blog", project_id=root)
    assert w1.success and w1.data["changed"] is True
    w2 = await agents.wire_app_urls("blog", project_id=root)
    assert w2.success and w2.data["changed"] is False
    assert "router.include(blog_router)" in _urls(root)


async def test_create_app_accepts_app_alias(root: Path):
    res = await agents.create_app(app="blog", project_id=root)
    assert res.success and res.data["name"] == "blog"


async def test_create_app_explicit_name_wins_over_alias(root: Path):
    res = await agents.create_app(name="shop", app="ignored", project_id=root)
    assert res.success and res.data["name"] == "shop"


# ---------------------------------------------------------------------------
# make_migrations unregistered-app warning
# ---------------------------------------------------------------------------


async def test_make_migrations_warns_on_unregistered_app_with_models(root: Path):
    await agents.create_app("shop", wire=False, project_id=root)
    (root / "apps" / "shop" / "models.py").write_text(
        "from zeeb_orm import Model, fields\n\n"
        "class Product(Model):\n    name = fields.CharField(max_length=50)\n"
    )
    res = await agents.make_migrations(project_id=root)
    assert res.success  # "nothing to do" is still success
    assert "shop" in (res.data.get("unregistered_apps") or [])
    assert "INSTALLED_APPS" in res.data.get("warning", "")


# ---------------------------------------------------------------------------
# B — orientation tools
# ---------------------------------------------------------------------------


async def test_describe_project_reports_served_and_wiring(root: Path):
    await agents.create_app("blog", project_id=root)
    crud = await agents.generate_crud(
        "blog", "Post", [{"name": "title", "type": "CharField", "max_length": 100}],
        project_id=root,
    )
    assert crud.success, crud.message
    res = await agents.describe_project(project_id=root)
    assert res.success, res.message
    d = res.data
    assert d["served"] is True
    blog = next(a for a in d["apps"] if a["name"] == "blog")
    assert blog["installed"] and blog["urls_included"] and blog["model_count"] == 1
    ep = next(e for e in d["endpoints"] if e["app"] == "blog")
    assert ep["served"] is True
    assert d["warnings"] == []


async def test_describe_project_surfaces_unwired_endpoint_warning(root: Path):
    await agents.create_app("shop", wire=False, project_id=root)
    await agents.generate_crud(
        "shop", "Item", [{"name": "name", "type": "CharField", "max_length": 20}],
        project_id=root,
    )
    res = await agents.describe_project(project_id=root)
    shop_ep = next(e for e in res.data["endpoints"] if e["app"] == "shop")
    assert shop_ep["served"] is False
    assert any("shop" in w for w in res.data["warnings"])


async def test_get_started_without_project_returns_recipe(root: Path):
    res = await agents.get_started()
    assert res.success
    assert res.data["steps"][0]["step"] == "create_project"
    assert res.data["next_action"] is None
    assert res.data["state"] is None


async def test_get_started_with_project_recommends_next_action(root: Path):
    res = await agents.get_started(project_id=root)
    assert res.success
    assert res.data["state"] is not None
    # No apps yet → recommend create_app.
    assert "create_app" in res.data["next_action"]


# ---------------------------------------------------------------------------
# C — idempotency (if_exists) + error codes
# ---------------------------------------------------------------------------


async def test_create_model_if_exists_error_then_skip(root: Path):
    await agents.create_app("blog", project_id=root)
    fields = [{"name": "t", "type": "CharField", "max_length": 5}]
    r1 = await agents.create_model("blog", "Post", fields, project_id=root)
    assert r1.success
    r2 = await agents.create_model("blog", "Post", fields, project_id=root)
    assert not r2.success and r2.data["error_code"] == "already_exists"
    r3 = await agents.create_model("blog", "Post", fields, if_exists="skip", project_id=root)
    assert r3.success and r3.data.get("skipped") is True


async def test_if_exists_invalid_value_fails(root: Path):
    await agents.create_app("blog", project_id=root)
    res = await agents.create_model(
        "blog", "Post", [{"name": "t", "type": "CharField", "max_length": 5}],
        if_exists="bogus", project_id=root,
    )
    assert not res.success and res.data["error_code"] == "invalid_input"


async def test_generate_crud_is_resumable(root: Path):
    await agents.create_app("blog", project_id=root)
    fields = [{"name": "t", "type": "CharField", "max_length": 5}]
    r1 = await agents.generate_crud("blog", "Post", fields, project_id=root)
    assert r1.success
    # Re-run must not wall on already_exists.
    r2 = await agents.generate_crud("blog", "Post", fields, project_id=root)
    assert r2.success, r2.message


async def test_generate_crud_migrate_applies_migration(root: Path):
    await agents.create_app("blog", project_id=root)
    res = await agents.generate_crud(
        "blog", "Post", [{"name": "t", "type": "CharField", "max_length": 5}],
        migrate=True, project_id=root,
    )
    assert res.success, res.message
    assert res.data["migrated"]["created"] is not None
    assert res.data["migrated"]["applied"]


async def test_create_viewset_register_in_one_call(root: Path):
    await agents.create_app("blog", project_id=root)
    await agents.create_model(
        "blog", "Tag", [{"name": "n", "type": "CharField", "max_length": 10}],
        project_id=root,
    )
    await agents.create_serializer("blog", "Tag", ["id", "n"], project_id=root)
    res = await agents.create_viewset("blog", "Tag", register=True, project_id=root)
    assert res.success and res.data["registered"] is True
    assert res.data["prefix"] == "blog"


async def test_missing_component_file_has_error_code(root: Path):
    await agents.create_app("blog", project_id=root)
    (root / "apps" / "blog" / "models.py").unlink()
    res = await agents.create_model(
        "blog", "Post", [{"name": "t", "type": "CharField", "max_length": 5}],
        project_id=root,
    )
    assert not res.success and res.data["error_code"] == "file_not_found"


async def test_health_endpoint_already_exists_has_error_code(root: Path):
    r1 = await agents.create_health_endpoint(project_id=root)
    assert r1.success, r1.message
    r2 = await agents.create_health_endpoint(project_id=root)
    assert not r2.success and r2.data["error_code"] == "already_exists"


# ---------------------------------------------------------------------------
# F — verb-name aliases
# ---------------------------------------------------------------------------


async def test_delete_field_alias(root: Path):
    await agents.create_app("blog", project_id=root)
    await agents.create_model(
        "blog", "Post",
        [
            {"name": "t", "type": "CharField", "max_length": 5},
            {"name": "x", "type": "IntegerField", "default": 0},
        ],
        project_id=root,
    )
    res = await agents.delete_field("blog", "Post", "x", project_id=root)
    assert res.success and res.data["field"] == "x"


async def test_update_signal_receiver_alias(root: Path):
    await agents.create_app("blog", project_id=root)
    await agents.create_model(
        "blog", "Post", [{"name": "t", "type": "CharField", "max_length": 5}],
        project_id=root,
    )
    await agents.create_signal_receiver("blog", "post_save", "Post", "on_post", project_id=root)
    res = await agents.update_signal_receiver("blog", "on_post", "print('x')", project_id=root)
    assert res.success and res.data["func_name"] == "on_post"
