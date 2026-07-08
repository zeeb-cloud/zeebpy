"""Regression tests for bugs found in the 2026-07-08 live MCP e2e test.

Each test pins one concrete failure observed against the hosted platform:
generated code that did not compile, generators clobbering sibling classes,
schema generation yielding empty request models, silent migration failures,
and runtime contract gaps (M2M writes, list route, ORM validation errors).
"""

from __future__ import annotations

import re
from pathlib import Path

import sys

import pytest

import zeeb_agents as agents
from zeeb_agents._utils.code_gen import extract_model_names, render_serializer_class
from zeeb_agents._utils.field_types import render_field_line, validate_field_spec
from zeeb_agents._utils.errors import AgentError
from zeeb_orm.models.base import _model_registry, metadata


@pytest.fixture(autouse=True)
def _isolate_global_state():
    """Snapshot/restore registry, metadata and project-module imports.

    Tests that call ``_register_models`` import ``apps.*`` packages from
    scaffolded tmp projects; without cleanup they leak into other tests.
    """
    registry_before = dict(_model_registry)
    tables_before = set(metadata.tables)
    modules_before = set(sys.modules)
    path_before = list(sys.path)
    yield
    for name in set(_model_registry) - set(registry_before):
        del _model_registry[name]
    for name in set(metadata.tables) - tables_before:
        metadata.remove(metadata.tables[name])
    for name in set(sys.modules) - modules_before:
        if name == "apps" or name.startswith("apps.") or name == "settings":
            del sys.modules[name]
    sys.path[:] = path_before


@pytest.fixture
async def project(tmp_path: Path) -> Path:
    """A real scaffolded project (via create_project) with one app 'blog'."""
    res = await agents.create_project("demo", directory=str(tmp_path))
    assert res.success, res.message
    root = tmp_path / "demo"
    res = await agents.create_app("blog", project_root=root)
    assert res.success, res.message
    return root


# ---------------------------------------------------------------------------
# add_viewset_action: must land inside the real class, not the commented
# example ViewSet from the app scaffold, and must keep its import.
# ---------------------------------------------------------------------------


async def test_add_viewset_action_lands_in_real_class(project: Path):
    assert (await agents.create_model("blog", "Post", [{"name": "title", "type": "string", "max_length": 50}], project_root=project)).success
    assert (await agents.create_serializer("blog", "Post", project_root=project)).success
    assert (await agents.create_viewset("blog", "Post", project_root=project)).success

    res = await agents.add_viewset_action(
        "blog", "Post", "publish", detail=True, methods=["post"], project_root=project
    )
    assert res.success, res.message

    content = (project / "apps" / "blog" / "views.py").read_text()
    # The generated file must stay valid Python (the live bug produced an
    # IndentationError that took the whole app down).
    compile(content, "views.py", "exec")
    # The action must sit inside the real class, i.e. after its definition...
    class_pos = content.index("class PostViewSet")
    action_pos = content.index("async def publish")
    assert action_pos > class_pos
    # ...and not inside the commented example block.
    for line in content.splitlines():
        if "async def publish" in line:
            assert not line.lstrip().startswith("#")
    # The import must survive the write.
    assert "from zeeb_api.viewsets import action" in content


# ---------------------------------------------------------------------------
# update_serializer: scoped to the target class.
# ---------------------------------------------------------------------------


async def test_update_serializer_only_touches_target_class(project: Path):
    for model in ("Post", "Tag"):
        assert (await agents.create_model("blog", model, [{"name": "title", "type": "string", "max_length": 50}], project_root=project)).success
        assert (await agents.create_serializer("blog", model, fields=["id", "title"], project_root=project)).success

    res = await agents.update_serializer(
        "blog", "Post", fields=["id"], read_only_fields=["id"], project_root=project
    )
    assert res.success, res.message

    content = (project / "apps" / "blog" / "serializers.py").read_text()
    tag_block = re.search(
        r"(^class TagSerializer\b.*?)(?=^\S|\Z)", content, re.DOTALL | re.MULTILINE
    ).group(1)
    post_block = re.search(
        r"(^class PostSerializer\b.*?)(?=^\S|\Z)", content, re.DOTALL | re.MULTILINE
    ).group(1)
    assert 'fields = ["id"]' in post_block
    assert 'read_only_fields = ["id"]' in post_block
    # The sibling class keeps its own Meta untouched.
    assert 'fields = ["id", "title"]' in tag_block
    assert "read_only_fields" not in tag_block


# ---------------------------------------------------------------------------
# create_serializer default: fields = "__all__" (bare string, not a list).
# ---------------------------------------------------------------------------


def test_render_serializer_class_all_fields_is_bare_string():
    rendered = render_serializer_class("Post")
    assert 'fields = "__all__"' in rendered
    assert 'fields = ["__all__"]' not in rendered


# ---------------------------------------------------------------------------
# M2M field specs: blank is dropped, unsupported kwargs are rejected.
# ---------------------------------------------------------------------------


def test_m2m_blank_is_dropped_not_rendered():
    line = render_field_line(
        {"name": "tags", "type": "manytomany", "to": "Tag", "blank": True, "related_name": "posts"}
    )
    assert "blank" not in line
    assert 'fields.ManyToManyField("Tag"' in line


def test_m2m_unsupported_kwargs_rejected():
    with pytest.raises(AgentError) as exc:
        validate_field_spec(
            {"name": "tags", "type": "manytomany", "to": "Tag", "unique": True}
        )
    assert "unique" in str(exc.value)


