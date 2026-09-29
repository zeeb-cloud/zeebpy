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
