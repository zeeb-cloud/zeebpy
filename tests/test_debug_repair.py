"""The debug → fix loop without ``write_file``.

Every failure rooted in agent-authored code used to have an observation tool
and no mutation tool: an action body, a class method, a class attribute, a
stray file, a missing dependency could only be fixed by rewriting a whole
file. These tests pin the surgical editors that replace that, the checks that
find the problem with a file and line, and the guidance that names the editor
for it.

Runs against a real scaffolded project, in the style of ``test_intent.py``.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

import zeeb_agents as agents

SPEC = {
    "name": "blog",
    "entities": [
        {
            "name": "Post",
            "fields": [{"name": "title", "type": "string", "max_length": 200}],
            "functions": [
                {
                    "kind": "action",
                    "name": "publish",
                    "detail": True,
                    "methods": ["post"],
                    "body": "return {'ok': True}",
                }
            ],
        }
    ],
}


@pytest.fixture(autouse=True)
def _isolate_global_state():
    """Snapshot/restore zeeb_orm's process-global registry and ``apps.*`` imports."""
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
    """The project with the blog feature built (no verification, no migration run)."""
    res = await agents.build_feature(SPEC, project_id=root, verify=False)
    assert res.success, res.message
    return root


def _views(root: Path) -> Path:
    return root / "apps" / "blog" / "views.py"


# ---------------------------------------------------------------------------
# edit_function — the body changes, the decorator and signature do not
# ---------------------------------------------------------------------------


async def test_edit_function_replaces_an_action_body_and_keeps_nesting(built: Path):
    before = _views(built).read_text()
    res = await agents.edit_function(
        "blog",
        "publish",
        entity="Post",
        body="post = await self.get_object()\nif post:\n    return {'id': str(post.id)}\nreturn {}",
        imports=["from datetime import date"],
        project_id=built,
    )
    assert res.success, res.message
    assert res.data == {
        "app": "blog",
        "name": "publish",
        "kind": "action",
        "file": "apps/blog/views.py",
        "replaced": True,
        "imports_added": ["from datetime import date"],
    }
    after = _views(built).read_text()
    ast.parse(after)
    # Nested block survived at the body's indentation; the decorator is untouched.
    assert "        if post:\n            return {'id': str(post.id)}\n        return {}\n" in after
    assert before.count("@action(") == after.count("@action(")
    assert "return {'ok': True}" not in after
    assert "from datetime import date" in after


async def test_edit_function_names_the_closest_function_when_missing(built: Path):
    res = await agents.edit_function("blog", "publsh", "pass", entity="Post", project_id=built)
    assert not res.success
    assert res.data["error_code"] == "function_not_found"
    assert "publish" in res.data["suggestions"]


async def test_edit_function_refuses_a_file_that_does_not_parse(built: Path):
    _views(built).write_text(_views(built).read_text() + "\ndef broken(:\n")
    res = await agents.edit_function("blog", "publish", "pass", entity="Post", project_id=built)
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert "edit_file" in res.message


async def test_edit_function_validates_its_inputs(built: Path):
    assert (await agents.edit_function("blog", "x", "pass", kind="nope", project_id=built)).data[
        "error_code"
    ] == "invalid_input"
    assert (await agents.edit_function("blog", "publish", "pass", project_id=built)).data[
        "error_code"
    ] == "invalid_input"


# ---------------------------------------------------------------------------
# set_class_method / set_class_attribute
# ---------------------------------------------------------------------------


async def test_set_class_method_adds_replaces_and_removes(built: Path):
    source = "def get_queryset(self):\n    return Post.objects.filter(title__isnull=False)"
    res = await agents.set_class_method(
        "blog", "PostViewSet", "get_queryset", source=source, project_id=built
    )
    assert res.success, res.message
    assert res.data["action"] == "added" and res.data["file"] == "apps/blog/views.py"
    text = _views(built).read_text()
    ast.parse(text)
    assert (
        "    def get_queryset(self):\n        return Post.objects.filter(title__isnull=False)"
        in text
    )

    res = await agents.set_class_method(
        "blog",
        "PostViewSet",
        "get_queryset",
        source="def get_queryset(self):\n    return Post.objects.none()",
        project_id=built,
    )
    assert res.success and res.data["action"] == "replaced"
    text = _views(built).read_text()
    assert text.count("def get_queryset") == 1 and "Post.objects.none()" in text

    res = await agents.set_class_method(
        "blog", "PostViewSet", "get_queryset", remove=True, project_id=built
    )
    assert res.success and res.data["action"] == "removed"
    assert "def get_queryset" not in _views(built).read_text()
    res = await agents.set_class_method(
        "blog", "PostViewSet", "get_queryset", remove=True, project_id=built
    )
    assert res.success and res.data["action"] == "skipped"


