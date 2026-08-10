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


async def test_generate_crud_wires_unwired_app(root: Path):
    """generate_crud on a wire=False app self-wires the full serve chain (G4)."""
    await agents.create_app("shop", wire=False, project_id=root)
    res = await agents.generate_crud(
        "shop", "Item", [{"name": "name", "type": "CharField", "max_length": 20}],
        project_id=root,
    )
    assert res.success, res.message
    assert '"apps.shop",' in _settings(root)
    assert "router.include(shop_router)" in _urls(root)
    described = await agents.describe_project(project_id=root)
    shop_ep = next(e for e in described.data["endpoints"] if e["app"] == "shop")
    assert shop_ep["served"] is True


async def test_describe_project_surfaces_unwired_endpoint_warning(root: Path):
    await agents.create_app("shop", project_id=root)
    await agents.generate_crud(
        "shop", "Item", [{"name": "name", "type": "CharField", "max_length": 20}],
        project_id=root,
    )
    # Manufacture the broken state directly: strip the project-level include
    # (the scaffolding tools no longer produce it on their own).
    urls_path = root / "demo" / "urls.py"
    urls_path.write_text(
        "\n".join(
            line
            for line in urls_path.read_text().splitlines()
            if "shop_router" not in line
        )
        + "\n"
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
    # A fresh project already has the accounts app, its models and its routes,
    # so the outstanding step is confirming the acceptance gate.
    assert "verify_project" in res.data["next_action"]


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
    assert res.data["prefix"] == "tags"


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


# ---------------------------------------------------------------------------
# G — wiring guarantees (routes served, prefixes unique, middleware present)
# ---------------------------------------------------------------------------

_FIELDS = [{"name": "t", "type": "CharField", "max_length": 5}]


async def test_generate_crud_two_models_one_app_get_distinct_prefixes(root: Path):
    """Two models in one app must not collide at one URL prefix (G1)."""
    await agents.create_app("blog", project_id=root)
    r1 = await agents.generate_crud("blog", "Post", _FIELDS, project_id=root)
    r2 = await agents.generate_crud("blog", "Comment", _FIELDS, project_id=root)
    assert r1.success and r2.success, (r1.message, r2.message)
    urls = (root / "apps" / "blog" / "urls.py").read_text()
    assert 'router.register("posts", PostViewSet)' in urls
    assert 'router.register("comments", CommentViewSet)' in urls


async def test_generate_crud_explicit_url_prefix_wins(root: Path):
    await agents.create_app("blog", project_id=root)
    res = await agents.generate_crud(
        "blog", "Post", _FIELDS, url_prefix="entries", project_id=root
    )
    assert res.success, res.message
    urls = (root / "apps" / "blog" / "urls.py").read_text()
    assert 'router.register("entries", PostViewSet)' in urls


async def test_register_route_prefix_conflict_is_never_skippable(root: Path):
    """A prefix taken by a different ViewSet fails even under if_exists='skip'."""
    await agents.create_app("blog", project_id=root)
    for model in ("Post", "Comment"):
        await agents.create_model("blog", model, _FIELDS, project_id=root)
        await agents.create_serializer("blog", model, ["id", "t"], project_id=root)
        await agents.create_viewset("blog", model, project_id=root)
    r1 = await agents.register_route("blog", "Post", url_prefix="items", project_id=root)
    assert r1.success, r1.message
    r2 = await agents.register_route("blog", "Comment", url_prefix="items", project_id=root)
    assert not r2.success and r2.data["error_code"] == "prefix_conflict"
    r3 = await agents.register_route(
        "blog", "Comment", url_prefix="items", if_exists="skip", project_id=root
    )
    assert not r3.success and r3.data["error_code"] == "prefix_conflict"
    # A distinct prefix resolves the conflict.
    r4 = await agents.register_route("blog", "Comment", project_id=root)
    assert r4.success and r4.data["prefix"] == "comments"


async def test_register_route_self_ensures_serve_chain(root: Path):
    """register_route wires INSTALLED_APPS + project urls.py itself (1.7)."""
    await agents.create_app("blog", wire=False, project_id=root)
    await agents.create_model("blog", "Post", _FIELDS, project_id=root)
    await agents.create_serializer("blog", "Post", ["id", "t"], project_id=root)
    await agents.create_viewset("blog", "Post", project_id=root)
    res = await agents.register_route("blog", "Post", project_id=root)
    assert res.success, res.message
    assert res.data["installed"] is True and res.data["urls_included"] is True
    assert '"apps.blog",' in _settings(root)
    assert "router.include(blog_router)" in _urls(root)
    # The skip path repairs wiring too.
    urls_path = root / "demo" / "urls.py"
    urls_path.write_text(
        "\n".join(
            line for line in urls_path.read_text().splitlines() if "blog_router" not in line
        )
        + "\n"
    )
    r2 = await agents.register_route("blog", "Post", if_exists="skip", project_id=root)
    assert r2.success and r2.data.get("skipped") is True
    assert "router.include(blog_router)" in _urls(root)


async def test_create_viewset_register_failure_fails_the_call(root: Path):
    """A ViewSet written but not routed is a failure, not a buried note (G2)."""
    await agents.create_app("blog", project_id=root)
    await agents.create_model("blog", "Tag", _FIELDS, project_id=root)
    await agents.create_serializer("blog", "Tag", ["id", "t"], project_id=root)
    (root / "apps" / "blog" / "urls.py").unlink()
    res = await agents.create_viewset("blog", "Tag", register=True, project_id=root)
    assert not res.success
    assert res.data["viewset_created"] is True
    assert res.data["registered"] is False
    assert "not served" in res.message
    # Restore the app urls.py and re-run: idempotent repair.
    (root / "apps" / "blog" / "urls.py").write_text(
        "from zeeb_api.routers import DefaultRouter\n\nrouter = DefaultRouter()\n"
    )
    r2 = await agents.create_viewset(
        "blog", "Tag", register=True, if_exists="skip", project_id=root
    )
    assert r2.success and r2.data["registered"] is True and r2.data["prefix"] == "tags"


async def test_create_route_wires_full_chain(root: Path):
    """create_route ensures app urls + INSTALLED_APPS + project include (G3)."""
    await agents.create_app("blog", wire=False, project_id=root)
    res = await agents.create_route("blog", "/ping", "get", "ping", project_id=root)
    assert res.success and res.data["wired"] is True
    assert '"apps.blog",' in _settings(root)
    assert "router.include(blog_router)" in _urls(root)


async def test_create_route_fails_when_app_urls_missing(root: Path):
    await agents.create_app("blog", project_id=root)
    (root / "apps" / "blog" / "urls.py").unlink()
    res = await agents.create_route("blog", "/ping", "get", "ping", project_id=root)
    assert not res.success and res.data["error_code"] == "file_not_found"
    # The handler write is kept — the failure message says how to repair.
    assert "async def ping" in (root / "apps" / "blog" / "views.py").read_text()


async def test_setup_auth_fails_cleanly_on_renamed_router(root: Path):
    """A customized project urls.py without a `router` symbol fails loudly (G9)."""
    urls_path = root / "demo" / "urls.py"
    # Drop the scaffolded auth include first, otherwise setup_auth returns
    # already_wired before it ever looks for the router symbol.
    original = "\n".join(
        line
        for line in urls_path.read_text().splitlines()
        if "create_auth_router" not in line
    ).replace("router = DefaultRouter()", "api = DefaultRouter()") + "\n"
    urls_path.write_text(original)
    res = await agents.setup_auth(project_id=root)
    assert not res.success and res.data["error_code"] == "invalid_input"
    assert urls_path.read_text() == original  # left untouched


# ---------------------------------------------------------------------------
# H — describe_project verification layer (M5)
# ---------------------------------------------------------------------------


async def test_describe_project_flags_duplicate_prefixes(root: Path):
    await agents.create_app("blog", project_id=root)
    await agents.generate_crud("blog", "Post", _FIELDS, project_id=root)
    urls_path = root / "apps" / "blog" / "urls.py"
    urls_path.write_text(
        urls_path.read_text() + 'router.register("posts", OtherViewSet)\n'
    )
    res = await agents.describe_project(project_id=root)
    dup = [w for w in res.data["warnings"] if "registered 2 times" in w]
    assert dup and "posts" in dup[0] and "url_prefix" in dup[0]


async def test_describe_project_flags_missing_auth_middleware(root: Path):
    await agents.setup_auth(project_id=root)
    settings_path = root / "demo" / "settings.py"
    settings_path.write_text(
        "\n".join(
            line
            for line in settings_path.read_text().splitlines()
            if "JWTAuthMiddleware" not in line
        ) + "\n"
    )
    res = await agents.describe_project(project_id=root)
    assert res.data["middleware"]["auth"] is False
    assert any("JWTAuthMiddleware" in w for w in res.data["warnings"])
    verify = await agents.verify_project(checks=["structure"], project_id=root)
    assert verify.success  # findings are the payload, not a failure
    assert verify.data["verification"]["passed"] is False


async def test_describe_project_flags_cors_config_without_middleware(root: Path):
    settings_path = root / "demo" / "settings.py"
    settings_path.write_text(
        "\n".join(
            line
            for line in settings_path.read_text().splitlines()
            if "CORSMiddleware" not in line
        ) + "\n"
    )
    res = await agents.describe_project(project_id=root)
    assert res.data["middleware"]["cors"] is False
    assert any("CORSMiddleware" in w for w in res.data["warnings"])


async def test_describe_project_flags_signals_in_uninstalled_app(root: Path):
    await agents.create_app("shop", wire=False, project_id=root)
    await agents.create_model("shop", "Item", _FIELDS, project_id=root)
    sig = await agents.create_signal_receiver(
        "shop", "post_save", "Item", "on_item", project_id=root
    )
    assert sig.success
    assert any("INSTALLED_APPS" in w for w in sig.data["warnings"])
    res = await agents.describe_project(project_id=root)
    assert any("signal receivers" in w for w in res.data["warnings"])


async def test_fresh_wired_project_stays_warning_free(root: Path):
    """False-positive guard: the happy path must not trip the new checks."""
    await agents.create_app("blog", project_id=root)
    await agents.generate_crud("blog", "Post", _FIELDS, migrate=True, project_id=root)
    await agents.setup_auth(project_id=root)
    await agents.configure_cors(["http://localhost:3000"], project_id=root)
    await agents.create_signal_receiver(
        "blog", "post_save", "Post", "on_post", project_id=root
    )
    res = await agents.describe_project(project_id=root)
    assert res.data["warnings"] == [], res.data["warnings"]
    assert res.data["middleware"]["auth"] and res.data["middleware"]["cors"]
