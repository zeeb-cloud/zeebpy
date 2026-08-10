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
from zeeb_agents._utils.code_gen import extract_field_types

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
    assert res.data["plan_version"] == 2
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
    # tests=True (default) generated a suite, so the tests check auto-joins.
    assert set(verification["checks"]) == {"structure", "migrations", "openapi", "tests"}
    assert verification["checks"]["tests"]["ok"] is True
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
    # The scaffold already ships the accounts app and its user model, so
    # bootstrap has nothing to create there and says so.
    assert data["changes"]["apps_created"] == []
    assert data["changes"]["models_created"] == []
    assert any("AUTH_USER_MODEL" in w for w in data["warnings"])
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
    # Never swap the user model out from under an existing schema: the project
    # already has one from the scaffold, and bootstrap leaves it alone.
    assert "accounts.User" not in res.data["changes"]["models_created"]
    assert any("AUTH_USER_MODEL" in w for w in res.data["warnings"])


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


async def test_verify_project_runtime_check_reports_health(root: Path):
    assert (await agents.build_feature(SPEC, project_id=root, verify=False)).success
    res = await agents.verify_project(checks=["runtime"], project_id=root)
    assert res.success, res.message
    runtime = res.data["verification"]["checks"]["runtime"]
    assert runtime["settings"] == "ok"
    assert runtime["db"] == "ok"
    assert runtime["ok"] is True


async def test_verify_project_security_check_flags_dev_posture(root: Path):
    """A freshly scaffolded project is deliberately not production-ready."""
    res = await agents.verify_project(checks=["security"], project_id=root)
    assert res.success, res.message
    security = res.data["verification"]["checks"]["security"]
    assert security["ok"] is False
    assert security["issues"]
    assert any("production-readiness" in a for a in res.data["next_actions"])


async def test_verify_project_endpoints_check_fails_when_nothing_is_serving(root: Path):
    """The smoke check must fail loudly when the API is not reachable.

    ``openapi`` proves a route is documented; this proves it responds. With no
    runtime up, every request errors and the check must not pass.
    """
    assert (await agents.build_feature(SPEC, project_id=root, verify=False)).success
    # Port 9 (discard) is reserved and never serves HTTP.
    res = await agents.verify_project(checks=["endpoints"], port=9, project_id=root)
    assert res.success, res.message
    endpoints = res.data["verification"]["checks"]["endpoints"]
    assert endpoints["ok"] is False
    assert endpoints.get("failures")
    assert any("unreachable" in a or "server error" in a for a in res.data["next_actions"])


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


# ---------------------------------------------------------------------------
# Plan hardening: risk recompute, v1 acceptance, staleness fingerprint
# ---------------------------------------------------------------------------


async def test_apply_plan_recomputes_tampered_risk(root: Path):
    res = await agents.plan_feature(SPEC, project_id=root)
    assert res.success, res.message
    plan = dict(res.data)
    plan["risk"] = {"level": "low", "destructive": False, "database_changes": False}
    applied = await agents.apply_plan(plan, migrate=False, verify=False, project_id=root)
    assert applied.success, applied.message
    assert any("plan.risk did not match" in w for w in applied.data["warnings"])


async def test_apply_plan_accepts_v1_plan_with_warning(root: Path):
    res = await agents.plan_feature(SPEC, project_id=root)
    assert res.success, res.message
    plan = {k: v for k, v in res.data.items() if k not in ("preconditions", "state_changed")}
    plan["plan_version"] = 1
    applied = await agents.apply_plan(plan, migrate=False, verify=False, project_id=root)
    assert applied.success, applied.message
    assert any("older planner" in w for w in applied.data["warnings"])


async def test_apply_plan_warns_when_project_state_changed(root: Path):
    res = await agents.plan_feature(SPEC, project_id=root)
    assert res.success, res.message
    plan = res.data
    # Create one of the planned models after the plan was compiled.
    build = await agents.build_feature(
        {"name": "blog", "entities": [
            {"name": "Category", "fields": [{"name": "name", "type": "string"}]}
        ]},
        migrate=False, verify=False, project_id=root,
    )
    assert build.success, build.message
    applied = await agents.apply_plan(plan, migrate=False, verify=False, project_id=root)
    assert applied.success, applied.message
    assert any("Plan is stale" in w for w in applied.data["warnings"])
    assert any("plan_feature" in a for a in applied.data["next_actions"])


async def test_apply_plan_rejects_hand_built_op_payloads(root: Path):
    applied = await agents.apply_plan(
        {"plan_version": 2, "operations": [{"op": "remove_field", "app": "blog"}]},
        project_id=root,
    )
    assert not applied.success
    assert applied.data["error_code"] == "invalid_input"
    assert "missing required key" in applied.message


