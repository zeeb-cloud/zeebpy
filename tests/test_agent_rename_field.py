"""rename_field: a field and every generated list naming it move together, and the data stays."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

import zeeb_agents as agents
from zeeb_agents._utils.code_gen import extract_class_block, extract_field_names

POST_FIELDS = [
    {"name": "title", "type": "CharField", "max_length": 200},
    {"name": "body", "type": "TextField", "null": True},
]


@pytest.fixture
async def project(tmp_path: Path) -> Path:
    res = await agents.create_project("demo", directory=str(tmp_path))
    assert res.success, res.message
    root = tmp_path / "demo"
    res = await agents.create_app("blog", project_id=root)
    assert res.success, res.message
    return root


async def _post_endpoint(root: Path) -> None:
    res = await agents.generate_crud(
        "blog",
        "Post",
        POST_FIELDS,
        meta={"ordering": ["-title"]},
        search_fields=["^title"],
        ordering_fields=["title"],
        project_id=root,
    )
    assert res.success, res.message
    res = await agents.update_viewset(
        "blog", "Post", query_fields=["title", "body"], project_id=root
    )
    assert res.success, res.message
    res = await agents.create_filterset("blog", "Post", {"title": ["exact"]}, project_id=root)
    assert res.success, res.message


def _source(root: Path, name: str) -> str:
    return (root / "apps" / "blog" / name).read_text()


def _class(root: Path, name: str, class_name: str) -> str:
    block = extract_class_block(_source(root, name), class_name)
    assert block is not None, f"{class_name} missing from {name}"
    return block


async def test_a_rename_moves_the_definition_and_every_generated_list(project: Path):
    await _post_endpoint(project)

    res = await agents.rename_field("blog", "Post", "title", "headline", project_id=project)

    assert res.success, res.message
    assert set(res.data["files"]) >= {
        "apps/blog/models.py",
        "apps/blog/serializers.py",
        "apps/blog/views.py",
        "apps/blog/filters.py",
    }
    models = _source(project, "models.py")
    assert extract_field_names(models, "Post")[:2] == ["headline", "body"]
    post = _class(project, "models.py", "Post")
    assert '"-headline"' in post and "title" not in post
    for name, class_name in (
        ("serializers.py", "PostSerializer"),
        ("views.py", "PostViewSet"),
        ("filters.py", "PostFilter"),
    ):
        assert "title" not in _class(project, name, class_name), f"{name} still names the old field"
        compile(_source(project, name), name, "exec")
    views = _class(project, "views.py", "PostViewSet")
    assert '"^headline"' in views
    assert 'query_fields = ["headline", "body"]' in views


async def test_a_rename_that_already_happened_is_skipped(project: Path):
    await _post_endpoint(project)
    await agents.rename_field("blog", "Post", "title", "headline", project_id=project)

    again = await agents.rename_field("blog", "Post", "title", "headline", project_id=project)

    assert again.success and again.data["skipped"] is True
    assert again.data["files"] == []


async def test_an_unknown_field_or_a_taken_name_is_refused(project: Path):
    await _post_endpoint(project)

    missing = await agents.rename_field("blog", "Post", "titel", "headline", project_id=project)
    assert not missing.success
    assert missing.data["error_code"] == "field_not_found"
    assert "title" in missing.data["suggestions"]

    taken = await agents.rename_field("blog", "Post", "title", "body", project_id=project)
    assert not taken.success
    assert taken.data["error_code"] == "already_exists"


async def test_code_the_rename_cannot_follow_refuses_it_and_writes_nothing(project: Path):
    """A get_queryset filtering on owner_id would break silently; the caller changes it first."""
    res = await agents.create_model(
        "blog",
        "Note",
        [
            {"name": "text", "type": "CharField", "max_length": 50},
            {"name": "owner", "type": "ForeignKey", "to": "User", "null": True},
        ],
        project_id=project,
    )
    assert res.success, res.message
    await agents.create_serializer("blog", "Note", project_id=project)
    res = await agents.create_viewset(
        "blog", "Note", owner_field="owner", owner_scoped_reads=True, project_id=project
    )
    assert res.success, res.message
    before = {name: _source(project, name) for name in ("models.py", "serializers.py", "views.py")}

    refused = await agents.rename_field("blog", "Note", "owner", "author", project_id=project)

    assert not refused.success
    assert refused.data["error_code"] == "field_in_use"
    assert {loc["file"] for loc in refused.data["locations"]} == {"apps/blog/views.py"}
    assert {name: _source(project, name) for name in before} == before


async def test_accepting_the_rename_keeps_the_columns_data(project: Path):
    await _post_endpoint(project)
    assert (await agents.make_migrations(name="initial", project_id=project)).success
    assert (await agents.run_migrations(project_id=project)).success
    db = sqlite3.connect(project / "db.sqlite3")
    table = next(
        name
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if name.endswith("post") or name.endswith("posts")
    )
    row = (uuid.uuid4().hex,)
    db.execute(f"INSERT INTO {table} (id, title, body) VALUES (?, 'kept', 'x')", row)
    db.commit()

    renamed = await agents.rename_field("blog", "Post", "title", "headline", project_id=project)
    assert renamed.success, renamed.message
    made = await agents.make_migrations(
        name="rename_title", accept_renames=True, project_id=project
    )
    assert made.success, made.message
    assert [(r["old"], r["new"]) for r in made.data["renamed"]] == [("title", "headline")]
    assert made.data["possible_renames"] == []
    assert (await agents.run_migrations(project_id=project)).success

    assert db.execute(f"SELECT headline FROM {table}").fetchall() == [("kept",)]
    db.close()