async def test_set_class_method_finds_the_class_across_generated_files(built: Path):
    res = await agents.set_class_method(
        "blog", "Post", "__str__", source="def __str__(self):\n    return self.title",
        project_id=built,
    )
    assert res.success, res.message
    assert res.data["file"] == "apps/blog/models.py"
    models = (built / "apps" / "blog" / "models.py").read_text()
    ast.parse(models)
    assert "    def __str__(self):\n        return self.title" in models


async def test_set_class_method_rejects_bad_source(built: Path):
    res = await agents.set_class_method(
        "blog", "PostViewSet", "x", source="def y(self):\n    pass", project_id=built
    )
    assert not res.success and res.data["error_code"] == "invalid_input"
    res = await agents.set_class_method(
        "blog", "PostViewSet", "x", source="def x(self:\n    pass", project_id=built
    )
    assert not res.success and res.data["error_code"] == "invalid_input"
    res = await agents.set_class_method("blog", "PostViewSet", "x", project_id=built)
    assert not res.success and res.data["error_code"] == "invalid_input"
    res = await agents.set_class_method(
        "blog", "Nope", "x", source="def x(self):\n    pass", project_id=built
    )
    assert not res.success and res.data["error_code"] == "model_not_found"


async def test_set_class_attribute_sets_replaces_removes_and_creates_meta(built: Path):
    res = await agents.set_class_attribute(
        "blog",
        "PostViewSet",
        "filterset_class",
        "PostFilter",
        imports=["from .filters import PostFilter"],
        project_id=built,
    )
    assert res.success, res.message
    assert res.data["action"] == "set" and res.data["imports_added"] == [
        "from .filters import PostFilter"
    ]
    text = _views(built).read_text()
    ast.parse(text)
    assert "    filterset_class = PostFilter\n" in text
    assert "from .filters import PostFilter" in text

    res = await agents.set_class_attribute(
        "blog", "PostViewSet", "filterset_class", "OtherFilter", project_id=built
    )
    assert res.success and res.data["action"] == "replaced"
    res = await agents.set_class_attribute(
        "blog", "PostViewSet", "filterset_class", remove=True, project_id=built
    )
    assert res.success and res.data["action"] == "removed"
    assert "filterset_class" not in _views(built).read_text()

    # A model gains a Meta key — and a Meta — with meta=True.
    res = await agents.set_class_attribute(
        "blog", "Post", "ordering", '["-created_at"]', meta=True, project_id=built
    )
    assert res.success, res.message
    assert res.data["meta"] is True and res.data["action"] in ("set", "replaced")
    models = (built / "apps" / "blog" / "models.py").read_text()
    ast.parse(models)
    assert 'ordering = ["-created_at"]' in models


async def test_set_class_attribute_rejects_a_non_expression(built: Path):
    res = await agents.set_class_attribute("blog", "PostViewSet", "x", "def (", project_id=built)
    assert not res.success and res.data["error_code"] == "invalid_input"


# ---------------------------------------------------------------------------
# edit_file / delete_file / add_dependency
# ---------------------------------------------------------------------------


async def test_edit_file_replaces_exactly_the_expected_occurrences(built: Path):
    res = await agents.edit_file(
        "apps/blog/views.py", "return {'ok': True}", "return {'ok': False}", project_id=built
    )
    assert res.success and res.data["replacements"] == 1
    assert "return {'ok': False}" in _views(built).read_text()

    res = await agents.edit_file("apps/blog/views.py", "not-in-file", "x", project_id=built)
    assert not res.success
    assert res.data["error_code"] == "invalid_input" and res.data["occurrences"] == 0

    res = await agents.edit_file("apps/blog/views.py", "", "x", project_id=built)
    assert not res.success and res.data["error_code"] == "invalid_input"
    res = await agents.edit_file("apps/blog/nope.py", "a", "b", project_id=built)
    assert not res.success and res.data["error_code"] == "file_not_found"


