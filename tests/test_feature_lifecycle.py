"""A feature is a thing you can list, change, park, restore, and destroy.

These run against a real scaffolded project. The load-bearing guarantee under
test is that *deactivating loses nothing*: the API layer goes, the models and
their tables stay, and activating puts the code back exactly as it left —
including edits made after it was generated.

The second guarantee is that features may share an app without interfering:
archiving one must leave its app-mate serving.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import zeeb_agents as agents
from zeeb_agents.feature_manifest import load_manifest, manifest_path

BLOG = {
    "name": "blog",
    "app": "content",
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

# A second feature living in the SAME app — the shared-app case.
NOTES = {
    "name": "notes",
    "app": "content",
    "entities": [
        {"name": "Note", "fields": [{"name": "text", "type": "string"}]},
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


@pytest.fixture
async def root(tmp_path: Path) -> Path:
    res = await agents.create_project("demo", directory=str(tmp_path))
    assert res.success, res.message
    return tmp_path / "demo"


@pytest.fixture
async def built(root: Path) -> Path:
    res = await agents.build_feature(BLOG, migrate=False, verify=False, project_id=root)
    assert res.success, res.message
    return root


def _views(root: Path) -> str:
    return (root / "apps" / "content" / "views.py").read_text(encoding="utf-8")


def _models_src(root: Path) -> str:
    return (root / "apps" / "content" / "models.py").read_text(encoding="utf-8")


def _urls(root: Path) -> str:
    return (root / "apps" / "content" / "urls.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Recording and listing
# ---------------------------------------------------------------------------


async def test_build_feature_records_the_feature_with_its_artifacts(built: Path):
    res = await agents.list_features(project_id=built)
    assert res.success, res.message
    names = [f["name"] for f in res.data["features"]]
    assert "blog" in names
    # The app blog lives in must not also appear as an inferred feature —
    # it would claim ownership of the same artifacts under a second name.
    assert "content" not in names

    feature = next(f for f in res.data["features"] if f["name"] == "blog")
    assert feature["name"] == "blog"
    assert feature["app"] == "content"
    assert feature["status"] == "active"
    assert feature["inferred"] is False
    assert feature["entities"] == ["Post"]
    assert feature["has_spec"] is True

    owned = load_manifest(built)["features"]["blog"]["artifacts"]
    assert owned["models"] == ["content.Post"]
    assert owned["serializers"] == ["content.PostSerializer"]
    assert owned["viewsets"] == ["content.PostViewSet"]
    assert owned["routes"][0]["model"] == "Post"


async def test_applying_a_plan_records_the_spec_like_a_direct_build(root: Path):
    """plan → apply must leave a feature the lifecycle tools can still recompile.

    A plan-applied feature used to land in the manifest without its spec, so it
    read as spec-less: regenerate_tests refused it and restoring it from
    archived had nothing to rebuild from — for a feature built through the
    structured flow, not one reconstructed off disk.
    """
    plan_res = await agents.plan_feature(BLOG, project_id=root)
    assert plan_res.success, plan_res.message
    apply_res = await agents.apply_plan(
        plan_res.data, migrate=False, verify=False, project_id=root
    )
    assert apply_res.success, apply_res.message

    listed = await agents.list_features(project_id=root)
    feature = next(f for f in listed.data["features"] if f["name"] == "blog")
    assert feature["inferred"] is False
    assert feature["has_spec"] is True
    assert load_manifest(root)["features"]["blog"]["spec"] == BLOG


async def test_regenerate_tests_works_on_a_plan_applied_feature(root: Path):
    """The tool that consumes the stored spec, on the path that used to lose it."""
    plan_res = await agents.plan_feature(BLOG, project_id=root)
    assert plan_res.success, plan_res.message
    assert (await agents.apply_plan(
        plan_res.data, migrate=False, verify=False, project_id=root
    )).success

    res = await agents.regenerate_tests("blog", project_id=root)
    assert res.success, res.message


async def test_list_features_rejects_an_unknown_status(built: Path):
    res = await agents.list_features(status="parked", project_id=built)
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert "archived" in res.data["suggestions"]


async def test_a_project_with_no_manifest_still_lists_its_features(built: Path):
    """The backfill path: features are reconstructed from what is on disk."""
    manifest_path(built).unlink()

    res = await agents.list_features(project_id=built)
    assert res.success, res.message
    names = [f["name"] for f in res.data["features"]]
    assert "content" in names  # inferred per app, not per spec
    inferred = next(f for f in res.data["features"] if f["name"] == "content")
    assert inferred["inferred"] is True
    assert inferred["has_spec"] is False
    assert res.data["inferred_count"] >= 1


# ---------------------------------------------------------------------------
# Deactivate: the API goes, the data stays
# ---------------------------------------------------------------------------


async def test_deactivate_removes_the_api_and_keeps_every_model(built: Path):
    models_before = _models_src(built)

    res = await agents.deactivate_feature("blog", verify=False, project_id=built)
    assert res.success, res.message
    assert res.data["status"] == "archived"
    assert "PostViewSet" in res.data["archived"]
    assert "PostSerializer" in res.data["archived"]
    assert res.data["models_retained"] == ["content.Post"]

    assert "PostViewSet" not in _views(built)
    assert "PostSerializer" not in (
        built / "apps" / "content" / "serializers.py"
    ).read_text(encoding="utf-8")
    assert "PostViewSet" not in _urls(built)
    # The guarantee that makes this safe.
    assert _models_src(built) == models_before


async def test_deactivate_generates_no_migration_because_nothing_schema_changed(built: Path):
    res = await agents.make_migrations(project_id=built)
    assert res.success, res.message
    await agents.run_migrations(project_id=built)

    await agents.deactivate_feature("blog", verify=False, project_id=built)

    after = await agents.make_migrations(project_id=built)
    # Nothing to migrate: no DROP TABLE was ever proposed.
    assert after.success, after.message
    assert not after.data.get("created"), after.data


async def test_deactivate_is_not_repeatable(built: Path):
    await agents.deactivate_feature("blog", verify=False, project_id=built)
    again = await agents.deactivate_feature("blog", verify=False, project_id=built)
    assert not again.success
    assert again.data["error_code"] == "feature_archived"


async def test_unknown_feature_suggests_the_real_names(built: Path):
    res = await agents.deactivate_feature("blogg", verify=False, project_id=built)
    assert not res.success
    assert res.data["error_code"] == "feature_not_found"
    assert "blog" in res.data["suggestions"]


# ---------------------------------------------------------------------------
# Activate: exactly what left comes back
# ---------------------------------------------------------------------------


async def test_activate_restores_the_code_verbatim_including_hand_edits(built: Path):
    views = built / "apps" / "content" / "views.py"
    edited = _views(built).replace(
        "class PostViewSet(ModelViewSet):",
        "class PostViewSet(ModelViewSet):\n    # hand-edited, must survive",
    )
    views.write_text(edited, encoding="utf-8")
    assert "hand-edited" in _views(built)

    await agents.deactivate_feature("blog", verify=False, project_id=built)
    assert "PostViewSet" not in _views(built)

    res = await agents.activate_feature("blog", verify=False, project_id=built)
    assert res.success, res.message
    assert res.data["status"] == "active"
    assert "PostViewSet" in res.data["restored"]
    assert res.data["rebuilt"] is False

    restored = _views(built)
    assert "class PostViewSet(ModelViewSet):" in restored
    assert "hand-edited, must survive" in restored
    assert "PostViewSet" in _urls(built)


async def test_activate_refuses_a_feature_that_is_already_active(built: Path):
    res = await agents.activate_feature("blog", verify=False, project_id=built)
    assert not res.success
    assert res.data["error_code"] == "feature_active"


async def test_activate_rebuilds_from_the_stored_spec_when_the_archive_is_gone(built: Path):
    await agents.deactivate_feature("blog", verify=False, project_id=built)
    # Lose the fragments but keep the record — a damaged archive.
    for fragment in (built / ".zeeb" / "archive" / "blog").rglob("*.py"):
        fragment.unlink()

    res = await agents.activate_feature(
        "blog", migrate=False, verify=False, project_id=built
    )
    assert res.success, res.message
    assert res.data["rebuilt"] is True
    assert "PostViewSet" in _views(built)


async def test_activate_without_archive_or_spec_says_so(built: Path):
    await agents.deactivate_feature("blog", verify=False, project_id=built)
    import shutil

    shutil.rmtree(built / ".zeeb" / "archive" / "blog")
    manifest = load_manifest(built)
    del manifest["features"]["blog"]["spec"]
    manifest_path(built).write_text(json.dumps(manifest), encoding="utf-8")

    res = await agents.activate_feature("blog", verify=False, project_id=built)
    assert not res.success
    assert res.data["error_code"] == "archive_missing"


# ---------------------------------------------------------------------------
# Shared apps
# ---------------------------------------------------------------------------


async def test_two_features_share_an_app_without_owning_each_others_artifacts(root: Path):
    assert (await agents.build_feature(
        BLOG, migrate=False, verify=False, project_id=root
    )).success
    assert (await agents.build_feature(
        NOTES, migrate=False, verify=False, project_id=root
    )).success

    features = load_manifest(root)["features"]
    assert features["blog"]["artifacts"]["models"] == ["content.Post"]
    assert features["notes"]["artifacts"]["models"] == ["content.Note"]

    res = await agents.deactivate_feature("blog", verify=False, project_id=root)
    assert res.success, res.message

    views = _views(root)
    assert "PostViewSet" not in views
    # The app-mate is untouched and still served.
    assert "NoteViewSet" in views
    assert "NoteViewSet" in _urls(root)
    assert "class Post(" in _models_src(root)
    assert "class Note(" in _models_src(root)


# ---------------------------------------------------------------------------
# change_feature by name
# ---------------------------------------------------------------------------


async def test_change_feature_by_name_updates_the_stored_spec(built: Path):
    res = await agents.change_feature(
        [{"operation": "add_field", "entity": "Post",
          "field": {"name": "subtitle", "type": "string", "required": False}}],
        feature="blog",
        migrate=False,
        verify=False,
        project_id=built,
    )
    assert res.success, res.message

    spec = load_manifest(built)["features"]["blog"]["spec"]
    names = [f["name"] for f in spec["entities"][0]["fields"]]
    assert "subtitle" in names, "the stored spec must not rot as the feature changes"


async def test_change_feature_refuses_an_archived_feature(built: Path):
    await agents.deactivate_feature("blog", verify=False, project_id=built)
    res = await agents.change_feature(
        [{"operation": "add_field", "entity": "Post",
          "field": {"name": "subtitle", "type": "string"}}],
        feature="blog",
        migrate=False,
        verify=False,
        project_id=built,
    )
    assert not res.success
    assert res.data["error_code"] == "feature_archived"


async def test_edit_feature_is_change_feature(built: Path):
    res = await agents.edit_feature(
        [{"operation": "set_authentication", "entity": "Post",
          "authentication": "required"}],
        feature="blog",
        migrate=False,
        verify=False,
        project_id=built,
    )
    assert res.success, res.message


# ---------------------------------------------------------------------------
# Delete: the destructive one
# ---------------------------------------------------------------------------


async def test_delete_refuses_without_confirmation_and_reports_the_scope(built: Path):
    res = await agents.delete_feature("blog", project_id=built)
    assert not res.success
    assert res.data["confirm_required"] is True
    assert res.data["would_delete"]["models"] == ["content.Post"]
    assert res.data["risk"]["level"] == "high"
    # Nothing moved.
    assert "class Post(" in _models_src(built)
    assert "PostViewSet" in _views(built)


async def test_delete_removes_the_models_and_forgets_the_feature(built: Path):
    res = await agents.delete_feature(
        "blog", confirm=True, migrate=False, verify=False, project_id=built
    )
    assert res.success, res.message
    assert res.data["models_deleted"] == ["content.Post"]

    assert "class Post(" not in _models_src(built)
    assert "PostViewSet" not in _views(built)
    assert "blog" not in load_manifest(built)["features"]
    # No archive is left: a deleted feature is not restorable.
    assert not (built / ".zeeb" / "archive" / "blog").exists()


async def test_delete_leaves_an_app_mate_alone(root: Path):
    await agents.build_feature(BLOG, migrate=False, verify=False, project_id=root)
    await agents.build_feature(NOTES, migrate=False, verify=False, project_id=root)

    res = await agents.delete_feature(
        "blog", confirm=True, migrate=False, verify=False, project_id=root
    )
    assert res.success, res.message
    assert "class Post(" not in _models_src(root)
    assert "class Note(" in _models_src(root)
    assert "NoteViewSet" in _views(root)


# ---------------------------------------------------------------------------
# The `functions` block: custom logic declared in the spec
# ---------------------------------------------------------------------------

WITH_FUNCTIONS = {
    "name": "shop",
    "entities": [
        {
            "name": "Order",
            "fields": [
                {"name": "total", "type": "decimal"},
                {"name": "status", "type": "enum",
                 "values": ["open", "closed"], "default": "open"},
            ],
            "functions": [
                {"name": "close", "kind": "action", "detail": True,
                 "methods": ["post"], "actor": "admin",
                 "body": "return {\"ok\": True}"},
                {"name": "on_saved", "kind": "hook", "trigger": "post_save"},
            ],
        }
    ],
    "functions": [
        {"name": "revenue", "kind": "endpoint", "path": "/orders/revenue",
         "method": "get", "body": "return {\"total\": 0}"},
        {"name": "nightly_rollup", "kind": "task", "schedule": "0 2 * * *"},
        {"name": "IsShopStaff", "kind": "rule", "logic": "staff_only"},
    ],
}


async def test_functions_block_generates_every_kind(root: Path):
    res = await agents.build_feature(
        WITH_FUNCTIONS, migrate=False, verify=False, project_id=root
    )
    assert res.success, res.message
    assert "5 function(s)" in res.data["summary"]

    app = root / "apps" / "shop"
    views = (app / "views.py").read_text(encoding="utf-8")
    assert "async def close" in views          # action on the endpoint
    assert "async def revenue" in views        # standalone route
    assert "async def on_saved" in (app / "signals.py").read_text(encoding="utf-8")
    assert "async def nightly_rollup" in (app / "tasks.py").read_text(encoding="utf-8")
    assert "class IsShopStaff" in (app / "permissions.py").read_text(encoding="utf-8")

    owned = load_manifest(root)["features"]["shop"]["artifacts"]["functions"]
    kinds = {fn["kind"] for fn in owned}
    assert kinds == {"action", "endpoint", "hook", "task", "rule"}


async def test_a_function_may_not_collide_with_a_workflow_transition(root: Path):
    res = await agents.build_feature(
        {
            "name": "orders",
            "entities": [
                {
                    "name": "Order",
                    "fields": [{"name": "total", "type": "decimal"}],
                    "workflow": {
                        "states": ["draft", "submitted"],
                        "transitions": [
                            {"name": "submit", "from": "draft", "to": "submitted"}
                        ],
                    },
                    "functions": [
                        {"name": "submit", "kind": "action", "body": "return {}"}
                    ],
                }
            ],
        },
        migrate=False,
        verify=False,
        project_id=root,
    )
    assert not res.success
    problems = res.data["problems"]
    assert any(p["code"] == "already_exists" for p in problems), problems


async def test_unknown_function_kind_suggests_a_real_one(root: Path):
    res = await agents.plan_feature(
        {
            "name": "shop",
            "entities": [{"name": "Order", "fields": [{"name": "total", "type": "int"}]}],
            "functions": [{"name": "revenue", "kind": "endpont", "path": "/x"}],
        },
        project_id=root,
    )
    assert not res.success
    problem = next(p for p in res.data["problems"] if p["path"].endswith(".kind"))
    assert "endpoint" in problem["suggestions"]


async def test_change_feature_adds_and_removes_a_function(root: Path):
    await agents.build_feature(BLOG, migrate=False, verify=False, project_id=root)

    added = await agents.change_feature(
        [{"operation": "add_function", "app": "content",
          "function": {"name": "publish", "kind": "action", "entity": "Post",
                       "body": "return {}"}}],
        feature="blog",
        migrate=False,
        verify=False,
        project_id=root,
    )
    assert added.success, added.message
    assert "async def publish" in _views(root)

    removed = await agents.change_feature(
        [{"operation": "remove_function", "app": "content",
          "function": {"name": "publish", "kind": "action", "entity": "Post"}}],
        feature="blog",
        migrate=False,
        verify=False,
        project_id=root,
    )
    assert removed.success, removed.message
    assert "async def publish" not in _views(root)
    assert "class PostViewSet" in _views(root), "only the method should go"


# ---------------------------------------------------------------------------
# Shared apps keep importing; delete is fail-safe
# ---------------------------------------------------------------------------

# A third feature in the same app whose model points at BLOG's Post.
COMMENTS = {
    "name": "comments",
    "app": "content",
    "entities": [
        {
            "name": "Comment",
            "fields": [
                {"name": "body", "type": "text"},
                {"name": "post", "type": "relation", "target": "Post",
                 "cardinality": "many-to-one"},
            ],
        }
    ],
}


def _import_header(path: Path) -> list[str]:
    """The module-level import statements of *path*, in order, as source."""
    import ast

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [
        ast.get_source_segment(source, node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


async def _import_errors(root: Path) -> list[dict]:
    res = await agents.check_code(project_id=root)
    assert res.success, res.message
    return res.data["errors"]


async def test_deactivate_in_a_shared_app_leaves_no_dangling_import(root: Path):
    for spec in (BLOG, NOTES):
        assert (await agents.build_feature(
            spec, migrate=False, verify=False, project_id=root
        )).success
    app = root / "apps" / "content"
    headers = {name: _import_header(app / name) for name in ("views.py", "serializers.py")}

    res = await agents.deactivate_feature("blog", verify=False, project_id=root)
    assert res.success, res.message
    views = _views(root)
    assert "PostSerializer" not in views and "import Post\n" not in views
    # The project still imports — the app-mate included — and still serves it.
    assert await _import_errors(root) == []
    assert "NoteViewSet" in _urls(root)
    record = json.loads((root / ".zeeb" / "archive" / "blog" / "archive.json").read_text())
    assert any("PostSerializer" in entry["original"] for entry in record["imports_removed"])

    res = await agents.activate_feature("blog", verify=False, project_id=root)
    assert res.success, res.message
    # Every import statement is back, verbatim and in its original place.
    for name, header in headers.items():
        assert _import_header(app / name) == header, name
    assert await _import_errors(root) == []
    assert "PostViewSet" in _urls(root) and "NoteViewSet" in _urls(root)


async def test_deactivate_with_a_relation_from_an_app_mate_keeps_importing(root: Path):
    for spec in (BLOG, COMMENTS):
        assert (await agents.build_feature(
            spec, migrate=False, verify=False, project_id=root
        )).success
    res = await agents.deactivate_feature("blog", verify=False, project_id=root)
    assert res.success, res.message
    assert await _import_errors(root) == []
    assert "CommentViewSet" in _urls(root)
    assert (await agents.activate_feature("blog", verify=False, project_id=root)).success
    assert await _import_errors(root) == []


async def test_deactivate_refuses_when_code_that_stays_uses_its_classes(root: Path):
    for spec in (BLOG, NOTES):
        assert (await agents.build_feature(
            spec, migrate=False, verify=False, project_id=root
        )).success
    views = root / "apps" / "content" / "views.py"
    source = views.read_text(encoding="utf-8")
    views.write_text(
        source.replace(
            "class NoteViewSet(ModelViewSet):\n",
            "class NoteViewSet(ModelViewSet):\n    related_serializer = PostSerializer\n",
        ),
        encoding="utf-8",
    )
    before = {p: p.read_text() for p in (root / "apps" / "content").glob("*.py")}

    res = await agents.deactivate_feature("blog", verify=False, project_id=root)
    assert not res.success
    assert res.data["error_code"] == "feature_in_use"
    assert {"file": "apps/content/views.py", "name": "PostSerializer"} in res.data["dependents"]
    # Refused before anything moved.
    assert {p: p.read_text() for p in before} == before
    assert load_manifest(root)["features"]["blog"]["status"] == "active"


async def test_deactivate_reports_a_failed_verification_as_a_failure(built: Path, monkeypatch):
    import zeeb_agents.intent as intent

    async def broken(_checks, _root, port=8000):
        return {
            "passed": False,
            "checks": {
                "code": {"ok": False, "errors": [{"kind": "import", "file": "x.py",
                                                  "line": 1, "message": "boom"}],
                         "count": 1},
                "openapi": {"ok": False, "paths_count": None, "error": "not reachable"},
            },
        }

    monkeypatch.setattr(intent, "_run_verification", broken)
    res = await agents.deactivate_feature("blog", project_id=built)
    assert not res.success
    assert res.data["error_code"] == "verification_failed"
    assert res.data["verified"] is False
    for key in ("archived", "routes_unregistered", "models_retained", "next_actions"):
        assert key in res.data
    assert load_manifest(built)["features"]["blog"]["status"] == "archived"


async def test_an_unreachable_openapi_server_alone_does_not_fail_deactivate(
    built: Path, monkeypatch
):
    import zeeb_agents.intent as intent

    async def only_offline(_checks, _root, port=8000):
        return {
            "passed": False,
            "checks": {
                "code": {"ok": True, "errors": [], "count": 0},
                "openapi": {"ok": False, "paths_count": None, "error": "not reachable"},
            },
        }

    monkeypatch.setattr(intent, "_run_verification", only_offline)
    res = await agents.deactivate_feature("blog", project_id=built)
    assert res.success, res.message
    assert "error_code" not in res.data


async def test_features_sharing_an_app_get_their_own_test_files(root: Path):
    for spec in (BLOG, NOTES):
        assert (await agents.build_feature(
            spec, migrate=False, verify=False, project_id=root
        )).success
    features = load_manifest(root)["features"]
    blog_tests = features["blog"]["artifacts"]["tests"]
    notes_tests = features["notes"]["artifacts"]["tests"]
    assert blog_tests == ["tests/test_content_feature_blog_generated.py"]
    assert notes_tests == ["tests/test_content_feature_notes_generated.py"]
    assert all((root / path).is_file() for path in (*blog_tests, *notes_tests))

    assert (await agents.deactivate_feature("blog", verify=False, project_id=root)).success
    assert not (root / blog_tests[0]).exists()
    assert (root / notes_tests[0]).is_file()


async def test_a_legacy_shared_test_file_stays_with_the_active_app_mate(root: Path):
    from zeeb_agents.feature_manifest import save_manifest

    for spec in (BLOG, NOTES):
        assert (await agents.build_feature(
            spec, migrate=False, verify=False, project_id=root
        )).success
    # A manifest written before per-feature test files: both own one file.
    shared = "tests/test_content_generated.py"
    (root / shared).write_text("def test_shared():\n    pass\n", encoding="utf-8")
    data = load_manifest(root)
    for name in ("blog", "notes"):
        data["features"][name]["artifacts"]["tests"] = [shared]
    save_manifest(root, data)

    res = await agents.deactivate_feature("blog", verify=False, project_id=root)
    assert res.success, res.message
    assert (root / shared).is_file(), "notes is active and owns it too"
    assert res.data["tests_archived"] == []
    assert any(shared in warning for warning in res.data["warnings"])

    res = await agents.delete_feature(
        "notes", confirm=True, migrate=False, verify=False, project_id=root
    )
    assert res.success, res.message
    assert (root / shared).is_file(), "blog (archived) owns it too"


async def test_archive_paths_are_posix_and_backslash_records_still_restore(built: Path):
    assert (await agents.deactivate_feature("blog", verify=False, project_id=built)).success
    record_file = built / ".zeeb" / "archive" / "blog" / "archive.json"
    record = json.loads(record_file.read_text())
    assert all("\\" not in fragment["fragment"] for fragment in record["fragments"])
    assert all("/" in fragment["fragment"] for fragment in record["fragments"])
    # An archive written on Windows by an older version.
    for fragment in record["fragments"]:
        fragment["fragment"] = fragment["fragment"].replace("/", "\\")
        fragment["file"] = fragment["file"].replace("/", "\\")
    record_file.write_text(json.dumps(record))

    res = await agents.activate_feature("blog", verify=False, project_id=built)
    assert res.success, res.message
    assert sorted(res.data["restored"]) == ["PostSerializer", "PostViewSet"]
    assert res.data["missing"] == []
    assert "class PostViewSet" in _views(built)


async def test_delete_in_one_process_after_build_drops_the_table(root: Path):
    """build then delete in one process: the cached models must not hide the delete."""
    res = await agents.build_feature(BLOG, verify=False, project_id=root)
    assert res.success, res.message
    assert "content_post" in (await agents.list_tables(project_id=root)).data["tables"]
    res = await agents.delete_feature("blog", confirm=True, verify=False, project_id=root)
    assert res.success, res.message
    assert res.data["migrations_created"], res.data
    migrations = "".join(p.read_text() for p in (root / "migrations").glob("*.py"))
    assert "DeleteModel" in migrations
    assert "content_post" not in (await agents.list_tables(project_id=root)).data["tables"]


async def test_delete_without_a_drop_migration_keeps_the_archive(root: Path, monkeypatch):
    import zeeb_agents.migrations as migrations

    res = await agents.build_feature(BLOG, verify=False, project_id=root)
    assert res.success, res.message
    # Reproduce the stale-registry state: detection that cannot see the edit.
    monkeypatch.setattr(migrations, "forget_loaded_project_code", lambda _root: [])
    res = await agents.delete_feature("blog", confirm=True, verify=False, project_id=root)
    assert not res.success
    assert res.data["error_code"] == "delete_migration_missing"
    assert res.data["models_not_dropped"] == ["content.Post"]
    entry = load_manifest(root)["features"]["blog"]
    assert entry["status"] == "archived" and entry["delete_pending"] is True
    assert (root / ".zeeb" / "archive" / "blog" / "archive.json").is_file()

    res = await agents.activate_feature("blog", verify=False, project_id=root)
    assert not res.success and res.data["error_code"] == "delete_pending"

    monkeypatch.undo()
    made = await agents.make_migrations(project_id=root)
    assert made.success and made.data["created"], made.data
    assert (await agents.run_migrations(project_id=root)).success
    res = await agents.delete_feature("blog", confirm=True, verify=False, project_id=root)
    assert res.success, res.message
    assert "blog" not in load_manifest(root)["features"]
    assert not (root / ".zeeb" / "archive" / "blog").exists()


async def test_delete_refuses_a_model_another_feature_points_at(root: Path):
    for spec in (BLOG, COMMENTS):
        assert (await agents.build_feature(
            spec, migrate=False, verify=False, project_id=root
        )).success
    before = {p: p.read_text() for p in (root / "apps" / "content").glob("*.py")}

    res = await agents.delete_feature(
        "blog", confirm=True, migrate=False, verify=False, project_id=root
    )
    assert not res.success
    assert res.data["error_code"] == "model_referenced"
    assert res.data["references"] == [
        {"model": "content.Comment", "field": "post", "target": "content.Post"}
    ]
    assert "content.Comment" in res.message
    assert {p: p.read_text() for p in before} == before
    assert load_manifest(root)["features"]["blog"]["status"] == "active"