# ---------------------------------------------------------------------------
# Recovery contract: affected scope on mutating envelopes
# ---------------------------------------------------------------------------


async def test_intent_envelopes_report_affected_scope(root: Path):
    planned = await agents.plan_feature(SPEC, project_id=root)
    assert planned.success, planned.message
    affected = planned.data["affected"]
    assert affected["apps"] == ["blog"]
    assert set(affected["entities"]) == {"blog.Post", "blog.Category"}
    assert "apps/blog/models.py" in affected["files"]
    assert "apps/blog/urls.py" in affected["files"]
    assert "migrations" in affected["files"]

    built = await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)
    assert built.success, built.message
    assert built.data["affected"] == affected


async def test_partial_failure_still_reports_affected(root: Path, monkeypatch):
    async def _boom(*args, **kwargs):
        from zeeb_agents._utils.errors import fail

        return fail("disk full", code="invalid_input")

    monkeypatch.setattr("zeeb_agents.serializers.create_serializer", _boom)
    built = await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)
    assert not built.success
    assert built.data["affected"]["apps"] == ["blog"]
    assert built.data["errors"]


# ---------------------------------------------------------------------------
# Workflows end-to-end (build → files on disk → idempotent re-run)
# ---------------------------------------------------------------------------

WORKFLOW_SPEC = {
    "name": "orders",
    "api": {"authentication": "public"},
    "entities": [
        {
            "name": "Order",
            "fields": [{"name": "total", "type": "decimal"}],
            "workflow": {
                "states": ["draft", "submitted", "approved"],
                "transitions": [
                    {"name": "submit", "from": "draft", "to": "submitted"},
                    {"name": "approve", "from": ["submitted"], "to": "approved",
                     "permission": "IsAdminUser"},
                ],
            },
        }
    ],
}


async def test_build_feature_with_workflow_e2e(root: Path):
    import ast

    res = await agents.build_feature(WORKFLOW_SPEC, verify=False, project_id=root)
    assert res.success, res.message
    assert res.data["changes"]["actions_created"] == ["Order.submit", "Order.approve"]

    models = (root / "apps" / "orders" / "models.py").read_text()
    assert "status" in models and "draft" in models

    views = (root / "apps" / "orders" / "views.py").read_text()
    ast.parse(views)
    assert "async def submit(self, request, pk=None):" in views
    assert "async def approve(self, request, pk=None):" in views
    assert "from zeeb_api.exceptions import ResourceConflictException" in views
    assert 'obj.status not in ("draft",)' in views
    assert "permission_classes=[permissions.IsAdminUser]" in views or "IsAdminUser" in views

    # Idempotent re-run: nothing duplicated, actions reported as skips.
    again = await agents.build_feature(WORKFLOW_SPEC, verify=False, project_id=root)
    assert again.success, again.message
    assert again.data["changes"]["actions_created"] == []
    assert any("already defined" in s for s in again.data["steps"])
    views_again = (root / "apps" / "orders" / "views.py").read_text()
    assert views_again.count("async def submit(") == 1


async def test_change_feature_add_transition_e2e(root: Path):
    res = await agents.build_feature(WORKFLOW_SPEC, verify=False, project_id=root)
    assert res.success, res.message
    changed = await agents.change_feature(
        [{"operation": "add_transition", "entity": "Order",
          "transition": {"name": "cancel", "from": ["draft", "submitted"],
                          "to": "cancelled"}}],
        verify=False,
        project_id=root,
    )
    assert changed.success, changed.message
    assert changed.data["changes"]["actions_created"] == ["Order.cancel"]
    views = (root / "apps" / "orders" / "views.py").read_text()
    assert "async def cancel(self, request, pk=None):" in views


# ---------------------------------------------------------------------------
# Generated tests: build writes them, verification gates on them, they PASS
# ---------------------------------------------------------------------------


async def test_build_feature_generates_tests_and_verification_gates_on_them(root: Path):
    res = await agents.build_feature(SPEC, project_id=root)
    assert res.success, res.message
    created = res.data["changes"]["tests_created"]
    # The shared harness ships with the project now, so build_feature only adds
    # the feature's own file — but the fixtures it needs are already on disk.
    assert "tests/test_blog_generated.py" in created
    assert (root / "tests" / "conftest.py").exists()

    # The default verification chain auto-included the tests check — and the
    # generated suite actually passes against the scaffolded feature.
    checks = res.data["verification"]["checks"]
    assert "tests" in checks
    assert checks["tests"]["ok"], checks["tests"]["summary"]

    # Idempotent re-run: no files rewritten, user edits survive.
    marker = "# user edited\n"
    test_file = root / "tests" / "test_blog_generated.py"
    original = test_file.read_text()
    test_file.write_text(original + marker)
    again = await agents.build_feature(SPEC, verify=False, project_id=root)
    assert again.success, again.message
    assert again.data["changes"]["tests_created"] == []
    assert test_file.read_text().endswith(marker)

    # Opt-out leaves no generate_tests op in the plan.
    planned = await agents.plan_feature(SPEC, tests=False, project_id=root)
    assert all(op["op"] != "generate_tests" for op in planned.data["operations"])