async def test_delete_file_is_idempotent_and_protects_the_skeleton(built: Path):
    stray = built / "tests" / "stray.py"
    stray.write_text("x = 1\n")
    res = await agents.delete_file("tests/stray.py", project_id=built)
    assert res.success and res.data == {"path": "tests/stray.py", "deleted": True}
    assert not stray.exists()
    res = await agents.delete_file("tests/stray.py", project_id=built)
    assert res.success and res.data["deleted"] is False

    for protected in ("manage.py", "apps/blog/__init__.py", "demo/settings.py", ".env"):
        res = await agents.delete_file(protected, project_id=built)
        assert not res.success and res.data["error_code"] == "permission_denied", protected
    res = await agents.delete_file("apps", project_id=built)
    assert not res.success and res.data["error_code"] == "invalid_input"
    res = await agents.delete_file("../outside.py", project_id=built)
    assert not res.success and res.data["error_code"] == "outside_project_root"


async def test_add_dependency_upserts_by_normalised_name(built: Path):
    requirements = built / "requirements.txt"
    res = await agents.add_dependency("requests-cache>=1", project_id=built)
    assert res.success and res.data["action"] == "added" and res.data["restart_required"]
    assert "requests-cache>=1" in requirements.read_text()

    res = await agents.add_dependency("Requests_Cache==1.2", project_id=built)
    assert res.success and res.data["action"] == "updated"
    text = requirements.read_text()
    assert "requests-cache>=1" not in text and "Requests_Cache==1.2" in text

    res = await agents.add_dependency("Requests_Cache==1.2", project_id=built)
    assert res.success and res.data["action"] == "unchanged" and not res.data["restart_required"]
    res = await agents.add_dependency("requests-cache", remove=True, project_id=built)
    assert res.success and res.data["action"] == "removed"
    assert "requests" not in requirements.read_text().lower()

    res = await agents.add_dependency(">=1", project_id=built)
    assert not res.success and res.data["error_code"] == "invalid_input"
    res = await agents.add_dependency("x", requirements_file="../r.txt", project_id=built)
    assert not res.success and res.data["error_code"] == "invalid_input"


# ---------------------------------------------------------------------------
# check_code — file, line and column, for syntax and import failures
# ---------------------------------------------------------------------------


async def test_check_code_passes_on_a_healthy_project(built: Path):
    res = await agents.check_code(project_id=built)
    assert res.success, res.message
    assert res.data["ok"] is True and res.data["errors"] == []
    assert res.data["imports_checked"] is True and res.data["checked"] > 5


async def test_check_code_locates_a_syntax_error(built: Path):
    views = _views(built)
    views.write_text(
        views.read_text().replace(
            "async def publish(self, request, pk=None):",
            "async def publish(self, request, pk=None)",
        )
    )
    res = await agents.check_code(project_id=built)
    assert res.success and res.data["ok"] is False
    error = res.data["errors"][0]
    assert error["kind"] == "syntax" and error["file"] == "apps/blog/views.py"
    assert isinstance(error["line"], int) and error["line"] > 1 and error["col"]


async def test_check_code_reports_a_missing_dependency_with_its_frame(built: Path):
    views = _views(built)
    views.write_text("import definitely_missing_pkg\n" + views.read_text())
    res = await agents.check_code(project_id=built)
    assert res.success and res.data["ok"] is False
    imports = [e for e in res.data["errors"] if e["kind"] == "import"]
    assert imports, res.data
    assert imports[0]["missing_module"] == "definitely_missing_pkg"
    assert imports[0]["file"] == "apps/blog/views.py" and imports[0]["line"] == 1
    assert imports[0]["module"] == "apps.blog.views"

    res = await agents.check_code(imports=False, project_id=built)
    assert res.success and res.data["ok"] is True and res.data["imports_checked"] is False


async def test_check_code_does_not_cascade_a_syntax_error_into_imports(built: Path):
    views = _views(built)
    views.write_text(views.read_text() + "\ndef broken(:\n")
    res = await agents.check_code(project_id=built)
    assert res.success and res.data["ok"] is False
    assert [e["kind"] for e in res.data["errors"]] == ["syntax"]
    assert res.data["imports_checked"] is False


# ---------------------------------------------------------------------------
# Migrations: named targets, fake, show, squash
# ---------------------------------------------------------------------------