# ---------------------------------------------------------------------------
# zeeb-manage migrate --noinput must parse (Django-compat no-op).
# ---------------------------------------------------------------------------


def test_migrate_accepts_noinput(monkeypatch):
    import importlib

    # ``zeeb_orm.cli`` re-exports the ``main`` function, which shadows the
    # submodule on plain ``import zeeb_orm.cli.main as ...``.
    cli_main_module = importlib.import_module("zeeb_orm.cli.main")
    from zeeb_orm.cli.commands import migrate as migrate_cmd

    calls = {}

    def fake_run_migrate(*args, **kwargs):
        calls["ran"] = True
        return 0

    monkeypatch.setattr(migrate_cmd, "run_migrate", fake_run_migrate)
    monkeypatch.setattr("sys.argv", ["zeeb-manage", "migrate", "--noinput"])
    assert cli_main_module.main() == 0
    assert calls.get("ran") is True


# ---------------------------------------------------------------------------
# Model registration: a broken import inside an existing models.py must fail
# loudly instead of degrading to "No changes detected".
# ---------------------------------------------------------------------------


async def test_broken_models_import_fails_loudly(project: Path, monkeypatch):
    settings_py = project / "demo" / "settings.py"
    settings_py.write_text(
        settings_py.read_text().replace(
            "INSTALLED_APPS = [", 'INSTALLED_APPS = [\n    "apps.blog",'
        )
    )
    models_py = project / "apps" / "blog" / "models.py"
    models_py.write_text(
        models_py.read_text() + "\nfrom .does_not_exist import Broken\n"
    )
    import zeeb_orm.migrations.cli as mig_cli

    monkeypatch.chdir(project)
    with pytest.raises(Exception) as exc:
        mig_cli._register_models(project)
    assert "blog" in str(exc.value)


async def test_missing_models_module_still_skippable(project: Path, monkeypatch):
    import warnings

    import zeeb_orm.migrations.cli as mig_cli

    # An INSTALLED_APPS entry whose models module simply doesn't exist stays a
    # warning (apps without models are legal).
    settings_py = project / "demo" / "settings.py"
    settings_py.write_text(
        settings_py.read_text().replace(
            "INSTALLED_APPS = [", 'INSTALLED_APPS = [\n    "apps.ghost",'
        )
    )
    monkeypatch.chdir(project)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        mig_cli._register_models(project)
    assert any("ghost" in str(w.message) for w in caught)


# ---------------------------------------------------------------------------
# Inspectors: commented registrations are not endpoints; AbstractUser models
# appear in model listings.
# ---------------------------------------------------------------------------


async def test_list_endpoints_skips_commented_registrations(project: Path):
    # Fresh scaffold contains only the commented example registration.
    res = await agents.list_endpoints(project_root=project)
    assert res.success
    assert res.data["count"] == 0, res.data

    assert (await agents.create_model("blog", "Post", [{"name": "title", "type": "string", "max_length": 50}], project_root=project)).success
    assert (await agents.create_serializer("blog", "Post", project_root=project)).success
    assert (await agents.create_viewset("blog", "Post", project_root=project)).success
    assert (await agents.register_route("blog", "Post", project_root=project)).success
    res = await agents.list_endpoints(project_root=project)
    assert res.success
    assert [e["viewset"] for e in res.data["endpoints"]] == ["PostViewSet"]


def test_extract_model_names_includes_abstract_user_subclasses():
    content = (
        "class User(AbstractUser):\n"
        "    display_name = fields.CharField(max_length=120, null=True)\n"
        "\n"
        "class Post(Model):\n"
        "    title = fields.CharField(max_length=50)\n"
    )
    assert extract_model_names(content) == ["User", "Post"]


# ---------------------------------------------------------------------------
# Router: GET collection (list) route is registered again.
# ---------------------------------------------------------------------------


def test_default_routes_include_get_list():
    from zeeb_api.routers.default import SimpleRouter

    mappings = [r.mapping for r in SimpleRouter.default_routes]
    assert {"get": "list"} in mappings
    assert {"post": "create"} in mappings


# ---------------------------------------------------------------------------
# Exception handlers: zeeb_orm.ValidationError becomes a 400 envelope, not a
# 500 with a leaked traceback.
# ---------------------------------------------------------------------------


def test_orm_validation_error_returns_400_envelope():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from zeeb_api.exception_handlers import install_exception_handlers
    from zeeb_orm.exceptions import ValidationError as ORMValidationError

    app = FastAPI()
    install_exception_handlers(app)

    @app.post("/boom")
    async def boom():
        raise ORMValidationError({"label": ["This field cannot be null."]})

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/boom")
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"][0]["field"] == "label"


# ---------------------------------------------------------------------------
# ASGI template: applies settings.MIDDLEWARE and serves root /health.
# ---------------------------------------------------------------------------


def test_asgi_template_applies_middleware_and_health():
    from zeeb_orm.cli.commands.startproject import ASGI_PY

    rendered = ASGI_PY.format(project_name="probe_api")
    compile(rendered, "asgi.py", "exec")
    assert "for middleware_path in reversed(MIDDLEWARE):" in rendered
    assert "app.add_middleware(middleware_cls)" in rendered
    assert '@app.get("/health"' in rendered
    assert '@app.get("/ready"' in rendered