async def test_generated_workflow_transition_test_passes_live(root: Path):
    res = await agents.build_feature(WORKFLOW_SPEC, verify=False, project_id=root)
    assert res.success, res.message
    generated = (root / "tests" / "test_orders_generated.py").read_text()
    assert "test_order_submit_transition_conflict" in generated

    run = await agents.run_tests(project_id=root)
    assert run.success, (run.data or {}).get("output", run.message)
    assert (run.data or {}).get("failed") == 0
    assert (run.data or {}).get("passed", 0) > 0


# ---------------------------------------------------------------------------
# Convergence: re-running build_feature with an extended spec
# ---------------------------------------------------------------------------


async def test_build_feature_rerun_converges_new_spec_fields(root: Path):
    res = await agents.build_feature(SPEC, verify=False, project_id=root)
    assert res.success, res.message

    extended = {
        **SPEC,
        "entities": [
            {**e, "fields": [*e["fields"], {"name": "subtitle", "type": "string"}]}
            if e["name"] == "Post"
            else e
            for e in SPEC["entities"]
        ],
    }
    res = await agents.build_feature(extended, verify=False, project_id=root)
    assert res.success, res.message
    assert res.data["changes"]["fields_added"] == ["Post.subtitle"]
    assert res.data["state_changed"] is True

    models = (root / "apps" / "blog" / "models.py").read_text()
    assert "subtitle = fields.CharField" in models
    # The serializer must list it too, or writes are silently discarded.
    serializers = (root / "apps" / "blog" / "serializers.py").read_text()
    assert '"subtitle"' in serializers
    # And a third run changes nothing.
    res = await agents.build_feature(extended, verify=False, project_id=root)
    assert res.success and res.data["state_changed"] is False


async def test_build_feature_rerun_reports_drift_without_applying(root: Path):
    assert (await agents.build_feature(SPEC, verify=False, project_id=root)).success
    shrunk = {
        **SPEC,
        "entities": [
            {**e, "fields": [f for f in e["fields"] if f["name"] != "title"]}
            if e["name"] == "Post"
            else e
            for e in SPEC["entities"]
        ],
    }
    res = await agents.build_feature(shrunk, verify=False, project_id=root)
    assert res.success, res.message
    drift = res.data["drift"]
    assert {"entity": "blog.Post", "field": "title", "kind": "missing_from_spec"} in drift[
        "entries"
    ]
    assert drift["suggested_changes"] == [
        {"operation": "remove_field", "entity": "Post", "app": "blog", "field_name": "title"}
    ]
    # Reported only — the field is still there.
    assert "title = fields.CharField" in (root / "apps" / "blog" / "models.py").read_text()
    assert any("drift" in action for action in res.data["next_actions"])

    # And the suggested payload is directly sendable.
    res = await agents.change_feature(
        drift["suggested_changes"], migrate=False, verify=False, project_id=root
    )
    assert res.success, res.message
    models = (root / "apps" / "blog" / "models.py").read_text()
    assert "title" not in extract_field_types(models, "Post")


# ---------------------------------------------------------------------------
# New change_feature operations
# ---------------------------------------------------------------------------


async def test_change_feature_alter_field_rewrites_the_definition(root: Path):
    assert (await agents.build_feature(SPEC, verify=False, project_id=root)).success
    res = await agents.change_feature(
        [{"operation": "alter_field", "entity": "Post",
          "field": {"name": "title", "type": "text"}}],
        migrate=False, verify=False, project_id=root,
    )
    assert res.success, res.message
    assert res.data["changes"]["fields_altered"] == ["Post.title"]
    models = (root / "apps" / "blog" / "models.py").read_text()
    assert "title = fields.TextField" in models
    # Scoped to the class — the module docstring shows an example model whose
    # fields are not definitions.
    assert extract_field_types(models, "Post")["title"] == "TextField"


