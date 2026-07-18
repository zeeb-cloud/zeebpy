"""End-to-end tests for the intent workflow functions (``zeeb_agents.intent``).

Runs against a real scaffolded project (same fixture style as
``test_agent_seamless.py``): plan_feature writes nothing, build_feature and
apply_plan produce identical trees through the shared executor, re-runs are
idempotent, and bootstrap/configure_auth/verify/diagnose report the intent
envelope.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import zeeb_agents as agents

SPEC = {
    "name": "blog",
    "entities": [
        {
            "name": "Post",
            "fields": [
                {"name": "title", "type": "string", "max_length": 200},
                {"name": "status", "type": "enum", "values": ["draft", "published"],
                 "default": "draft"},
                {"name": "category", "type": "relation", "target": "Category",
                 "required": False},
            ],
        },
        {"name": "Category", "fields": [{"name": "name", "type": "string"}]},
    ],
}


@pytest.fixture(autouse=True)
def _isolate_global_state():
    """Snapshot/restore zeeb_orm's process-global registry and ``apps.*`` imports.

    Mirrors the fixture in ``test_agent_seamless.py`` — migration-running
    intent calls import scaffolded models into the flat global registry.
    """
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


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


# ---------------------------------------------------------------------------
# plan_feature
# ---------------------------------------------------------------------------


async def test_plan_feature_writes_nothing(root: Path):
    before = _tree_snapshot(root)
    res = await agents.plan_feature(SPEC, project_id=root)
    assert res.success, res.message
    assert _tree_snapshot(root) == before
    assert res.data["state_changed"] is False
    assert res.data["plan_version"] == 1
    kinds = [op["op"] for op in res.data["operations"]]
    assert "create_app" in kinds and "create_model" in kinds
    # Relation ordering: Category before Post.
    models = [op["model"] for op in res.data["operations"] if op["op"] == "create_model"]
    assert models == ["Category", "Post"]


async def test_plan_feature_invalid_spec_reports_all_problems(root: Path):
    res = await agents.plan_feature(
        {"name": "blog", "entities": [
            {"name": "Post", "fields": [
                {"name": "a", "type": "nope"},
                {"name": "b", "type": "relation", "target": "Missing"},
            ]},
        ]},
        project_id=root,
    )
    assert not res.success
    assert len(res.data["problems"]) == 2
    assert res.data["error_code"] == "invalid_field_type"


# ---------------------------------------------------------------------------
# build_feature / apply_plan
# ---------------------------------------------------------------------------


async def test_build_feature_scaffolds_everything(root: Path):
    res = await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)
    assert res.success, res.message
    data = res.data
    assert data["changes"]["apps_created"] == ["blog"]
    assert data["changes"]["models_created"] == ["blog.Category", "blog.Post"]
    assert data["changes"]["endpoints_created"] == ["categories", "posts"]
    assert data["state_changed"] is True

    models = (root / "apps" / "blog" / "models.py").read_text()
    assert "class Post(Model):" in models
    assert 'choices=[["draft", "draft"], ["published", "published"]]' in models
    assert "created_at = fields.DateTimeField(auto_now_add=True)" in models
    urls = (root / "apps" / "blog" / "urls.py").read_text()
    assert 'router.register("posts", PostViewSet)' in urls
    assert 'router.register("categories", CategoryViewSet)' in urls
    assert "import" in (root / "apps" / "blog" / "serializers.py").read_text()


async def test_build_feature_is_idempotent(root: Path):
    first = await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)
    assert first.success
    second = await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)
    assert second.success, second.message
    assert second.data["changes"]["models_created"] == []
    assert second.data["changes"]["endpoints_created"] == []
    assert second.data["state_changed"] is False
    assert any("already exists" in w for w in second.data["warnings"])


async def test_build_feature_with_migrate_applies_migrations(root: Path):
    res = await agents.build_feature(SPEC, project_id=root, verify=False)
    assert res.success, res.message
    assert res.data["changes"]["migrations_created"]
    assert res.data["changes"]["migrations_applied"]
    status = await agents.get_migration_status(project_id=root)
    assert status.data["pending_count"] == 0


async def test_apply_plan_equals_build_feature(root: Path, tmp_path: Path):
    plan_res = await agents.plan_feature(SPEC, project_id=root)
    assert plan_res.success
    apply_res = await agents.apply_plan(
        plan_res.data, migrate=False, verify=False, project_id=root
    )
    assert apply_res.success, apply_res.message
    applied_tree = _tree_snapshot(root)

    other = await agents.create_project("demo2", directory=str(tmp_path))
    assert other.success
    other_root = tmp_path / "demo2"
    build_res = await agents.build_feature(
        SPEC, migrate=False, verify=False, project_id=other_root
    )
    assert build_res.success
    built_tree = _tree_snapshot(other_root)

    # Identical generated content module-for-module (project name differs only
    # in the scaffold package directory, which carries no generated feature code).
    for rel in ("apps/blog/models.py", "apps/blog/serializers.py",
                "apps/blog/views.py", "apps/blog/urls.py"):
        assert applied_tree[rel] == built_tree[rel]
    assert apply_res.data["changes"] == build_res.data["changes"]


async def test_apply_plan_rejects_malformed_plans(root: Path):
    res = await agents.apply_plan({"plan_version": 42}, project_id=root)
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert "plan_feature" in res.message


async def test_build_feature_verification_envelope(root: Path):
    res = await agents.build_feature(SPEC, project_id=root)  # verify=True default
    assert res.success, res.message
    verification = res.data["verification"]
    assert set(verification["checks"]) == {"structure", "migrations", "openapi"}
    assert verification["checks"]["structure"]["ok"] is True
    assert verification["checks"]["migrations"]["ok"] is True
    # No live server in tests — the openapi check reports, not raises.
    assert verification["checks"]["openapi"]["ok"] is False
    assert verification["passed"] is False
    assert any("OpenAPI" in a or "reachable" in a for a in res.data["next_actions"])


# ---------------------------------------------------------------------------
# change_feature
# ---------------------------------------------------------------------------


async def test_change_feature_add_and_remove_fields(root: Path):
    assert (await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)).success
    res = await agents.change_feature(
        [
            {"operation": "add_field", "entity": "Post",
             "field": {"name": "subtitle", "type": "string", "required": False}},
            {"operation": "add_relation", "entity": "Category",
             "field": {"name": "parent", "target": "self", "required": False}},
            {"operation": "remove_field", "entity": "Post", "field_name": "status"},
        ],
        migrate=False,
        verify=False,
        project_id=root,
    )
    assert res.success, res.message
    assert res.data["changes"]["fields_added"] == ["Post.subtitle", "Category.parent"]
    assert res.data["changes"]["fields_removed"] == ["Post.status"]
    models = (root / "apps" / "blog" / "models.py").read_text()
    assert "subtitle" in models and "status" not in models
    assert 'parent = fields.ForeignKey("self"' in models


async def test_change_feature_add_entity(root: Path):
    assert (await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)).success
    res = await agents.change_feature(
        [{"operation": "add_entity", "app": "blog",
          "entity": {"name": "Comment", "fields": [
              {"name": "body", "type": "text"},
              {"name": "post", "type": "relation", "target": "Post"},
          ]}}],
        migrate=False,
        verify=False,
        project_id=root,
    )
    assert res.success, res.message
    assert res.data["changes"]["models_created"] == ["blog.Comment"]
    assert res.data["changes"]["endpoints_created"] == ["comments"]


async def test_change_feature_add_field_is_idempotent(root: Path):
    """Re-adding an existing field is a tolerated no-op, not a duplicate line.

    Regression: add_field had no duplicate guard, so the executor's
    ``already_exists`` tolerance was dead code and every re-run appended a second
    ``subtitle = fields.CharField(...)`` line to the model.
    """
    assert (await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)).success
    add = {
        "operation": "add_field",
        "entity": "Post",
        "field": {"name": "subtitle", "type": "string", "required": False},
    }
    first = await agents.change_feature([add], migrate=False, verify=False, project_id=root)
    assert first.success, first.message
    second = await agents.change_feature([add], migrate=False, verify=False, project_id=root)
    assert second.success, second.message  # tolerated, not a partial failure
    models = (root / "apps" / "blog" / "models.py").read_text()
    assert models.count("subtitle = ") == 1  # exactly one definition


async def test_build_feature_succeeds_despite_unregistered_app_warning(root: Path):
    """A project-global make_migrations warning (an unrelated app not in
    INSTALLED_APPS) must surface under ``warnings`` and must NOT flip a fully
    successful build to ``success=False``.
    """
    orphan = root / "apps" / "orphan"
    orphan.mkdir(parents=True)
    (orphan / "__init__.py").write_text("")
    (orphan / "models.py").write_text(
        "from zeeb_orm import Model, fields\n\n"
        "class Widget(Model):\n    name = fields.CharField(max_length=20)\n"
    )
    res = await agents.build_feature(SPEC, project_id=root, verify=False)  # migrate=True default
    assert res.success, res.message
    assert res.data["warnings"]
    assert any("make_migrations" in w for w in res.data["warnings"])


async def test_change_feature_invalid_changes_write_nothing(root: Path):
    assert (await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)).success
    before = _tree_snapshot(root)
    res = await agents.change_feature(
        [{"operation": "add_field", "entity": "Nope",
          "field": {"name": "x", "type": "string"}}],
        project_id=root,
    )
    assert not res.success
    assert res.data["error_code"] == "model_not_found"
    assert _tree_snapshot(root) == before


# ---------------------------------------------------------------------------
# bootstrap_project / configure_auth
# ---------------------------------------------------------------------------


async def test_bootstrap_project_full(root: Path):
    res = await agents.bootstrap_project(project_id=root)
    assert res.success, res.message
    data = res.data
    assert "accounts" in data["changes"]["apps_created"]
    assert "accounts.User" in data["changes"]["models_created"]
    assert data["changes"]["migrations_applied"]
    settings = (root / "demo" / "settings.py").read_text()
    assert 'AUTH_USER_MODEL = "accounts.User"' in settings
    assert "JWTAuthMiddleware" in settings
    urls = (root / "demo" / "urls.py").read_text()
    assert "create_auth_router" in urls
    assert (root / "health.py").exists()
    assert data["verification"]["checks"]["migrations"]["ok"] is True
    assert any("JWT_SECRET_KEY" in a for a in data["next_actions"])


async def test_bootstrap_project_reruns_idempotently(root: Path):
    assert (await agents.bootstrap_project(project_id=root)).success
    res = await agents.bootstrap_project(project_id=root)
    assert res.success, res.message
    assert res.data["changes"]["models_created"] == []
    assert any("AUTH_USER_MODEL" in w for w in res.data["warnings"])


async def test_bootstrap_after_migrations_keeps_default_user_model(root: Path):
    assert (await agents.build_feature(SPEC, project_id=root, verify=False)).success
    res = await agents.bootstrap_project(project_id=root)
    assert res.success, res.message
    assert "accounts.User" not in res.data["changes"]["models_created"]
    assert any("must precede" in w for w in res.data["warnings"])


async def test_configure_auth_with_oauth_provider(root: Path):
    res = await agents.configure_auth(
        providers=[{"type": "password"}, {"type": "oauth", "provider": "google"}],
        project_id=root,
    )
    assert res.success, res.message
    settings = (root / "demo" / "settings.py").read_text()
    assert "OAUTH_PROVIDERS" in settings and '"google"' in settings
    assert any("GOOGLE_CLIENT_ID" in a for a in res.data["next_actions"])


async def test_configure_auth_unknown_provider_fails_up_front(root: Path):
    before = _tree_snapshot(root)
    res = await agents.configure_auth(
        providers=[{"type": "oauth", "provider": "gogle"}], project_id=root
    )
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert "google" in res.data["suggestions"]
    assert _tree_snapshot(root) == before


# ---------------------------------------------------------------------------
# verify_project / diagnose_problem
# ---------------------------------------------------------------------------


async def test_verify_project_reports_verdict_without_failing(root: Path):
    assert (await agents.build_feature(SPEC, project_id=root, verify=False)).success
    res = await agents.verify_project(checks=["structure", "migrations"], project_id=root)
    assert res.success, res.message
    assert res.data["verification"]["passed"] is True
    assert res.data["next_actions"] == []
    assert res.data["state_changed"] is False


async def test_verify_project_flags_pending_migrations(root: Path):
    assert (await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)).success
    mk = await agents.make_migrations(project_id=root)
    assert mk.success and mk.data["created"]
    res = await agents.verify_project(checks=["migrations"], project_id=root)
    assert res.success
    assert res.data["verification"]["passed"] is False
    assert any("migration" in a.lower() for a in res.data["next_actions"])


async def test_verify_project_rejects_unknown_checks(root: Path):
    res = await agents.verify_project(checks=["structure", "openapy"], project_id=root)
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert "openapi" in res.data["suggestions"]


async def test_diagnose_problem_finds_pending_migrations(root: Path):
    assert (await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)).success
    mk = await agents.make_migrations(project_id=root)
    assert mk.success and mk.data["created"]
    res = await agents.diagnose_problem(symptom="POST /posts returns 500", project_id=root)
    assert res.success, res.message
    assert res.data["root_cause"]["type"] == "migrations_pending"
    assert res.data["recommended_fix"] == {"tool": "run_migrations", "arguments": {}}
    assert res.data["state_changed"] is False


async def test_diagnose_problem_flags_unregistered_endpoint(root: Path):
    assert (await agents.build_feature(SPEC, project_id=root, verify=False)).success
    res = await agents.diagnose_problem(endpoint="/invoices", project_id=root)
    assert res.success
    assert res.data["root_cause"]["type"] == "route_not_registered"
    assert any(f["area"] == "routing" for f in res.data["findings"])


async def test_diagnose_problem_healthy_project_is_inconclusive(root: Path):
    assert (await agents.build_feature(SPEC, project_id=root, verify=False)).success
    res = await agents.diagnose_problem(project_id=root)
    assert res.success
    assert res.data["root_cause"] is None