async def test_run_migrations_walks_to_a_target_and_fakes(built: Path):
    res = await agents.run_migrations(target="zero", project_id=built)
    assert res.success, res.message
    assert res.data["applied"] == [] and res.data["unapplied"] and res.data["target"] == "zero"
    status = await agents.get_migration_status(project_id=built)
    assert status.data["applied"] == []

    res = await agents.run_migrations(fake=True, project_id=built)
    assert res.success and res.data["fake"] is True and res.data["applied"]
    assert "Faked" in res.message
    res = await agents.run_migrations(target="0999_nope", project_id=built)
    assert not res.success and res.data["error_code"] == "file_not_found"


async def test_show_migration_reads_source_operations_and_state(built: Path):
    res = await agents.show_migration("0001", project_id=built)
    assert res.success, res.message
    assert res.data["name"].startswith("0001_") and res.data["path"].startswith("migrations/")
    assert "class Migration" in res.data["content"]
    assert res.data["operations"] and res.data["applied"] is True
    assert res.data["dependencies"] == [] and res.data["replaces"] == []
    res = await agents.show_migration("0042", project_id=built)
    assert not res.success and res.data["error_code"] == "file_not_found"


def _makemigrations_out_of_process(root: Path) -> None:
    """A second migration, made by the CLI so the in-process registry stays clean."""
    proc = subprocess.run(
        [sys.executable, "manage.py", "makemigrations", "--json"],
        cwd=str(root),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


async def test_squash_migrations_records_what_it_replaces(built: Path):
    res = await agents.add_field(
        "blog", "Post", {"name": "note", "type": "TextField", "null": True}, project_id=built
    )
    assert res.success, res.message
    _makemigrations_out_of_process(built)
    names = sorted(p.stem for p in (built / "migrations").glob("0*.py"))
    assert len(names) == 2, names

    res = await agents.squash_migrations("0001", "0002", name="all", project_id=built)
    assert res.success, res.message
    assert res.data["replaces"] == names and res.data["created"].startswith("0003_")
    shown = await agents.show_migration(res.data["created"], project_id=built)
    assert shown.success and shown.data["replaces"] == names

    res = await agents.squash_migrations("0002", "0001", project_id=built)
    assert not res.success and res.data["error_code"] == "invalid_input"
    res = await agents.squash_migrations("0001", "0777", project_id=built)
    assert not res.success and res.data["error_code"] == "file_not_found"


# ---------------------------------------------------------------------------
# Guidance: diagnose names the editor, verify names the fix
# ---------------------------------------------------------------------------


async def test_diagnose_problem_names_the_editor_for_a_syntax_error(built: Path):
    views = _views(built)
    views.write_text(
        views.read_text().replace(
            "async def publish(self, request, pk=None):",
            "async def publish(self, request, pk=None)",
        )
    )
    res = await agents.diagnose_problem(
        symptom="POST /posts/1/publish returns 500", project_id=built
    )
    assert res.success, res.message
    assert res.data["root_cause"]["type"] == "syntax_error"
    assert res.data["recommended_fix"] == {
        "tool": "edit_function",
        "arguments": {"app": "blog", "name": "publish", "kind": "action", "entity": "Post"},
    }
    assert any(f["area"] == "code" for f in res.data["findings"])
    assert "edit_function" in res.data["next_actions"][0]


async def test_diagnose_problem_names_add_dependency_for_a_missing_package(built: Path):
    views = _views(built)
    views.write_text("import definitely_missing_pkg\n" + views.read_text())
    res = await agents.diagnose_problem(project_id=built)
    assert res.success
    assert res.data["root_cause"]["type"] == "import_error"
    assert res.data["recommended_fix"] == {
        "tool": "add_dependency",
        "arguments": {"requirement": "definitely_missing_pkg"},
    }


async def test_diagnose_problem_names_edit_file_for_a_broken_project_import(built: Path):
    views = _views(built)
    views.write_text("from .nothere import x\n" + views.read_text())
    res = await agents.diagnose_problem(project_id=built)
    assert res.success
    assert res.data["root_cause"]["type"] == "import_error"
    assert res.data["recommended_fix"] == {
        "tool": "edit_file",
        "arguments": {"path": "apps/blog/views.py"},
    }


async def test_diagnose_problem_names_create_viewset_for_an_unserved_model(built: Path):
    res = await agents.create_model(
        "blog", "Invoice", [{"name": "total", "type": "IntegerField"}], project_id=built
    )
    assert res.success, res.message
    res = await agents.diagnose_problem(endpoint="/api/v1/invoices", project_id=built)
    assert res.success
    assert res.data["root_cause"]["type"] == "route_not_registered"
    assert res.data["recommended_fix"] == {
        "tool": "create_viewset",
        "arguments": {"app": "blog", "model_name": "Invoice"},
    }


async def test_verify_project_runs_the_code_check_by_default(built: Path):
    res = await agents.verify_project(checks=["structure", "code"], project_id=built)
    assert res.success, res.message
    assert res.data["verification"]["checks"]["code"] == {"ok": True, "errors": [], "count": 0}

    views = _views(built)
    views.write_text(views.read_text() + "\ndef broken(:\n")
    res = await agents.verify_project(checks=["code"], project_id=built)
    assert res.success and res.data["verified"] is False
    # One syntax error, not a cascade: the import pass waits until the file parses.
    assert res.data["verification"]["checks"]["code"]["count"] == 1
    assert "edit_function" in res.data["next_actions"][0]
    assert "apps/blog/views.py" in res.data["next_actions"][0]


# ---------------------------------------------------------------------------
# The fixes that came along: signals, Meta, action replace, test refresh
# ---------------------------------------------------------------------------


async def test_edit_signal_receiver_keeps_nested_blocks(built: Path):
    res = await agents.create_signal_receiver(
        "blog", "post_save", "Post", "on_post_saved", project_id=built
    )
    assert res.success, res.message
    res = await agents.edit_signal_receiver(
        "blog",
        "on_post_saved",
        "if instance.title:\n    for _ in range(2):\n        pass\nreturn None",
        project_id=built,
    )
    assert res.success, res.message
    signals = (built / "apps" / "blog" / "signals.py").read_text()
    ast.parse(signals)  # used to be an IndentationError: every line flattened to 4 spaces
    assert "    if instance.title:\n        for _ in range(2):\n            pass\n" in signals
    assert "@receiver(post_save, sender=Post)" in signals


async def test_update_model_adds_a_meta_key_it_did_not_have(built: Path):
    res = await agents.create_model(
        "blog", "Invoice", [{"name": "total", "type": "IntegerField"}], project_id=built
    )
    assert res.success, res.message
    res = await agents.update_model(
        "blog", "Invoice", meta_changes={"ordering": ["-total"]}, project_id=built
    )
    assert res.success, res.message
    assert res.data["changes"] == ["meta.ordering added"]
    res = await agents.update_model(
        "blog", "Invoice", meta_changes={"ordering": ["total"]}, project_id=built
    )
    assert res.data["changes"] == ["meta.ordering updated"]
    models = (built / "apps" / "blog" / "models.py").read_text()
    ast.parse(models)
    assert 'ordering = ["total"]' in models and 'ordering = ["-total"]' not in models


async def test_add_viewset_action_can_replace_an_existing_action(built: Path):
    res = await agents.add_viewset_action(
        "blog", "Post", "publish", body="return {'v': 2}", if_exists="replace", project_id=built
    )
    assert res.success, res.message
    assert res.data["replaced"] is True
    text = _views(built).read_text()
    ast.parse(text)
    assert text.count("def publish") == 1 and "return {'v': 2}" in text
    res = await agents.add_viewset_action("blog", "Post", "publish", body="x", project_id=built)
    assert not res.success and res.data["error_code"] == "already_exists"
    assert "replace" in res.message


async def test_regenerate_tests_rewrites_only_the_generated_suite(built: Path):
    generated = built / "tests" / "test_blog_generated.py"
    conftest = built / "tests" / "conftest.py"
    generated.write_text("# stale\n")
    conftest_before = conftest.read_text()
    res = await agents.regenerate_tests("blog", project_id=built)
    assert res.success, res.message
    assert res.data["file"] == "tests/test_blog_generated.py" and res.data["tests"] > 0
    assert res.data["state_changed"] is True
    assert generated.read_text() != "# stale\n"
    ast.parse(generated.read_text())
    assert conftest.read_text() == conftest_before
    res = await agents.regenerate_tests("nope", project_id=built)
    assert not res.success and res.data["error_code"] == "feature_not_found"