async def test_change_feature_remove_entity_leaves_the_app_importable(root: Path):
    assert (await agents.build_feature(SPEC, verify=False, project_id=root)).success
    res = await agents.change_feature(
        [{"operation": "remove_entity", "entity": "Post"}],
        migrate=False, verify=False, project_id=root,
    )
    assert res.success, res.message
    assert res.data["changes"]["models_removed"] == ["blog.Post"]

    app_dir = root / "apps" / "blog"
    models = (app_dir / "models.py").read_text()
    serializers = (app_dir / "serializers.py").read_text()
    views = (app_dir / "views.py").read_text()
    urls = (app_dir / "urls.py").read_text()
    assert "class Post(" not in models
    assert "PostSerializer" not in serializers
    assert "PostViewSet" not in views
    # The imports must go with the classes, or the module raises at startup and
    # takes every other endpoint in the app down with it.
    assert "import Post" not in views and "PostSerializer" not in views
    assert "PostViewSet" not in urls
    # Category is untouched.
    assert "class Category(" in models and "CategoryViewSet" in views


async def test_change_feature_skips_migrations_for_non_schema_changes(root: Path):
    assert (await agents.build_feature(SPEC, verify=False, project_id=root)).success
    res = await agents.change_feature(
        [{"operation": "set_permissions", "entity": "Post",
          "permissions": ["IsAdminUser"]}],
        verify=False, project_id=root,
    )
    assert res.success, res.message
    assert res.data["changes"]["migrations_created"] == []
    assert any("migration steps were omitted" in w for w in res.data["warnings"])
    assert "IsAdminUser" in (root / "apps" / "blog" / "views.py").read_text()


async def test_change_feature_add_entity_writes_a_per_entity_test_file(root: Path):
    assert (await agents.build_feature(SPEC, verify=False, project_id=root)).success
    assert (root / "tests" / "test_blog_generated.py").exists()
    res = await agents.change_feature(
        [{"operation": "add_entity", "app": "blog",
          "entity": {"name": "Comment", "fields": [{"name": "body", "type": "text"}]}}],
        migrate=False, verify=False, project_id=root,
    )
    assert res.success, res.message
    # The app-level file already exists and is never overwritten, so the new
    # entity's tests need their own file to be written at all.
    assert (root / "tests" / "test_blog_comment_generated.py").exists()


# ---------------------------------------------------------------------------
# Verification honesty and partial-failure resume metadata
# ---------------------------------------------------------------------------


async def test_verification_fails_loudly_when_no_tests_are_collected(root: Path):
    assert (
        await agents.build_feature(SPEC, tests=False, verify=False, project_id=root)
    ).success
    # Strip every test the scaffold ships so pytest collects nothing — the
    # shape a broken import produces, which must not read as a pass.
    for shipped in (root / "tests").glob("test_*.py"):
        shipped.unlink()
    res = await agents.verify_project(checks=["tests"], project_id=root)
    assert res.success, res.message
    check = res.data["verification"]["checks"]["tests"]
    assert check["no_tests"] is True
    assert check["ok"] is False
    assert res.data["verified"] is False
    assert any("No tests were collected" in a for a in res.data["next_actions"])


async def test_success_envelope_carries_the_verified_flag(root: Path):
    res = await agents.build_feature(
        SPEC, tests=False, verify=True, project_id=root,
        # structure alone is deterministic offline; openapi needs a live runtime.
    )
    assert res.success, res.message
    assert res.data["verified"] == res.data["verification"]["passed"]
    if not res.data["verified"]:
        assert "BUILT BUT VERIFICATION FAILED" in res.message


async def test_partial_failure_reports_resume_metadata(root: Path, monkeypatch):
    import zeeb_agents.serializers as serializers_mod

    async def boom(*args, **kwargs):
        return agents.AgentResult(
            success=False,
            message="disk on fire",
            data={"error_code": "invalid_input", "recoverable": True},
        )

    monkeypatch.setattr(serializers_mod, "create_serializer", boom)
    res = await agents.build_feature(SPEC, migrate=False, verify=False, project_id=root)
    assert not res.success
    data = res.data
    assert data["error_code"] == "partial_failure"
    assert data["recoverable"] is True
    assert data["verified"] is False

    failed = data["failed_operations"]
    assert failed and all(f["op"]["op"] == "create_serializer" for f in failed)
    assert all(f["error_code"] == "invalid_input" and f["recoverable"] for f in failed)
    # The index locates the failure inside the plan, so a caller can resume
    # instead of guessing from a single state_changed boolean.
    assert [f["index"] for f in failed] == sorted(f["index"] for f in failed)
    assert data["total_count"] == len(
        [op for op in data["remaining_operations"]]
    ) + data["completed_count"] + len(failed)
    assert data["completed_count"] > 0
    assert any("failed_operations" in a for a in data["next_actions"])
