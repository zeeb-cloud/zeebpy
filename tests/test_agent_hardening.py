"""Regression tests for the ``zeeb_agents`` hardening pass.

Each section pins one defect an audit found: the call that used to succeed
(or corrupt the project) is asserted to fail cleanly — or to behave — now.
Every tool still returns an ``AgentResult`` and never raises.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import zeeb_agents as agents
from zeeb_agents._utils.code_gen import render_serializer_class
from zeeb_agents.feature_spec import _render_transition_body

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def project(tmp_path: Path) -> Path:
    """A real scaffolded project with one app ``blog``."""
    res = await agents.create_project("demo", directory=str(tmp_path))
    assert res.success, res.message
    root = tmp_path / "demo"
    res = await agents.create_app("blog", project_id=root)
    assert res.success, res.message
    return root


def _snapshot(root: Path) -> dict[str, str]:
    """Every ``.py`` file under *root*, by relative path — to prove nothing moved."""
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    }


def _calls_named(tree: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


# ---------------------------------------------------------------------------
# 1. Names and values never become code
# ---------------------------------------------------------------------------

_SERIALIZER_PAYLOAD = 'title"]\n    import os; os.system("id")  #'


async def test_serializer_field_names_cannot_inject_code(project):
    before = _snapshot(project)
    res = await agents.create_serializer(
        "blog", "Post", fields=[_SERIALIZER_PAYLOAD], project_id=project
    )
    assert not res.success
    assert res.data["error_code"] == "invalid_identifier"
    assert _snapshot(project) == before


def test_render_serializer_class_refuses_non_identifier_fields():
    with pytest.raises(agents._utils.errors.AgentError):
        render_serializer_class("Post", fields=[_SERIALIZER_PAYLOAD])
    with pytest.raises(agents._utils.errors.AgentError):
        render_serializer_class("Post", read_only_fields=[_SERIALIZER_PAYLOAD])


async def test_update_serializer_field_names_cannot_inject_code(project):
    res = await agents.create_serializer("blog", "Post", fields=["id"], project_id=project)
    assert res.success, res.message
    before = _snapshot(project)
    res = await agents.update_serializer(
        "blog", "Post", fields=[_SERIALIZER_PAYLOAD], project_id=project
    )
    assert not res.success
    assert _snapshot(project) == before


@pytest.mark.parametrize(
    "spec",
    [
        # A kwarg KEY carrying code — values were rendered, keys were not.
        {"name": "title", "type": "CharField", 'max_length=1) or __import__("os").getcwd() #': 1},
        # The same through the verbatim ``raw`` block: its keys are names too.
        {"name": "title", "type": "CharField", "raw": {"default=__import__('os')#": "1"}},
        # A key that is an identifier but not something CharField accepts.
        {"name": "title", "type": "CharField", "max_digits": 3},
        # A field name that is a keyword.
        {"name": "class", "type": "CharField"},
    ],
)
async def test_field_kwarg_keys_are_checked_against_the_field(project, spec):
    before = _snapshot(project)
    res = await agents.create_model("blog", "Post", fields=[spec], project_id=project)
    assert not res.success
    assert res.data["error_code"] == "invalid_field_spec"
    assert _snapshot(project) == before


async def test_raw_values_stay_verbatim(project):
    """``raw`` is the documented escape hatch: its values are code on purpose."""
    res = await agents.create_model(
        "blog",
        "Post",
        fields=[{"name": "tags", "type": "JSONField", "raw": {"default": "list"}}],
        project_id=project,
    )
    assert res.success, res.message
    source = (project / "apps" / "blog" / "models.py").read_text()
    assert "tags = fields.JSONField(default=list)" in source


def test_workflow_states_are_data_not_code():
    payload = "{__import__('os').getcwd()}"
    body = _render_transition_body(
        "Post", "status", {"name": "publish", "from": ["draft", payload], "to": payload}
    )
    tree = ast.parse("async def publish(self, request, pk=None):\n" + _indent(body))
    # No f-string in the generated body: a state inside one would be evaluated.
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.JoinedStr)]
    assert not _calls_named(tree, "__import__")
    constants = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)}
    assert payload in constants


def test_workflow_renderer_refuses_non_identifier_names():
    with pytest.raises(agents._utils.errors.AgentError):
        _render_transition_body(
            "Post", "status); import os #", {"name": "go", "from": ["a"], "to": "b"}
        )
    with pytest.raises(agents._utils.errors.AgentError):
        _render_transition_body(
            "Post", "status", {"name": "go()\nimport os", "from": ["a"], "to": "b"}
        )


def _indent(body: str) -> str:
    return "\n".join("    " + line for line in body.splitlines()) + "\n"


async def test_route_path_cannot_inject_code(project):
    before = _snapshot(project)
    res = await agents.create_route(
        "blog",
        '/x")\nimport os\n@router.get("/y',
        "get",
        "handler",
        project_id=project,
    )
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert _snapshot(project) == before


async def test_route_response_model_must_be_a_class_name(project):
    before = _snapshot(project)
    res = await agents.create_route(
        "blog", "/x", "get", "handler", response_model="dict)\nimport os\n#", project_id=project
    )
    assert not res.success
    assert _snapshot(project) == before


async def test_route_path_is_rendered_as_a_literal(project):
    res = await agents.create_route(
        "blog", "/items/{item_id}", "get", "get_item", project_id=project
    )
    assert res.success, res.message
    tree = ast.parse((project / "apps" / "blog" / "views.py").read_text())
    handler = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_item"
    )
    decorator = handler.decorator_list[0]
    assert isinstance(decorator, ast.Call)
    assert decorator.args[0].value == "/items/{item_id}"
    assert ast.get_docstring(handler) == "GET /items/{item_id}"


async def test_router_prefix_cannot_inject_code(project):
    res = await agents.generate_crud(
        "blog", "Post", [{"name": "title", "type": "CharField"}], project_id=project
    )
    assert res.success, res.message
    res = await agents.create_viewset("blog", "Post", if_exists="skip", project_id=project)
    before = _snapshot(project)
    res = await agents.register_route(
        "blog", "Post", url_prefix='x", PostViewSet)\nimport os\n#', project_id=project
    )
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert _snapshot(project) == before


async def test_viewset_action_url_path_cannot_inject_code(project):
    res = await agents.generate_crud(
        "blog", "Post", [{"name": "title", "type": "CharField"}], project_id=project
    )
    assert res.success, res.message
    before = _snapshot(project)
    res = await agents.add_viewset_action(
        "blog", "Post", "publish", url_path='go")\nimport os\n#', project_id=project
    )
    assert not res.success
    assert _snapshot(project) == before


async def test_viewset_field_lists_are_literals(project):
    res = await agents.generate_crud(
        "blog", "Post", [{"name": "title", "type": "CharField"}], project_id=project
    )
    assert res.success, res.message
    before = _snapshot(project)
    res = await agents.update_viewset(
        "blog", "Post", search_fields=['title"]\nimport os\n#'], project_id=project
    )
    assert not res.success
    assert _snapshot(project) == before


async def test_task_schedule_cannot_escape_the_docstring(project):
    schedule = 'hourly"""\nimport os\n"""'
    res = await agents.create_task("blog", "nightly", schedule=schedule, project_id=project)
    assert res.success, res.message
    tree = ast.parse((project / "apps" / "blog" / "tasks.py").read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Import) and n.names[0].name == "os"]
    task = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef))
    assert schedule in ast.get_docstring(task)


async def test_task_and_permission_names_must_be_identifiers(project):
    before = _snapshot(project)
    res = await agents.create_task("blog", "x():\n    pass\nimport os\ndef y", project_id=project)
    assert not res.success and res.data["error_code"] == "invalid_identifier"
    res = await agents.create_permission_class(
        "blog", "X(BasePermission):\n    pass\nimport os\nclass Y", project_id=project
    )
    assert not res.success and res.data["error_code"] == "invalid_identifier"
    assert _snapshot(project) == before


async def test_oauth_values_are_rendered_as_literals(project):
    uri = 'https://x"\nimport os\n#'
    scope = 'read"]\nimport os\n#'
    res = await agents.setup_oauth("github", scopes=[scope], redirect_uri=uri, project_id=project)
    assert res.success, res.message
    tree = ast.parse((project / "demo" / "settings.py").read_text())
    assigned = {
        n.targets[0].id: n.value
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
    }
    assert ast.literal_eval(assigned["OAUTH_REDIRECT_URI"]) == uri
    provider = assigned["OAUTH_PROVIDERS"].values[0]
    scopes = dict(zip([k.value for k in provider.keys], provider.values))["scopes"]
    assert ast.literal_eval(scopes) == [scope]
    res = await agents.setup_oauth("google", client_id_env="X)\nimport os\n#", project_id=project)
    assert not res.success and res.data["error_code"] == "invalid_input"


async def test_auth_prefix_and_lifetimes_are_validated(project):
    before = _snapshot(project)
    res = await agents.setup_auth(url_prefix='/auth")\nimport os\n#', project_id=project)
    assert not res.success
    res = await agents.setup_auth(access_token_minutes="5\nimport os", project_id=project)
    assert not res.success
    assert _snapshot(project) == before


async def test_manage_settings_writes_a_complete_literal(project):
    value = 'C:\\new\\path "quoted"\nsecond line'
    res = await agents.manage_settings("SECRET_KEY", value, project_id=project)
    assert res.success, res.message
    tree = ast.parse((project / "demo" / "settings.py").read_text())
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "SECRET_KEY"
    )
    assert ast.literal_eval(node.value) == value


async def test_update_model_meta_keys_are_known_names(project):
    res = await agents.create_model(
        "blog", "Post", [{"name": "title", "type": "CharField"}], project_id=project
    )
    assert res.success, res.message
    before = _snapshot(project)
    res = await agents.update_model(
        "blog", "Post", meta_changes={"x = 1\nimport os\ny": 1}, project_id=project
    )
    assert not res.success
    assert res.data["error_code"] == "invalid_meta"
    assert _snapshot(project) == before


async def test_generated_tests_refuse_code_in_descriptors(project):
    entity = {
        "name": "Post",
        "prefix": 'posts{__import__("os").getcwd()}',
        "exposed": True,
        "permission": ["AllowAny"],
        "operations": ["list"],
        "fields": [{"name": "title", "type": "CharField", "max_length": 20}],
    }
    res = await agents.generate_tests("blog", [entity], project_id=project)
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert not (project / "tests" / "test_blog_generated.py").exists()


async def test_dockerfile_values_are_validated(project):
    res = await agents.generate_dockerfile(
        python_version="3.12\nRUN curl x | sh", project_id=project
    )
    assert not res.success and res.data["error_code"] == "invalid_input"
    res = await agents.generate_dockerfile(port="8000\nRUN id", project_id=project)
    assert not res.success and res.data["error_code"] == "invalid_input"
    assert not (project / "Dockerfile").exists()


# ---------------------------------------------------------------------------
# 2. Paths stay inside the project
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["..", ".", "../demo", "/tmp", "blog/../..", "a b"])
async def test_delete_app_refuses_names_that_are_not_apps(project, name):
    before = _snapshot(project)
    res = await agents.delete_app(name, project_id=project)
    assert not res.success
    assert res.data["error_code"] == "invalid_identifier"
    assert (project / "manage.py").exists()
    assert _snapshot(project) == before


async def test_delete_app_refuses_a_symlinked_app(project, tmp_path):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (project / "apps" / "evil").symlink_to(outside, target_is_directory=True)
    res = await agents.delete_app("evil", project_id=project)
    assert not res.success
    assert res.data["error_code"] == "outside_project_root"
    assert (outside / "keep.txt").read_text() == "keep"


async def test_app_scoped_tools_refuse_traversal(project):
    res = await agents.create_model(
        "../demo", "Post", [{"name": "title", "type": "CharField"}], project_id=project
    )
    assert not res.success and res.data["error_code"] == "invalid_identifier"
    res = await agents.create_task("../../tmp", "job", project_id=project)
    assert not res.success and res.data["error_code"] == "invalid_identifier"


@pytest.mark.parametrize("output", ["/tmp/zeeb_seed_escape.py", "../escape_seed.py"])
async def test_seed_output_path_is_confined(project, output):
    res = await agents.create_model(
        "blog", "Post", [{"name": "title", "type": "CharField"}], project_id=project
    )
    assert res.success, res.message
    res = await agents.generate_seed_script("blog", output_path=output, project_id=project)
    assert not res.success
    assert res.data["error_code"] == "outside_project_root"
    assert not (project.parent / "escape_seed.py").exists()


async def test_export_openapi_output_path_is_confined(project):
    res = await agents.export_openapi(output_path="/tmp/zeeb_openapi.json", project_id=project)
    assert not res.success
    assert res.data["error_code"] == "outside_project_root"


async def test_generate_requirements_output_path_is_confined(project):
    res = await agents.generate_requirements(output_path="../reqs.txt", project_id=project)
    assert not res.success
    assert res.data["error_code"] == "outside_project_root"
    assert not (project.parent / "reqs.txt").exists()


async def test_generated_test_filename_is_confined(project):
    entity = {
        "name": "Post",
        "prefix": "posts",
        "exposed": True,
        "permission": ["AllowAny"],
        "operations": ["list"],
        "fields": [{"name": "title", "type": "CharField", "max_length": 20}],
    }
    res = await agents.generate_tests(
        "blog", [entity], filename="../escape_test.py", project_id=project
    )
    assert not res.success
    assert res.data["error_code"] == "outside_project_root"
    assert not (project.parent / "escape_test.py").exists()


async def test_log_tools_are_confined(project, tmp_path):
    victim = tmp_path / "victim.log"
    victim.write_text("do not truncate\n")
    res = await agents.clear_logs(log_file=str(victim), project_id=project)
    assert not res.success and res.data["error_code"] == "outside_project_root"
    res = await agents.clear_logs(log_file="../victim.log", project_id=project)
    assert not res.success
    res = await agents.read_logs(log_file=str(victim), project_id=project)
    assert not res.success and res.data["error_code"] == "outside_project_root"
    res = await agents.search_logs("x", log_file=str(victim), project_id=project)
    assert not res.success
    assert victim.read_text() == "do not truncate\n"


async def test_class_edit_file_is_confined(project):
    res = await agents.set_class_method(
        "blog", "Post", "go", "def go(self):\n    pass", file="../../manage.py", project_id=project
    )
    assert not res.success
    assert res.data["error_code"] == "outside_project_root"


@pytest.mark.parametrize("project_id", ["..", ".", "../other", "/etc", "a/b"])
async def test_default_resolver_refuses_ids_outside_the_workspace(
    tmp_path, monkeypatch, project_id
):
    from zeeb_agents._utils import resolver

    monkeypatch.setattr(resolver, "_resolver", None)  # the built-in default
    monkeypatch.setenv("ZEEB_WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    (tmp_path / "other").mkdir()
    res = await agents.list_apps(project_id=project_id)
    assert not res.success
    assert res.data["error_code"] == "invalid_input"


async def test_default_resolver_still_resolves_a_plain_id(tmp_path, monkeypatch):
    from zeeb_agents._utils import resolver

    monkeypatch.setattr(resolver, "_resolver", None)
    monkeypatch.setenv("ZEEB_WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "proj-1.a").mkdir()
    assert resolver.resolve_project_id("proj-1.a") == tmp_path / "proj-1.a"


def test_archive_path_refuses_non_identifier_feature(tmp_path):
    from zeeb_agents._utils.errors import AgentError
    from zeeb_agents.feature_manifest import archive_path

    with pytest.raises(AgentError):
        archive_path(tmp_path, "../../etc")
    assert archive_path(tmp_path, "blog").name == "blog"


# ---------------------------------------------------------------------------
# 3. User rows come back under their own column names
# ---------------------------------------------------------------------------


@pytest.fixture
async def user_project(project: Path) -> Path:
    """A project whose sqlite database has a user table with many columns."""
    import sqlite3

    db_path = project / "users.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE accounts_user ("
        "id CHAR(32) PRIMARY KEY, email TEXT UNIQUE, first_name TEXT DEFAULT '', "
        "last_name TEXT DEFAULT '', username TEXT DEFAULT '', password TEXT NOT NULL, "
        "is_active BOOLEAN, is_staff BOOLEAN, is_superuser BOOLEAN, date_joined TEXT)"
    )
    conn.commit()
    conn.close()
    settings_py = project / "demo" / "settings.py"
    settings_py.write_text(
        settings_py.read_text() + f'\nDATABASE = {{"url": "sqlite+aiosqlite:///{db_path}"}}\n'
    )
    return project


def _stored_hash(project: Path, email: str) -> str:
    import sqlite3

    conn = sqlite3.connect(project / "users.sqlite3")
    try:
        return conn.execute(
            "SELECT password FROM accounts_user WHERE email = ?", (email,)
        ).fetchone()[0]
    finally:
        conn.close()


async def test_update_user_returns_values_under_their_own_columns(user_project):
    created = await agents.create_user("ada@example.com", "s3cret-pass", project_id=user_project)
    assert created.success, created.message
    res = await agents.update_user(
        "ada@example.com", {"first_name": "Ada", "last_name": "Lovelace"}, project_id=user_project
    )
    assert res.success, res.message
    assert res.data["email"] == "ada@example.com"
    assert res.data["first_name"] == "Ada"
    assert res.data["last_name"] == "Lovelace"
    assert res.data["id"] == created.data["id"]
    stored = _stored_hash(user_project, "ada@example.com")
    assert "password" not in res.data
    assert stored not in res.data.values()


async def test_update_user_reports_a_missing_user(user_project):
    res = await agents.update_user(
        "ghost@example.com", {"first_name": "Nobody"}, project_id=user_project
    )
    assert not res.success
    assert res.data["error_code"] == "user_not_found"


async def test_user_tools_accept_a_uuid_id_string(user_project):
    import uuid

    created = await agents.create_user("grace@example.com", "s3cret-pass", project_id=user_project)
    assert created.success, created.message
    dashed = str(uuid.UUID(created.data["id"]))

    got = await agents.get_user(dashed, project_id=user_project)
    assert got.success and got.data["email"] == "grace@example.com"
    res = await agents.update_user(dashed, {"email": "hopper@example.com"}, project_id=user_project)
    assert res.success, res.message
    assert res.data["email"] == "hopper@example.com"
    res = await agents.set_user_password(created.data["id"], "n3w-pass!", project_id=user_project)
    assert res.success, res.message
    res = await agents.delete_user(dashed, project_id=user_project)
    assert res.success and res.data["deleted"] == 1
    res = await agents.get_user(dashed, project_id=user_project)
    assert not res.success and res.data["error_code"] == "user_not_found"


# ---------------------------------------------------------------------------
# 4. Subprocess tools: no option injection, always a deadline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("--basetemp=/tmp/zeeb-victim", "invalid_input"),
        ("-p no:cacheprovider", "invalid_input"),
        ("../outside_tests", "outside_project_root"),
        ("/etc", "outside_project_root"),
    ],
)
async def test_run_tests_refuses_options_and_escapes(project, path, code):
    res = await agents.run_tests(path, project_id=project)
    assert not res.success
    assert res.data["error_code"] == code
    assert "returncode" not in res.data  # pytest was never started


async def test_run_tests_reports_the_verdict_and_honours_a_deadline(project):
    tests_dir = project / "tests"
    (tests_dir / "test_hardening_ok.py").write_text("def test_ok():\n    assert True\n")
    (tests_dir / "test_hardening_slow.py").write_text(
        "import time\n\n\ndef test_slow():\n    time.sleep(60)\n"
    )
    res = await agents.run_tests("tests/test_hardening_ok.py::test_ok", project_id=project)
    assert res.success, res.data["output"]
    assert res.data["all_passed"] is True and res.data["timed_out"] is False

    import time

    started = time.monotonic()
    res = await agents.run_tests("tests/test_hardening_slow.py", timeout=3, project_id=project)
    assert time.monotonic() - started < 30
    assert not res.success
    assert res.data["timed_out"] is True
    assert res.data["returncode"] is None
    assert res.data["all_passed"] is False
    res = await agents.run_tests(timeout=0, project_id=project)
    assert not res.success and res.data["error_code"] == "invalid_input"


async def test_run_management_command_kills_the_whole_group_at_the_deadline(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    # The command starts a grandchild that holds the output pipes open: killing
    # only the direct child would leave the tool waiting for it.
    (root / "manage.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print('started', flush=True)\n"
        "time.sleep(60)\n"
    )
    import time

    started = time.monotonic()
    res = await agents.run_management_command("anything", timeout=2, project_id=root)
    assert time.monotonic() - started < 30
    assert not res.success
    assert res.data["timed_out"] is True
    assert res.data["returncode"] is None
    assert "started" in res.data["output"]


async def test_run_management_command_closes_stdin(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "manage.py").write_text("import sys\nprint(repr(sys.stdin.read()))\n")
    res = await agents.run_management_command("prompt", timeout=30, project_id=root)
    assert res.success, res.data
    assert "''" in res.data["output"]


# ---------------------------------------------------------------------------
# 5. generate_requirements freezes the running interpreter, not PATH's pip
# ---------------------------------------------------------------------------


async def test_generate_requirements_ignores_the_pip_on_path(project, tmp_path, monkeypatch):
    import os
    import stat

    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    fake_pip = fake_bin / "pip"
    fake_pip.write_text("#!/bin/sh\necho 'not-this-environment==6.6.6'\n")
    fake_pip.chmod(fake_pip.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    res = await agents.generate_requirements(project_id=project)
    assert res.success, res.message
    written = (project / "requirements.txt").read_text()
    assert "not-this-environment" not in written
    assert "sqlalchemy" in written.lower()


# ---------------------------------------------------------------------------
# 6. set_env cannot inject keys and keeps the file as written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["x\nDEBUG=True", "x\rDEBUG=True", "x\x00y"])
async def test_set_env_refuses_line_breaks(project, value):
    before = (project / ".env").read_text()
    res = await agents.set_env("API_KEY", value, project_id=project)
    assert not res.success
    assert res.data["error_code"] == "invalid_input"
    assert (project / ".env").read_text() == before


async def test_set_env_preserves_comments_and_other_lines(project):
    env = project / ".env"
    env.write_text("# Database settings\nDEBUG=true\n\n# secret, keep me\nexport SECRET_KEY=abc\n")
    res = await agents.set_env("DEBUG", "false", project_id=project)
    assert res.success and res.data["action"] == "updated"
    res = await agents.set_env("NEW_KEY", "value", project_id=project)
    assert res.success and res.data["action"] == "added"
    res = await agents.set_env("SECRET_KEY", "xyz", project_id=project)
    assert res.success
    assert env.read_text() == (
        "# Database settings\nDEBUG=false\n\n# secret, keep me\n"
        "export SECRET_KEY=xyz\nNEW_KEY=value\n"
    )
    res = await agents.delete_env("NEW_KEY", project_id=project)
    assert res.success
    assert env.read_text() == (
        "# Database settings\nDEBUG=false\n\n# secret, keep me\nexport SECRET_KEY=xyz\n"
    )


@pytest.mark.parametrize("value", ["  padded  ", "a #not-a-comment", "it's #1", 'say "hi" #x', ""])
async def test_set_env_values_read_back_unchanged(project, value):
    from zeeb_api.conf.env import parse_env

    res = await agents.set_env("TRICKY", value, project_id=project)
    assert res.success, res.message
    assert parse_env((project / ".env").read_text())["TRICKY"] == value
    got = await agents.get_env(project_id=project)
    assert got.data["env"]["TRICKY"] == value
