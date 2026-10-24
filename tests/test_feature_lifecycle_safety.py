"""Partial failures in the feature lifecycle must not lose code or migrate half a schema.

Each test pins one way a multi-step lifecycle operation used to leave the
project worse than either "done" or "not started": a migration written for an
incomplete plan, an archived fragment left off the record, a re-run restore
reporting finished work as missing, a delete that forgot a feature whose model
was still in ``models.py``, and the text-span ``delete_model`` that took the
module code after the class with it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import zeeb_agents as agents
from zeeb_agents import feature_archive
from zeeb_agents._utils.errors import fail
from zeeb_agents.feature_manifest import archive_path, load_manifest
from zeeb_agents.feature_spec import execute_plan

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
async def built(tmp_path: Path) -> Path:
    res = await agents.create_project("demo", directory=str(tmp_path))
    assert res.success, res.message
    root = tmp_path / "demo"
    res = await agents.build_feature(BLOG, migrate=False, verify=False, project_id=root)
    assert res.success, res.message
    return root


def _models_src(root: Path) -> str:
    return (root / "apps" / "content" / "models.py").read_text(encoding="utf-8")


async def test_delete_model_keeps_the_module_code_after_the_class(built: Path):
    path = built / "apps" / "content" / "models.py"
    path.write_text(
        _models_src(built) + "\n\ndef post_count_label(n):\n    return f'{n} posts'\n",
        encoding="utf-8",
    )

    res = await agents.delete_model("content", "Post", project_id=built)

    assert res.success, res.message
    source = _models_src(built)
    assert "class Post(" not in source
    assert "def post_count_label(n):" in source


async def test_the_manifest_records_the_route_prefix_the_plan_registered(built: Path):
    routes = load_manifest(built)["features"]["blog"]["artifacts"]["routes"]
    assert routes == [{"app": "content", "model": "Post", "prefix": "posts"}]


async def test_a_failed_operation_stops_the_plan_before_any_migration(built: Path):
    plan = {
        "operations": [
            {"op": "delete_model", "app": "nosuchapp", "model": "Ghost"},
            {"op": "make_migrations", "name": None},
            {"op": "run_migrations"},
        ]
    }

    outcome = await execute_plan(plan, built, migrate=True)

    assert outcome["failed_operations"][0]["op"]["op"] == "delete_model"
    assert outcome["changes"]["migrations_created"] == []
    assert [op["op"] for op in outcome["remaining_operations"]] == [
        "make_migrations",
        "run_migrations",
    ]
    assert outcome["completed_count"] == 0
    assert not list((built / "migrations").glob("*.py"))


async def test_a_deactivate_that_fails_midway_keeps_every_cut_fragment_on_record(
    built: Path, monkeypatch
):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(feature_archive, "_prune_host_imports", _boom)

    res = await agents.deactivate_feature("blog", verify=False, project_id=built)

    assert not res.success
    record = json.loads((archive_path(built, "blog") / "archive.json").read_text())
    names = {fragment["name"] for fragment in record["fragments"]}
    assert {"PostSerializer", "PostViewSet"} <= names
    for fragment in record["fragments"]:
        assert (archive_path(built, "blog") / fragment["fragment"]).is_file()


async def test_re_running_a_restore_reports_restored_code_as_skipped_not_missing(
    built: Path,
):
    res = await agents.deactivate_feature("blog", verify=False, project_id=built)
    assert res.success, res.message
    record = feature_archive.read_record(built, "blog")

    first = feature_archive.restore_artifacts(built, "blog", record)
    again = feature_archive.restore_artifacts(built, "blog", record)

    assert "PostViewSet" in first["restored"]
    assert again["missing"] == []
    assert "PostViewSet" in again["skipped"]


async def test_delete_keeps_the_archive_when_a_model_cannot_be_removed(
    built: Path, monkeypatch
):
    import zeeb_agents.models as models_module

    async def _refuse(app, model_name, project_root=None, **_kwargs):
        return fail("models.py is read-only", code="permission_denied")

    monkeypatch.setattr(models_module, "delete_model", _refuse)

    res = await agents.delete_feature(
        "blog", confirm=True, migrate=False, verify=False, project_id=built
    )

    assert not res.success
    assert res.data["error_code"] == "delete_incomplete"
    assert res.data["models_not_removed"] == ["content.Post"]
    entry = load_manifest(built)["features"]["blog"]
    assert entry.get("delete_pending") is True
    assert (archive_path(built, "blog") / "archive.json").is_file()
    assert "class Post(" in _models_src(built)
