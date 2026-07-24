"""Characterization tests for the ``zeeb_agents`` package.

All public agent functions return :class:`zeeb_agents.AgentResult` and must
never raise — failures are reported as ``AgentResult(success=False, ...)``.

Intentionally untested modules (runtime / subprocess / network paths):

- ``testing.py`` (``run_tests``) — spawns a nested pytest subprocess; running
  pytest-inside-pytest is slow and recursive.
- ``tasks.py`` *runtime* execution and ``deploy.py``'s
  ``generate_requirements`` — depend on schedulers / ``pip freeze``
  subprocesses and the ambient environment.  (The pure scaffolding parts of
  ``tasks.py`` are covered below.)
- ``shell.py`` (``run_management_command``) — subprocess invocation of
  ``manage.py`` against a real interpreter environment.

These would be integration tests against external processes, not
deterministic filesystem characterization tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import zeeb_agents as agents
from zeeb_agents import AgentResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def project(tmp_path: Path) -> Path:
    """A real scaffolded project (via create_project) with one app 'blog'."""
    res = await agents.create_project("demo", directory=str(tmp_path))
    assert res.success, res.message
    root = tmp_path / "demo"
    res = await agents.create_app("blog", project_id=root)
    assert res.success, res.message
    return root


@pytest.fixture
async def db_project(project: Path) -> Path:
    """Project whose settings point at a pre-seeded sqlite database."""
    db_path = project / "test.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE posts ("
        "id INTEGER PRIMARY KEY, "
        "title TEXT NOT NULL, "
        "created_at TEXT, "
        "updated_at TEXT)"
    )
    conn.executemany(
        "INSERT INTO posts (title, created_at, updated_at) VALUES (?, ?, ?)",
        [
            ("first", "2026-01-01", "2026-01-02"),
            ("second", "2026-02-01", "2026-02-02"),
            ("third", "2026-03-01", None),
        ],
    )
    conn.commit()
    conn.close()

    settings_py = project / "demo" / "settings.py"
    settings_py.write_text(
        settings_py.read_text()
        + f'\nDATABASE = {{"url": "sqlite+aiosqlite:///{db_path}"}}\n'
    )
    return project


def _post_count(project: Path) -> int:
    conn = sqlite3.connect(project / "test.sqlite3")
    try:
        return conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# AgentResult semantics
# ---------------------------------------------------------------------------


def test_agent_result_truthiness():
    assert AgentResult(success=True, message="ok")
    assert not AgentResult(success=False, message="nope")
    assert AgentResult(success=True, message="ok").data is None


async def test_missing_project_id_fails_gracefully(tmp_path, monkeypatch):
    """Without a project_id, project-operating tools fail, never raise."""
    monkeypatch.chdir(tmp_path)
    for fn in (
        agents.list_apps,
        agents.get_project_info,
        agents.get_settings,
        agents.list_tables,
    ):
        result = await fn(project_id=None)
        assert isinstance(result, AgentResult)
        assert result.success is False
        assert result.data["error_code"] == "no_project_id"


async def test_project_root_alias_matches_project_id(project):
    """`project_root=<Path>` is accepted as an alias for `project_id` and
    yields identical results — the bridge the codegen engine relies on."""
    via_id = await agents.list_apps(project_id=project)
    via_root = await agents.list_apps(project_root=project)
    assert via_id.success and via_root.success
    assert via_id.data == via_root.data

    # A write path behaves identically regardless of the spelling used.
    made = await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    assert made.success, made.message
    listed = await agents.list_models(project_root=project)
    assert listed.success
    assert any(m["model"] == "Post" for m in listed.data["models"])


async def test_explicit_project_id_wins_over_project_root(project, tmp_path):
    """When both are supplied, `project_id` takes precedence over the alias."""
    bogus = tmp_path / "does-not-exist"
    result = await agents.list_apps(project_id=project, project_root=bogus)
    assert result.success
    assert result.data["apps"] == ["blog"]


# ---------------------------------------------------------------------------
# Project scaffolding
# ---------------------------------------------------------------------------


async def test_create_project(tmp_path):
    result = await agents.create_project("myproj", directory=str(tmp_path))
    assert result.success
    root = tmp_path / "myproj"
    assert (root / "manage.py").exists()
    assert (root / "myproj" / "settings.py").exists()
    assert (root / "apps" / "__init__.py").exists()
    assert result.data["path"] == str(root)
    settings = (root / "myproj" / "settings.py").read_text()
    assert "DATABASE" in settings
    assert "INSTALLED_APPS" in settings


async def test_create_project_duplicate_fails(tmp_path):
    assert (await agents.create_project("dup", directory=str(tmp_path))).success
    result = await agents.create_project("dup", directory=str(tmp_path))
    assert not result.success


async def test_create_app(project):
    app_dir = project / "apps" / "blog"
    for name in ("__init__.py", "models.py", "serializers.py", "views.py", "urls.py"):
        assert (app_dir / name).exists(), name
    assert "from zeeb_orm import Model, fields" in (app_dir / "models.py").read_text()


async def test_list_apps_and_project_info(project):
    result = await agents.list_apps(project_id=project)
    assert result.success
    assert result.data["apps"] == ["blog"]

    info = await agents.get_project_info(project_id=project)
    assert info.success
    assert info.data["project_package"] == "demo"
    assert info.data["apps"] == ["blog"]
    assert "sqlite" in info.data["database_url"]


async def test_get_project_structure(project):
    result = await agents.get_project_structure(project_id=project, max_depth=3)
    assert result.success
    assert result.data["tree"]["name"] == "demo"
    assert result.data["file_count"] > 0
    top_level = {child["name"] for child in result.data["tree"]["children"]}
    assert "manage.py" in top_level
    assert "apps" in top_level


# ---------------------------------------------------------------------------
# Models / serializers / viewsets / CRUD
# ---------------------------------------------------------------------------

POST_FIELDS = [
    {"name": "title", "type": "CharField", "max_length": 200},
    {"name": "body", "type": "TextField"},
    {"name": "published", "type": "BooleanField", "default": False},
]


async def test_create_model(project):
    result = await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    assert result.success, result.message
    content = (project / "apps" / "blog" / "models.py").read_text()
    assert "class Post(Model):" in content
    assert "title = fields.CharField(max_length=200)" in content
    assert "body = fields.TextField()" in content
    assert 'table_name = "blog_post"' in content
    assert result.data["fields"] == ["title", "body", "published"]


async def test_create_model_duplicate_fails(project):
    assert (await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)).success
    result = await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    assert not result.success
    assert "already exists" in result.message


async def test_add_field(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    result = await agents.add_field(
        "blog", "Post", {"name": "views", "type": "IntegerField", "default": 0},
        project_id=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "models.py").read_text()
    assert "views = fields.IntegerField(default=0)" in content


async def test_add_field_missing_model_fails(project):
    result = await agents.add_field(
        "blog", "Nope", {"name": "x", "type": "IntegerField"}, project_id=project
    )
    assert not result.success
    assert "not found" in result.message


async def test_create_serializer(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    result = await agents.create_serializer(
        "blog", "Post",
        fields=["id", "title", "body"],
        read_only_fields=["id"],
        project_id=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "serializers.py").read_text()
    assert "class PostSerializer(ModelSerializer):" in content
    assert "from .models import Post" in content
    assert '"title"' in content


async def test_create_viewset(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset("blog", "Post", project_id=project)
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "class PostViewSet(ModelViewSet):" in content
    assert "serializer_class = PostSerializer" in content
    assert "permissions.IsAuthenticatedOrReadOnly" in content


async def test_generate_crud(project):
    result = await agents.generate_crud("blog", "Article", POST_FIELDS, project_id=project)
    assert result.success, result.message
    assert len(result.data["steps"]) == 4
    app_dir = project / "apps" / "blog"
    assert "class Article(Model):" in (app_dir / "models.py").read_text()
    assert "class ArticleSerializer(ModelSerializer):" in (app_dir / "serializers.py").read_text()
    assert "class ArticleViewSet(ModelViewSet):" in (app_dir / "views.py").read_text()
    # Default prefix is the pluralized model name, not the app name — every
    # ViewSet in an app gets its own segment.
    assert 'router.register("articles", ArticleViewSet)' in (app_dir / "urls.py").read_text()


async def test_create_route_generates_valid_mounted_handler(project):
    """create_route emits importable, mounted code with the supplied body."""
    import ast

    result = await agents.create_route(
        "blog", "/posts/featured", "get", "get_featured_posts",
        imports=["from .models import Post"],
        body="""
            posts = await Post.objects.filter(featured=True).all()
            return [{"id": str(p.id)} for p in posts]
        """,
        project_id=project,
    )
    assert result.success, result.message
    assert result.data["wired"] is True

    app_dir = project / "apps" / "blog"
    views = (app_dir / "views.py").read_text()
    urls = (app_dir / "urls.py").read_text()
    # Both files must stay syntactically valid Python.
    ast.parse(views)
    ast.parse(urls)
    # Uses FastAPI's APIRouter, NOT the nonexistent ``from zeeb_api import Router``.
    assert "from fastapi import APIRouter, Request" in views
    assert "router = APIRouter()" in views
    assert "from zeeb_api import Router" not in views
    # request is typed (otherwise FastAPI treats it as a query param).
    assert "async def get_featured_posts(request: Request):" in views
    # Caller's imports + body are present.
    assert "from .models import Post" in views
    assert "await Post.objects.filter(featured=True).all()" in views
    # Auto-wired into urls.py so the route is actually served.
    assert "from .views import router as blog_api_router" in urls
    assert "router.include(blog_api_router)" in urls


async def test_create_route_default_body_and_idempotent_wiring(project):
    r1 = await agents.create_route("blog", "/ping", "get", "ping", project_id=project)
    assert r1.success, r1.message
    r2 = await agents.create_route(
        "blog", "/items/{item_id}", "get", "get_item", project_id=project
    )
    assert r2.success, r2.message
    app_dir = project / "apps" / "blog"
    views = (app_dir / "views.py").read_text()
    urls = (app_dir / "urls.py").read_text()
    # Placeholder body is valid and returns something useful (not bare ``pass``).
    assert 'return {"message": "ping"}' in views
    # Path params are extracted and typed.
    assert "async def get_item(request: Request, item_id: str):" in views
    # Wiring is added exactly once across multiple routes.
    assert urls.count("router.include(blog_api_router)") == 1


async def test_create_route_invalid_method_fails(project):
    result = await agents.create_route(
        "blog", "/x", "options", "do_x", project_id=project
    )
    assert not result.success
    assert "Invalid method" in result.message


async def test_add_viewset_action_with_body(project):
    import ast

    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_viewset("blog", "Post", project_id=project)
    result = await agents.add_viewset_action(
        "blog", "Post", "publish", detail=True, methods=["post"],
        body="""
            post = await self.get_object()
            post.published = True
            await post.save()
            return {"status": "published"}
        """,
        project_id=project,
    )
    assert result.success, result.message
    views = (project / "apps" / "blog" / "views.py").read_text()
    ast.parse(views)
    assert '@action(detail=True, methods=["post"])' in views
    assert "async def publish(self, request, pk=None):" in views
    assert "post = await self.get_object()" in views
    assert 'return {"status": "published"}' in views
    # No leftover placeholder when a body was supplied.
    assert "TODO: implement publish" not in views


async def test_add_viewset_action_full_surface(project):
    import ast

    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_viewset("blog", "Post", project_id=project)
    result = await agents.add_viewset_action(
        "blog",
        "Post",
        "publish",
        detail=True,
        methods=["post"],
        url_path="publish-now",
        request_serializer="PublishSerializer",
        response_serializer="PostSerializer",
        permission="IsAdminUser",
        project_id=project,
    )
    assert result.success, result.message
    assert result.data["url_path"] == "publish-now"
    assert result.data["wiring"] == {
        "request_serializer": "PublishSerializer",
        "response_serializer": "PostSerializer",
        "permission": "IsAdminUser",
    }

    views = (project / "apps" / "blog" / "views.py").read_text()
    ast.parse(views)
    # Every wired kwarg appears on the decorator.
    assert 'url_path="publish-now"' in views
    assert "request_serializer=PublishSerializer" in views
    assert "response_serializer=PostSerializer" in views
    assert "permission_classes=[permissions.IsAdminUser]" in views
    # Serializer-aware scaffold: validated input in, serialized output out.
    assert "serializer = PublishSerializer(data=self.get_action_request_body())" in views
    assert "serializer.is_valid(raise_exception=True)" in views
    assert "instance = await self.get_object()" in views
    assert "return PostSerializer(instance).data" in views
    # Imports for the referenced serializer classes are wired in.
    assert "from .serializers import PublishSerializer" in views
    assert "from .serializers import PostSerializer" in views


async def test_add_viewset_action_invalid_permission(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_viewset("blog", "Post", project_id=project)
    result = await agents.add_viewset_action(
        "blog", "Post", "publish", permission="IsAdmin",
        project_id=project,
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_permission"
    assert "IsAdminUser" in result.data["suggestions"]


# ---------------------------------------------------------------------------
# Config: .env + settings
# ---------------------------------------------------------------------------


async def test_env_round_trip(project):
    # No .env yet
    result = await agents.get_env(project_id=project)
    assert not result.success

    result = await agents.set_env("API_KEY", "abc123", project_id=project)
    assert result.success
    assert result.data["action"] == "added"
    assert "API_KEY=abc123" in (project / ".env").read_text()

    result = await agents.set_env("API_KEY", "xyz", project_id=project)
    assert result.success
    assert result.data["action"] == "updated"

    result = await agents.get_env(project_id=project)
    assert result.success
    assert result.data["env"] == {"API_KEY": "xyz"}

    result = await agents.delete_env("API_KEY", project_id=project)
    assert result.success
    result = await agents.get_env(project_id=project)
    assert result.success is True or result.data["env"] == {}

    result = await agents.delete_env("MISSING", project_id=project)
    assert not result.success
    assert "not found" in result.message


async def test_get_settings(project):
    result = await agents.get_settings(project_id=project)
    assert result.success
    settings = result.data["settings"]
    assert "sqlite" in settings["DATABASE"]["url"]
    assert isinstance(settings["INSTALLED_APPS"], list)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


async def test_write_and_read_file(project):
    result = await agents.write_file("notes/hello.txt", "hi there", project_id=project)
    assert result.success
    assert result.data["action"] == "created"

    result = await agents.read_file("notes/hello.txt", project_id=project)
    assert result.success
    assert result.data["content"] == "hi there"

    result = await agents.write_file("notes/hello.txt", "again", project_id=project)
    assert result.success
    assert result.data["action"] == "updated"


async def test_read_file_missing(project):
    result = await agents.read_file("nope/missing.txt", project_id=project)
    assert not result.success
    assert "not found" in result.message.lower()


async def test_list_files(project):
    result = await agents.list_files(".", pattern="*.py", project_id=project)
    assert result.success
    names = {e["name"] for e in result.data["entries"]}
    assert "manage.py" in names


async def test_search_code(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    result = await agents.search_code("class Post", glob="apps/**/*.py", project_id=project)
    assert result.success
    assert result.data["total_matches"] >= 1
    files = {f["file"] for f in result.data["files"]}
    assert "apps/blog/models.py" in files


async def test_search_code_bad_regex(project):
    result = await agents.search_code("(*invalid", project_id=project)
    assert not result.success


async def test_read_file_outside_project_root_fails(tmp_path):
    """Security: paths escaping the project root are rejected, never raised."""
    root = tmp_path / "proj"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")

    result = await agents.read_file(str(secret), project_id=root)
    assert isinstance(result, AgentResult)
    assert not result.success
    assert "outside the project root" in result.message

    result = await agents.read_file("../secret.txt", project_id=root)
    assert not result.success
    assert "outside the project root" in result.message


async def test_write_file_outside_project_root_fails(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    result = await agents.write_file("../evil.txt", "x", project_id=root)
    assert not result.success
    assert "outside the project root" in result.message
    assert not (tmp_path / "evil.txt").exists()


# ---------------------------------------------------------------------------
# Permission scaffolding
# ---------------------------------------------------------------------------


async def test_create_and_list_permission_classes(project):
    result = await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_id=project
    )
    assert result.success, result.message
    perms = project / "apps" / "blog" / "permissions.py"
    content = perms.read_text()
    assert "class IsPostOwner(BasePermission):" in content
    assert "has_object_permission" in content

    # Duplicate rejected
    result = await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_id=project
    )
    assert not result.success
    assert "already exists" in result.message

    # Unknown preset rejected
    result = await agents.create_permission_class(
        "blog", "Whatever", logic="not_a_preset", project_id=project
    )
    assert not result.success

    result = await agents.list_permission_classes("blog", project_id=project)
    assert result.success
    assert result.data["permissions"] == ["IsPostOwner"]


async def test_list_permission_classes_no_file(project):
    result = await agents.list_permission_classes("blog", project_id=project)
    assert result.success
    assert result.data["permissions"] == []


# ---------------------------------------------------------------------------
# Database introspection (functions build their own sync engine from settings)
# ---------------------------------------------------------------------------


async def test_list_tables(db_project):
    result = await agents.list_tables(project_id=db_project)
    assert result.success
    assert "posts" in result.data["tables"]


async def test_describe_table(db_project):
    result = await agents.describe_table("posts", project_id=db_project)
    assert result.success
    cols = {c["name"] for c in result.data["columns"]}
    assert {"id", "title", "created_at", "updated_at"} <= cols


async def test_run_query_select(db_project):
    result = await agents.run_query(
        "SELECT id, title FROM posts ORDER BY id", project_id=db_project
    )
    assert result.success, result.message
    assert result.data["count"] == 3
    assert result.data["rows"][0]["title"] == "first"


async def test_run_query_created_at_column_is_not_false_positive(db_project):
    """Word-boundary check: created_at/updated_at must not trip 'create'/'update'."""
    result = await agents.run_query(
        "SELECT created_at, updated_at FROM posts WHERE created_at IS NOT NULL",
        project_id=db_project,
    )
    assert result.success, result.message
    assert result.data["count"] == 3


async def test_run_query_with_cte(db_project):
    result = await agents.run_query(
        "WITH t AS (SELECT title FROM posts) SELECT * FROM t ORDER BY title",
        project_id=db_project,
    )
    assert result.success, result.message
    assert result.data["count"] == 3


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM posts",
        "INSERT INTO posts (title) VALUES ('hacked')",
        "UPDATE posts SET title = 'hacked'",
        "DROP TABLE posts",
        "WITH t AS (SELECT 1) DELETE FROM posts",
        "EXPLAIN ANALYZE DELETE FROM posts",
        "SELECT 1; DROP TABLE posts",
        "-- sneaky comment\nDELETE FROM posts",
        "/* sneaky */ DELETE FROM posts",
        "SELECT 1 /* hide */; DROP TABLE posts",
        "PRAGMA writable_schema = ON",
        "ATTACH DATABASE '/tmp/x.db' AS x",
        "VACUUM",
    ],
)
async def test_run_query_rejects_mutations(db_project, sql):
    result = await agents.run_query(sql, project_id=db_project)
    assert isinstance(result, AgentResult)
    assert not result.success, f"should have rejected: {sql!r}"
    # Table is untouched
    assert _post_count(db_project) == 3


async def test_run_query_rolls_back(db_project):
    """Even an allowed query runs in a transaction that is always rolled back."""
    result = await agents.run_query("SELECT COUNT(*) AS n FROM posts", project_id=db_project)
    assert result.success
    assert result.data["rows"][0]["n"] == 3
    assert _post_count(db_project) == 3


# ---------------------------------------------------------------------------
# Signal scaffolding (filesystem only)
# ---------------------------------------------------------------------------


async def test_signal_receiver_round_trip(project):
    result = await agents.create_signal_receiver(
        "blog", "post_save", "Post", "notify_on_save", project_id=project
    )
    assert result.success, result.message
    signals_py = project / "apps" / "blog" / "signals.py"
    content = signals_py.read_text()
    assert "@receiver(post_save, sender=Post)" in content
    assert "async def notify_on_save" in content

    result = await agents.list_signal_receivers("blog", project_id=project)
    assert result.success
    assert result.data["count"] == 1
    assert result.data["receivers"][0]["func_name"] == "notify_on_save"

    result = await agents.create_signal_receiver(
        "blog", "bogus_signal", "Post", "x", project_id=project
    )
    assert not result.success


# ---------------------------------------------------------------------------
# Tool discovery — list_capabilities
# ---------------------------------------------------------------------------


async def test_list_capabilities_covers_all_exports():
    from zeeb_agents.capabilities import _NON_FUNCTION_EXPORTS

    result = await agents.list_capabilities()
    assert result.success, result.message
    names = {t["name"] for t in result.data["tools"]}
    # Every callable export except non-tool exports (result type, URI registry,
    # and the vendor-config hooks configure/set_project_resolver).
    expected = {
        n
        for n in agents.__all__
        if n not in _NON_FUNCTION_EXPORTS and callable(getattr(agents, n))
    }
    assert expected <= names
    # The config hooks must NOT be advertised as tools.
    assert "configure" not in names and "set_project_resolver" not in names
    # Retired server tools must be gone.
    assert names.isdisjoint({"start_server", "stop_server", "get_server_status"})
    assert result.data["count"] == len(result.data["tools"])
    # Each entry has the documented shape; project_id is the public handle.
    entry = next(t for t in result.data["tools"] if t["name"] == "create_model")
    assert entry["module"] == "models"
    assert entry["category"] == "Model Management"
    assert "project_id" in entry["signature"]
    assert entry["summary"]
    assert [p["name"] for p in entry["params"]][-1] == "project_id"
    assert any(r["key"] == "app" for r in entry["returns"])
    assert "doc" not in entry  # omitted unless include_docstrings=True
    assert "categories" in result.data


async def test_list_capabilities_with_docstrings_and_module_filter():
    result = await agents.list_capabilities(include_docstrings=True, module="models")
    assert result.success, result.message
    assert result.data["modules"] == ["models"]
    assert all(t["module"] == "models" for t in result.data["tools"])
    assert all("doc" in t for t in result.data["tools"])


# ---------------------------------------------------------------------------
# MCP resource dispatch
# ---------------------------------------------------------------------------


async def test_get_resource_principles_with_tool_prefix():
    result = await agents.get_resource("mcp://docs/principles", tool_prefix="zeeb_")
    assert result.success, result.message
    content = result.data["content"]
    assert result.data["uri"] == "mcp://docs/principles"
    assert result.data["mime_type"] == "text/markdown"
    # The {prefix} placeholder must be fully substituted.
    assert "{prefix}" not in content
    assert "zeeb_list_capabilities" in content


async def test_get_resource_unknown_uri_fails():
    result = await agents.get_resource("mcp://docs/nope")
    assert not result.success
    assert "nope" in result.message


async def test_resource_uris_registry_has_principles():
    assert agents.RESOURCE_URIS["principles"] == "mcp://docs/principles"


# ---------------------------------------------------------------------------
# agent_docs ↔ code drift guard
#
# The markdown in agent_docs/ *is* the instruction set coding agents follow,
# and capabilities.md hand-maintains signature tables. These tests keep those
# docs from silently drifting out of sync with the real function signatures.
# ---------------------------------------------------------------------------

import inspect  # noqa: E402
import re  # noqa: E402

_DOCS_DIR = Path(agents.__file__).parent / "agent_docs"


def _tool_funcs() -> dict:
    """Every callable public tool keyed by name (non-tool exports excluded)."""
    from zeeb_agents.capabilities import _NON_FUNCTION_EXPORTS

    return {
        n: getattr(agents, n)
        for n in agents.__all__
        if n not in _NON_FUNCTION_EXPORTS and callable(getattr(agents, n))
    }


def _strip_strings(text: str) -> str:
    """Blank quoted string contents (incl. multi-line triple-quoted) and ``#``
    comments so code / URLs / ``=`` inside example bodies and trailing comments
    (e.g. ``# ?search=…``) don't get mis-parsed as call kwargs."""
    text = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    text = re.sub(r"'''(?:.|\n)*?'''", "''", text)
    text = re.sub(r'"[^"\n]*"', '""', text)
    text = re.sub(r"'[^'\n]*'", "''", text)
    text = re.sub(r"#[^\n]*", "", text)  # strip comments (strings blanked first)
    return text


def _iter_calls(text: str, name_re: str):
    """Yield ``(name, args)`` for each call matching *name_re* (which captures the
    tool name and ends at the opening ``(``), balancing parens across newlines."""
    for m in re.finditer(name_re, text):
        depth, i, n = 1, m.end(), len(text)
        while i < n and depth:
            ch = text[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        yield m.group(1), text[m.end() : i - 1]


def _valid_params(func) -> set:
    """Public signature params plus any accepted-but-hidden aliases."""
    params = set(inspect.signature(func).parameters)
    params |= set(getattr(func, "__agent_aliases__", {}))
    return params


def _assert_kwargs_valid(label: str, funcs: dict, text: str, name_re: str) -> None:
    for name, args in _iter_calls(_strip_strings(text), name_re):
        if name not in funcs:
            continue  # non-tool call (framework API, builtin) — skip
        valid = _valid_params(funcs[name])
        for kw in re.findall(r"(\w+)\s*=", args):
            assert kw in valid, (
                f"{label}: {name}(...) uses unknown keyword '{kw}='; "
                f"valid params: {sorted(valid)}"
            )


# ``{prefix}name(`` in the MCP-served agent_docs; ``await name(`` in human docs/.
_PREFIX_CALL = r"\{prefix\}(\w+)\("
_AWAIT_CALL = r"await\s+(\w+)\("

_HUMAN_DOCS_DIR = Path(agents.__file__).resolve().parents[1] / "docs"


def test_capabilities_md_inventory_is_generated():
    """capabilities.md's inventory span must equal a fresh render (no drift)."""
    from zeeb_agents._utils.capabilities_doc import _DOCS_FILE, regenerate

    committed = _DOCS_FILE.read_text(encoding="utf-8")
    fresh = regenerate(write=False)
    assert committed == fresh, (
        "capabilities.md inventory is stale — regenerate with:\n"
        "  python -m zeeb_agents._utils.capabilities_doc --write"
    )


def test_agent_docs_reference_real_tools():
    """Every ``{prefix}name(`` call across the agent_docs names a real tool."""
    funcs = _tool_funcs()
    for md in sorted(_DOCS_DIR.rglob("*.md")):
        text = _strip_strings(md.read_text(encoding="utf-8"))
        for name, _ in _iter_calls(text, _PREFIX_CALL):
            assert name in funcs, f"{md.name} references unknown tool '{name}(...)'"


def test_agent_docs_example_kwargs_are_valid():
    """Keyword args in ``{prefix}name(...)`` examples (multi-line included) must
    be real params. Guards against the ``create_model(name=...)`` class of drift
    that the old single-line-only matcher silently skipped."""
    funcs = _tool_funcs()
    for md in sorted(_DOCS_DIR.rglob("*.md")):
        _assert_kwargs_valid(md.name, funcs, md.read_text(encoding="utf-8"), _PREFIX_CALL)


def test_human_docs_awaited_tool_calls_have_valid_kwargs():
    """`await <tool>(...)` examples in the human docs/ tree must use real params.

    Only awaited bare calls whose name is a known tool are checked, so framework
    API / builtin calls (``await authenticate(username=...)``) are skipped.
    """
    if not _HUMAN_DOCS_DIR.exists():
        pytest.skip("no docs/ tree")
    funcs = _tool_funcs()
    for md in sorted(_HUMAN_DOCS_DIR.rglob("*.md")):
        _assert_kwargs_valid(
            f"docs/{md.name}", funcs, md.read_text(encoding="utf-8"), _AWAIT_CALL
        )


def test_error_recovery_codes_are_real():
    """Every ``error_code`` cell in error-recovery.md is in the ERROR_CODES vocab."""
    from zeeb_agents._utils.errors import ERROR_CODES

    text = (_DOCS_DIR / "zeebpy" / "error-recovery.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"\|\s*`([a-z_]+)`\s*/?\s*`?([a-z_]*)`?\s*\|", text))
    codes = {c for pair in documented for c in pair if c} - {"error_code"}
    unknown = codes - set(ERROR_CODES)
    assert not unknown, f"error-recovery.md references unknown error codes: {sorted(unknown)}"


# ---------------------------------------------------------------------------
# project_id resolver seam
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_resolver():
    """Reset the module-global project resolver after a test that sets one."""
    yield
    agents.set_project_resolver(None)


async def test_resolver_maps_id_to_path(project, clean_resolver):
    """A registered resolver lets tools address the project by an opaque id."""
    agents.configure(project_resolver=lambda pid: project if pid == "P1" else None)
    result = await agents.list_apps(project_id="P1")
    assert result.success, result.message
    assert "blog" in result.data["apps"]


async def test_resolver_unknown_id_fails_project_not_found(project, clean_resolver):
    agents.set_project_resolver(lambda pid: None)
    result = await agents.list_apps(project_id="ghost")
    assert not result.success
    assert result.data["error_code"] == "project_not_found"


async def test_resolver_faulty_resolver_fails_gracefully(clean_resolver):
    def boom(pid):
        raise RuntimeError("db down")

    agents.set_project_resolver(boom)
    result = await agents.list_apps(project_id="X")
    assert not result.success
    assert result.data["error_code"] == "project_not_found"


async def test_path_passthrough_internal_forwarding(project):
    """A Path passed as project_id resolves to itself (internal forwarding)."""
    from zeeb_agents._utils.resolver import resolve_project_id

    assert resolve_project_id(project) == project
    # generate_crud forwards a resolved Path to its sub-tools internally.
    result = await agents.generate_crud(
        "blog",
        "Widget",
        [{"name": "name", "type": "CharField", "max_length": 50}],
        project_id=project,
    )
    assert result.success, result.message


# ---------------------------------------------------------------------------
# Platform runtime references
# ---------------------------------------------------------------------------


async def test_runtime_reference_unconfigured_fails(project):
    result = await agents.get_project_reference(project_id=project)
    assert not result.success
    assert result.data["error_code"] == "runtime_not_configured"

    result = await agents.get_openapi_url(project_id=project)
    assert not result.success
    assert result.data["error_code"] == "runtime_not_configured"


async def test_runtime_reference_reads_published_env(project):
    await agents.set_env("ZEEB_PREVIEW_URL", "https://p.preview.example", project_id=project)
    await agents.set_env(
        "ZEEB_OPENAPI_URL", "https://p.preview.example/openapi.json", project_id=project
    )
    ref = await agents.get_project_reference(project_id=project)
    assert ref.success, ref.message
    assert ref.data["preview_url"] == "https://p.preview.example"
    assert ref.data["openapi_url"] == "https://p.preview.example/openapi.json"

    url = await agents.get_openapi_url(project_id=project)
    assert url.success
    assert url.data["openapi_url"] == "https://p.preview.example/openapi.json"


# ---------------------------------------------------------------------------
# Framework-aware doc serving
# ---------------------------------------------------------------------------


async def test_new_resource_uris_resolve():
    for uri in ("mcp://docs/recipes", "mcp://docs/error-recovery"):
        result = await agents.get_resource(uri, tool_prefix="zeeb_")
        assert result.success, f"{uri}: {result.message}"
        assert "{prefix}" not in result.data["content"]
        assert result.data["mime_type"] == "text/markdown"


async def test_unknown_framework_falls_back_to_zeebpy():
    result = await agents.get_resource("mcp://docs/deployment", framework="nonesuch")
    assert result.success, result.message
    assert result.data["content"]


def test_detect_framework_reads_marker(tmp_path):
    from zeeb_agents._utils.project import detect_framework

    assert detect_framework(tmp_path) == "zeebpy"  # default when no marker
    (tmp_path / "pyproject.toml").write_text('[tool.zeeb]\nframework = "customfw"\n')
    assert detect_framework(tmp_path) == "customfw"


async def test_create_project_writes_framework_marker(tmp_path):
    res = await agents.create_project("fwproj", directory=str(tmp_path))
    assert res.success, res.message
    assert res.data["framework"] == "zeebpy"
    marker = (tmp_path / "fwproj" / "pyproject.toml").read_text()
    assert "[tool.zeeb]" in marker and 'framework = "zeebpy"' in marker


# ---------------------------------------------------------------------------
# CORS round-trip
# ---------------------------------------------------------------------------


async def test_cors_round_trip(project):
    result = await agents.get_cors_config(project_id=project)
    assert result.success
    assert isinstance(result.data["cors"], dict)

    result = await agents.configure_cors(
        ["https://app.example.com", "http://localhost:3000"],
        methods=["GET", "POST"],
        project_id=project,
    )
    assert result.success, result.message
    assert "CORS_ALLOW_ORIGINS" in result.data["keys_written"]

    result = await agents.get_cors_config(project_id=project)
    assert result.success
    cors = result.data["cors"]
    assert cors["CORS_ALLOW_ORIGINS"] == ["https://app.example.com", "http://localhost:3000"]
    assert cors["CORS_ALLOW_METHODS"] == ["GET", "POST"]


async def test_configure_cors_restores_missing_middleware(project):
    """CORS settings without CORSMiddleware are inert — configure_cors repairs (G5)."""
    settings_path = next(
        p / "settings.py" for p in project.iterdir() if (p / "settings.py").exists()
    )
    settings_path.write_text(
        "\n".join(
            line
            for line in settings_path.read_text().splitlines()
            if "CORSMiddleware" not in line
        )
        + "\n"
    )
    result = await agents.configure_cors(["http://localhost:3000"], project_id=project)
    assert result.success, result.message
    assert result.data["middleware_added"] is True
    assert '"zeeb_api.middleware.CORSMiddleware"' in settings_path.read_text()
    # Idempotent: already present on the second run.
    again = await agents.configure_cors(["http://localhost:3000"], project_id=project)
    assert again.success and again.data["middleware_added"] is False


async def test_configure_cors_empty_origins_warns(project):
    result = await agents.configure_cors([], project_id=project)
    assert result.success
    assert any("CORS_ALLOW_ORIGINS is empty" in w for w in result.data["warnings"])


# ---------------------------------------------------------------------------
# Logs — read / search / level filter
# ---------------------------------------------------------------------------


def _write_log(project: Path) -> None:
    logs_dir = project / "logs"
    logs_dir.mkdir(exist_ok=True)
    (logs_dir / "app.log").write_text(
        "2026-06-16 INFO server started\n"
        "2026-06-16 ERROR boom happened\n"
        "2026-06-16 INFO this line mentions NOTANERROR but is INFO\n"
        "2026-06-16 WARNING almost broke\n"
    )


async def test_read_logs_level_filter_is_token_matched(project):
    _write_log(project)
    result = await agents.read_logs(level="ERROR", project_id=project)
    assert result.success, result.message
    lines = result.data["lines"]
    # Exactly the one real ERROR line — the NOTANERROR INFO line must not match.
    assert len(lines) == 1
    assert "boom happened" in lines[0]


async def test_read_logs_no_file_fails(project):
    result = await agents.read_logs(project_id=project)
    assert not result.success
    assert result.data["searched_in"]


async def test_search_logs(project):
    _write_log(project)
    result = await agents.search_logs(r"boom", project_id=project)
    assert result.success
    assert result.data["count"] == 1
    assert result.data["matches"][0]["content"].endswith("boom happened")


# ---------------------------------------------------------------------------
# JSON Schema generation
# ---------------------------------------------------------------------------


async def test_get_model_json_schema(project):
    res = await agents.create_model(
        "blog",
        "Post",
        [
            {"name": "title", "type": "CharField", "max_length": 200},
            {"name": "body", "type": "TextField"},
        ],
        project_id=project,
    )
    assert res.success, res.message

    result = await agents.get_model_json_schema("blog", "Post", project_id=project)
    assert result.success, result.message
    schema = result.data["schema"]
    assert schema["title"] == "Post"
    assert schema["type"] == "object"
    assert "title" in schema["properties"]

    missing = await agents.get_model_json_schema("blog", "Nope", project_id=project)
    assert not missing.success


# ---------------------------------------------------------------------------
# Error/validation layer (errors.py, validation.py, decorator conversion)
# ---------------------------------------------------------------------------

from zeeb_agents._utils.errors import (  # noqa: E402
    ERROR_CODES,
    AgentError,
    fail,
)
from zeeb_agents._utils.validation import ensure_identifier  # noqa: E402


def test_fail_builds_error_code_payload():
    result = fail("boom", code="invalid_input", suggestions=["a"], extra=1)
    assert not result.success
    assert result.data["error_code"] == "invalid_input"
    assert result.data["suggestions"] == ["a"]
    assert result.data["extra"] == 1
    with pytest.raises(ValueError):
        fail("boom", code="not_a_real_code")


def test_ensure_identifier_rejects_bad_names():
    assert ensure_identifier("Post") == "Post"
    for bad in ("my model", "1abc", "class", "", None):
        with pytest.raises(AgentError):
            ensure_identifier(bad)


async def test_agent_error_converts_to_failure_result(project):
    # A typo'd app name is an expected failure: error_code + suggestions.
    result = await agents.create_model(
        "blgo", "Post", [{"name": "x", "type": "IntegerField"}], project_id=project
    )
    assert not result.success
    assert result.data["error_code"] == "app_not_found"
    assert "blog" in result.data["suggestions"]


def test_error_codes_documented_in_principles():
    """Drift guard: principles.md lists exactly the ERROR_CODES vocabulary."""
    text = (_DOCS_DIR / "zeebpy" / "principles.md").read_text(encoding="utf-8")
    section = text.split("### Failure `error_code` vocabulary")[1].split("##")[0]
    documented = set(re.findall(r"`(\w+)`", section))
    assert documented == set(ERROR_CODES), (
        f"missing from principles.md: {sorted(set(ERROR_CODES) - documented)}; "
        f"documented but not in ERROR_CODES: {sorted(documented - set(ERROR_CODES))}"
    )


# ---------------------------------------------------------------------------
# Field spec validation & rich rendering
# ---------------------------------------------------------------------------


async def test_create_model_unknown_field_type_suggests(project):
    result = await agents.create_model(
        "blog", "Post", [{"name": "title", "type": "CharFeild"}], project_id=project
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_field_spec"
    assert "CharField" in str(result.data["problems"])
    # Nothing was written
    content = (project / "apps" / "blog" / "models.py").read_text()
    assert "class Post" not in content


async def test_relation_without_to_fails_and_writes_nothing(project):
    result = await agents.create_model(
        "blog",
        "Post",
        [
            {"name": "title", "type": "CharField", "max_length": 10},
            {"name": "author", "type": "ForeignKey", "on_delete": "CASCADE"},
        ],
        project_id=project,
    )
    assert not result.success
    assert "requires 'to'" in result.message
    content = (project / "apps" / "blog" / "models.py").read_text()
    assert "class Post" not in content
    assert "fields.ForeignKey()" not in content


async def test_set_null_requires_null_true(project):
    result = await agents.create_model(
        "blog", "Post",
        [{"name": "editor", "type": "ForeignKey", "to": "User", "on_delete": "SET_NULL"}],
        project_id=project,
    )
    assert not result.success
    assert "null=True" in result.message


async def test_create_model_choices_raw_and_meta(project):
    result = await agents.create_model(
        "blog",
        "Post",
        [
            {"name": "title", "type": "CharField", "max_length": 200},
            {
                "name": "status", "type": "CharField", "max_length": 10,
                "choices": [["draft", "Draft"], ["pub", "Published"]],
                "default": "draft",
            },
            {
                "name": "score", "type": "IntegerField", "default": 0,
                "raw": {"validators": "[validators.MinValueValidator(0)]"},
            },
            {"name": "data", "type": "JSONField", "null": True},
        ],
        meta={
            "ordering": ["-title"],
            "unique_together": [["title", "status"]],
            "indexes": [{"fields": ["title"], "name": "idx_post_title"}],
            "constraints": [{"check": "score >= 0", "name": "ck_score"}],
        },
        project_id=project,
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "models.py"
    content = path.read_text()
    assert 'choices=[["draft", "Draft"], ["pub", "Published"]]' in content
    assert "validators=[validators.MinValueValidator(0)]" in content
    assert "from zeeb_orm import validators" in content
    assert 'unique_together = [["title", "status"]]' in content
    assert 'indexes = [{"fields": ["title"], "name": "idx_post_title"}]' in content
    compile(content, str(path), "exec")


async def test_create_model_unknown_meta_key_fails(project):
    result = await agents.create_model(
        "blog", "Post",
        [{"name": "title", "type": "CharField", "max_length": 10}],
        meta={"orderring": ["-title"]},
        project_id=project,
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_meta"
    assert "ordering" in result.data["suggestions"]


async def test_update_model_missing_model_fails(project):
    """Regression: renaming a nonexistent model used to report success."""
    result = await agents.update_model(
        "blog", "Nope", rename_to="StillNope", project_id=project
    )
    assert not result.success
    assert result.data["error_code"] == "model_not_found"


# ---------------------------------------------------------------------------
# Serializer extra fields / viewset options
# ---------------------------------------------------------------------------


async def test_create_serializer_extra_fields_and_validate_stub(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    result = await agents.create_serializer(
        "blog", "Post",
        fields=["id", "title"],
        extra_fields=[
            {"name": "summary", "type": "SerializerMethodField"},
            {"name": "secret", "type": "CharField", "write_only": True, "max_length": 8},
        ],
        validate_fields=["title"],
        project_id=project,
    )
    assert result.success, result.message
    assert result.data["extra_fields"] == ["summary", "secret"]
    path = project / "apps" / "blog" / "serializers.py"
    content = path.read_text()
    assert "summary = serializers.SerializerMethodField()" in content
    assert "def get_summary(self, obj):" in content
    assert 'secret = serializers.CharField(write_only=True, max_length=8)' in content
    assert "def validate_title(self, value):" in content
    # declared names are appended to the explicit fields list
    assert '"summary"' in content and '"secret"' in content
    compile(content, str(path), "exec")


async def test_create_serializer_unknown_extra_type_fails(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    result = await agents.create_serializer(
        "blog", "Post",
        extra_fields=[{"name": "x", "type": "MethodField"}],
        project_id=project,
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_field_type"


async def test_create_viewset_full_options(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset(
        "blog", "Post",
        pagination="page",
        throttles=["UserRateThrottle"],
        search_fields=["title"],
        ordering_fields=["title"],
        lookup_field="id",
        project_id=project,
    )
    assert result.success, result.message
    assert set(result.data["options"]) >= {
        "pagination_class", "throttle_classes", "search_fields",
        "ordering_fields", "filter_backends", "lookup_field",
    }
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert "pagination_class = PageNumberPagination" in content
    assert "throttle_classes = [UserRateThrottle]" in content
    assert "filter_backends = [SearchFilter, OrderingFilter]" in content
    assert "from zeeb_api.pagination import PageNumberPagination" in content
    assert "from zeeb_api.throttling import UserRateThrottle" in content
    compile(content, str(path), "exec")


async def test_create_viewset_read_only(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset("blog", "Post", read_only=True, project_id=project)
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "class PostViewSet(ReadOnlyModelViewSet):" in content
    assert "from zeeb_api.viewsets import ReadOnlyModelViewSet" in content


async def test_create_viewset_invalid_permission_suggests(project):
    result = await agents.create_viewset(
        "blog", "Post", permission="IsAuthenticatd", project_id=project
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_permission"
    assert "IsAuthenticated" in result.data["suggestions"]


async def test_update_viewset_sets_attrs(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_viewset("blog", "Post", project_id=project)
    result = await agents.update_viewset(
        "blog", "Post", permission="IsAdminUser", pagination="cursor",
        search_fields=["title"], project_id=project,
    )
    assert result.success, result.message
    assert result.data["changes"]
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert "permission_classes = [permissions.IsAdminUser]" in content
    assert "pagination_class = CursorPagination" in content
    assert "filter_backends = [SearchFilter]" in content
    compile(content, str(path), "exec")

    missing = await agents.update_viewset("blog", "Nope", permission="AllowAny",
                                          project_id=project)
    assert not missing.success


async def test_create_viewset_custom_permission_same_app(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    res = await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_id=project
    )
    assert res.success, res.message
    result = await agents.create_viewset(
        "blog", "Post", permission="IsPostOwner", project_id=project
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert "permission_classes = [IsPostOwner]" in content
    assert "from apps.blog.permissions import IsPostOwner" in content
    compile(content, str(path), "exec")


async def test_create_viewset_custom_permission_dotted_cross_app(project):
    await agents.create_app("accounts", project_id=project)
    res = await agents.create_permission_class(
        "accounts", "IsAccountAdmin", logic="staff_only", project_id=project
    )
    assert res.success, res.message
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset(
        "blog",
        "Post",
        permission="apps.accounts.permissions.IsAccountAdmin",
        project_id=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "permission_classes = [IsAccountAdmin]" in content
    assert "from apps.accounts.permissions import IsAccountAdmin" in content


async def test_create_viewset_dotted_permission_missing_class_fails(project):
    result = await agents.create_viewset(
        "blog",
        "Post",
        permission="apps.blog.permissions.DoesNotExist",
        project_id=project,
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_permission"
    assert "create_permission_class" in result.message


async def test_create_viewset_unknown_permission_suggests_custom_class(project):
    await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_id=project
    )
    result = await agents.create_viewset(
        "blog", "Post", permission="IsPostOwnr", project_id=project
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_permission"
    assert "IsPostOwner" in result.data["suggestions"]


async def test_update_viewset_custom_permission(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_viewset("blog", "Post", project_id=project)
    await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_id=project
    )
    result = await agents.update_viewset(
        "blog", "Post", permission="IsPostOwner", project_id=project
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert "permission_classes = [IsPostOwner]" in content
    assert "from apps.blog.permissions import IsPostOwner" in content
    compile(content, str(path), "exec")


async def test_add_viewset_action_custom_permission(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_viewset("blog", "Post", project_id=project)
    await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_id=project
    )
    result = await agents.add_viewset_action(
        "blog",
        "Post",
        "publish",
        methods=["post"],
        permission="IsPostOwner",
        project_id=project,
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert "permission_classes=[IsPostOwner]" in content
    assert "from apps.blog.permissions import IsPostOwner" in content
    compile(content, str(path), "exec")


# ---------------------------------------------------------------------------
# Multiple permission / authentication classes per viewset
# ---------------------------------------------------------------------------


async def test_create_viewset_multiple_permissions(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset(
        "blog", "Post",
        permission=["IsAuthenticated", "IsAdminUser"],
        project_id=project,
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert (
        "permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]"
        in content
    )
    compile(content, str(path), "exec")


async def test_create_viewset_permission_list_mixed_builtin_and_custom(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_id=project
    )
    result = await agents.create_viewset(
        "blog", "Post",
        permission=["IsAuthenticated", "IsPostOwner"],
        project_id=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "permission_classes = [permissions.IsAuthenticated, IsPostOwner]" in content
    assert "from apps.blog.permissions import IsPostOwner" in content


async def test_create_viewset_permission_list_dedupes_and_keeps_order(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset(
        "blog", "Post",
        permission=["IsAdminUser", "AllowAny", "IsAdminUser"],
        project_id=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "permission_classes = [permissions.IsAdminUser, permissions.AllowAny]" in content


async def test_create_viewset_permission_list_bad_entry_names_it(project):
    result = await agents.create_viewset(
        "blog", "Post",
        permission=["IsAuthenticated", "IsOwnr"],
        project_id=project,
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_permission"
    assert "'IsOwnr'" in result.message
    assert "IsOwner" in result.data["suggestions"]


async def test_create_viewset_empty_permission_list_fails(project):
    result = await agents.create_viewset(
        "blog", "Post", permission=[], project_id=project
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_input"


async def test_create_viewset_authentication_single(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset(
        "blog", "Post", authentication="JWTAuthentication", project_id=project
    )
    assert result.success, result.message
    assert "authentication_classes" in result.data["options"]
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert "authentication_classes = [authentication.JWTAuthentication]" in content
    assert "from zeeb_api import authentication" in content
    compile(content, str(path), "exec")


async def test_create_viewset_authentication_list(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset(
        "blog", "Post",
        authentication=["JWTAuthentication", "OAuth2BearerAuthentication"],
        project_id=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert (
        "authentication_classes = [authentication.JWTAuthentication, "
        "authentication.OAuth2BearerAuthentication]" in content
    )
    assert content.count("from zeeb_api import authentication") == 1


async def test_create_viewset_no_authentication_emits_no_attribute(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset("blog", "Post", project_id=project)
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "authentication_classes" not in content


async def test_create_viewset_invalid_authentication_suggests(project):
    result = await agents.create_viewset(
        "blog", "Post", authentication="JWTAuthentification", project_id=project
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_authentication"
    assert "JWTAuthentication" in result.data["suggestions"]


async def test_create_viewset_dotted_authentication_cross_app(project):
    await agents.create_app("accounts", project_id=project)
    auth_py = project / "apps" / "accounts" / "authentication.py"
    auth_py.write_text(
        "from zeeb_api.authentication import BaseAuthentication\n\n\n"
        "class ApiKeyAuthentication(BaseAuthentication):\n"
        "    async def authenticate(self, request):\n"
        "        return None\n"
    )
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    result = await agents.create_viewset(
        "blog", "Post",
        authentication="apps.accounts.authentication.ApiKeyAuthentication",
        project_id=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "authentication_classes = [ApiKeyAuthentication]" in content
    assert "from apps.accounts.authentication import ApiKeyAuthentication" in content


async def test_create_viewset_dotted_authentication_missing_class_fails(project):
    result = await agents.create_viewset(
        "blog", "Post",
        authentication="apps.blog.authentication.DoesNotExist",
        project_id=project,
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_authentication"


async def test_update_viewset_multiple_permissions_and_authentication(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_viewset("blog", "Post", project_id=project)
    result = await agents.update_viewset(
        "blog", "Post",
        permission=["IsAuthenticated", "IsAdminUser"],
        authentication=["JWTAuthentication"],
        project_id=project,
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert (
        "permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]"
        in content
    )
    assert "authentication_classes = [authentication.JWTAuthentication]" in content
    assert "from zeeb_api import authentication" in content
    compile(content, str(path), "exec")

    # A second call replaces (not duplicates) the attributes.
    result = await agents.update_viewset(
        "blog", "Post",
        authentication=["JWTStatelessAuthentication"],
        project_id=project,
    )
    assert result.success, result.message
    content = path.read_text()
    assert content.count("authentication_classes") == 1
    assert (
        "authentication_classes = [authentication.JWTStatelessAuthentication]"
        in content
    )


async def test_add_viewset_action_permission_list(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    await agents.create_serializer("blog", "Post", project_id=project)
    await agents.create_viewset("blog", "Post", project_id=project)
    await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_id=project
    )
    result = await agents.add_viewset_action(
        "blog", "Post", "publish",
        methods=["post"],
        permission=["IsAdminUser", "IsPostOwner"],
        project_id=project,
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "views.py"
    content = path.read_text()
    assert "permission_classes=[permissions.IsAdminUser, IsPostOwner]" in content
    assert "from apps.blog.permissions import IsPostOwner" in content
    compile(content, str(path), "exec")


async def test_generate_crud_forwards_permission_list_and_authentication(project):
    result = await agents.generate_crud(
        "blog", "Post", POST_FIELDS,
        permission=["IsAuthenticated", "IsAdminUser"],
        authentication=["JWTAuthentication"],
        project_id=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert (
        "permission_classes = [permissions.IsAuthenticated, permissions.IsAdminUser]"
        in content
    )
    assert "authentication_classes = [authentication.JWTAuthentication]" in content


# ---------------------------------------------------------------------------
# Seed script generation (typed placeholders — regression for f-string bug)
# ---------------------------------------------------------------------------


async def test_generate_seed_script_typed_placeholders(project):
    await agents.create_model(
        "blog", "Post",
        [
            {"name": "title", "type": "CharField", "max_length": 100},
            {"name": "views", "type": "IntegerField", "default": 0},
            {"name": "published", "type": "BooleanField", "default": False},
            {"name": "payload", "type": "JSONField", "null": True},
            {"name": "status", "type": "CharField", "max_length": 10,
             "choices": [["draft", "Draft"], ["pub", "Published"]]},
            {"name": "author", "type": "ForeignKey", "to": "User", "on_delete": "CASCADE"},
        ],
        project_id=project,
    )
    result = await agents.generate_seed_script("blog", project_id=project)
    assert result.success, result.message
    script = (project / result.data["path"]).read_text()
    assert "views=i," in script                   # int, not f'{i}'
    assert "published=bool(i % 2)," in script     # bool, not truthy string
    assert 'payload={"seed": i},' in script       # dict literal
    assert "status='draft'," in script            # first choice value
    assert "author=None,  # TODO" in script       # non-null FK flagged
    assert "f'" not in script.replace('f"', "")   # no repr-wrapped f-strings
    compile(script, "seed.py", "exec")


async def test_generate_seed_script_unknown_model_suggests(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    result = await agents.generate_seed_script("blog", models=["Pots"], project_id=project)
    assert not result.success
    assert result.data["error_code"] == "model_not_found"
    assert "Post" in result.data["suggestions"]


# ---------------------------------------------------------------------------
# JSON schema — choices/enum, positive ints, parser regression
# ---------------------------------------------------------------------------


async def test_get_model_json_schema_choices_and_options(project):
    await agents.create_model(
        "blog", "Post",
        [
            {"name": "status", "type": "CharField", "max_length": 10,
             "choices": [["draft", "Draft"], ["pub", "Published"]],
             "default": "draft", "help_text": "Publication status"},
            {"name": "rank", "type": "PositiveIntegerField", "default": 1},
        ],
        project_id=project,
    )
    result = await agents.get_model_json_schema("blog", "Post", project_id=project)
    assert result.success, result.message
    props = result.data["schema"]["properties"]
    assert props["status"]["enum"] == ["draft", "pub"]
    assert props["status"]["default"] == "draft"
    assert props["status"]["description"] == "Publication status"
    assert props["status"]["maxLength"] == 10   # parser survives nested brackets
    assert props["rank"]["minimum"] == 0

    missing = await agents.get_model_json_schema("blog", "Pots", project_id=project)
    assert not missing.success
    assert "Post" in missing.data["suggestions"]


async def test_get_model_json_schema_foreignkey_documents_id_convention(project):
    await agents.create_model(
        "blog", "Author", [{"name": "name", "type": "CharField", "max_length": 50}],
        project_id=project,
    )
    await agents.create_model(
        "blog", "Post",
        [
            {"name": "title", "type": "CharField", "max_length": 50},
            {"name": "author", "type": "ForeignKey", "to": "Author", "on_delete": "CASCADE"},
        ],
        project_id=project,
    )
    result = await agents.get_model_json_schema("blog", "Post", project_id=project)
    assert result.success, result.message
    desc = result.data["schema"]["properties"]["author"]["description"]
    # The write key must be spelled out; the static "FK id" left clients guessing.
    assert "author_id" in desc


def test_json_schema_map_covers_all_field_types():
    from zeeb_agents._utils.field_types import known_field_types
    from zeeb_agents.schema import _FIELD_TYPE_MAP
    missing = set(known_field_types()) - set(_FIELD_TYPE_MAP)
    assert not missing, f"schema._FIELD_TYPE_MAP missing entries for: {sorted(missing)}"


# ---------------------------------------------------------------------------
# New scaffolding: auth, oauth, user model, filterset, throttling, versioning
# ---------------------------------------------------------------------------


async def test_setup_auth_wires_router_idempotent(project):
    result = await agents.setup_auth(access_token_minutes=30, project_id=project)
    assert result.success, result.message
    assert result.data["wired"] is True
    urls = (project / "demo" / "urls.py").read_text()
    assert "create_auth_router" in urls
    settings = (project / "demo" / "settings.py").read_text()
    assert "JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 30" in settings
    # The auth middleware that populates request.state.user for ViewSets must
    # be installed, else protected ViewSets 403 with a valid Bearer token.
    assert "zeeb_api.middleware.JWTAuthMiddleware" in settings
    compile(settings, "settings.py", "exec")

    again = await agents.setup_auth(project_id=project)
    assert again.success
    assert again.data["already_wired"] is True
    # The include is not duplicated on re-run
    assert (
        (project / "demo" / "urls.py").read_text().count("router.include(create_auth_router(")
        == 1
    )
    # Nor is the middleware entry duplicated on re-run.
    assert (
        (project / "demo" / "settings.py")
        .read_text()
        .count('"zeeb_api.middleware.JWTAuthMiddleware"')
        == 1
    )


async def test_setup_auth_installs_missing_middleware(project):
    """A project scaffolded before the middleware fix self-heals on re-run."""
    settings_path = project / "demo" / "settings.py"
    # Simulate a legacy project: strip JWTAuthMiddleware out of MIDDLEWARE.
    settings_path.write_text(
        settings_path.read_text().replace(
            '    "zeeb_api.middleware.JWTAuthMiddleware",\n', ""
        )
    )
    assert "zeeb_api.middleware.JWTAuthMiddleware" not in settings_path.read_text()

    result = await agents.setup_auth(project_id=project)
    assert result.success, result.message
    assert "MIDDLEWARE" in result.data["settings_updated"]
    settings = settings_path.read_text()
    assert '"zeeb_api.middleware.JWTAuthMiddleware"' in settings
    compile(settings, "settings.py", "exec")


def test_ensure_middleware_adds_and_is_idempotent(tmp_path: Path):
    from zeeb_agents._utils.code_gen import ensure_middleware

    settings = tmp_path / "settings.py"
    settings.write_text(
        'MIDDLEWARE = [\n    "zeeb_api.middleware.CORSMiddleware",\n]\n'
    )
    dotted = "zeeb_api.middleware.JWTAuthMiddleware"

    assert ensure_middleware(settings, dotted) is True
    body = settings.read_text()
    assert body.count(f'"{dotted}"') == 1
    assert '"zeeb_api.middleware.CORSMiddleware"' in body  # existing entry kept
    compile(body, "settings.py", "exec")

    # Second call is a no-op.
    assert ensure_middleware(settings, dotted) is False
    assert settings.read_text().count(f'"{dotted}"') == 1


def test_ensure_middleware_ignores_commented_and_creates_list(tmp_path: Path):
    from zeeb_agents._utils.code_gen import ensure_middleware

    dotted = "zeeb_api.middleware.JWTAuthMiddleware"

    # A commented-out entry does not count as present.
    commented = tmp_path / "commented.py"
    commented.write_text(f'MIDDLEWARE = [\n    # "{dotted}",\n]\n')
    assert ensure_middleware(commented, dotted) is True
    body = commented.read_text()
    assert f'    # "{dotted}",' in body  # comment preserved
    assert body.count(f'    "{dotted}",') == 1  # active entry added
    compile(body, "settings.py", "exec")

    # No MIDDLEWARE assignment at all -> one is appended.
    empty = tmp_path / "empty.py"
    empty.write_text("DEBUG = True\n")
    assert ensure_middleware(empty, dotted) is True
    body = empty.read_text()
    assert f'"{dotted}"' in body
    compile(body, "settings.py", "exec")

    # Single-line, non-empty list.
    inline = tmp_path / "inline.py"
    inline.write_text('MIDDLEWARE = ["a.B"]\n')
    assert ensure_middleware(inline, dotted) is True
    body = inline.read_text()
    assert '"a.B"' in body and f'"{dotted}"' in body
    compile(body, "settings.py", "exec")


async def test_setup_oauth_configures_provider(project):
    result = await agents.setup_oauth("google", project_id=project)
    assert result.success, result.message
    settings = (project / "demo" / "settings.py").read_text()
    assert '"google"' in settings
    assert 'os.getenv("GOOGLE_CLIENT_ID")' in settings
    urls = (project / "demo" / "urls.py").read_text()
    assert "create_oauth_router" in urls
    compile(settings, "settings.py", "exec")

    unknown = await agents.setup_oauth("gogle", project_id=project)
    assert not unknown.success
    assert "google" in unknown.data["suggestions"]

    dup = await agents.setup_oauth("google", project_id=project)
    assert not dup.success
    assert dup.data["error_code"] == "already_exists"


async def test_create_user_model_sets_auth_user_model(project):
    result = await agents.create_user_model(
        "blog", "Member",
        extra_fields=[{"name": "phone", "type": "CharField", "max_length": 20, "null": True}],
        project_id=project,
    )
    assert result.success, result.message
    assert result.data["auth_user_model"] == "blog.Member"
    models = (project / "apps" / "blog" / "models.py").read_text()
    assert "class Member(AbstractUser):" in models
    assert "from zeeb_api.auth.models import AbstractUser" in models
    settings = (project / "demo" / "settings.py").read_text()
    assert 'AUTH_USER_MODEL = "blog.Member"' in settings
    compile(models, "models.py", "exec")


async def test_create_filterset(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_id=project)
    result = await agents.create_filterset(
        "blog", "Post", {"title": ["exact", "icontains"]}, project_id=project
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "filters.py"
    content = path.read_text()
    assert "class PostFilter(FilterSet):" in content
    assert '"title": ["exact", "icontains"],' in content
    compile(content, str(path), "exec")

    bad = await agents.create_filterset(
        "blog", "Post", {"title": ["icontainz"]}, project_id=project
    )
    assert not bad.success
    assert "icontains" in bad.data["suggestions"]


async def test_configure_throttling_round_trip(project):
    result = await agents.configure_throttling(
        default_classes=["AnonRateThrottle"],
        rates={"anon": "100/hour", "user": "1000/day"},
        project_id=project,
    )
    assert result.success, result.message
    read = await agents.manage_settings("DEFAULT_THROTTLE_RATES", project_id=project)
    assert read.success
    assert read.data["value"] == {"anon": "100/hour", "user": "1000/day"}

    bad = await agents.configure_throttling(rates={"anon": "fast"}, project_id=project)
    assert not bad.success
    assert bad.data["error_code"] == "invalid_input"


async def test_configure_versioning(project):
    result = await agents.configure_versioning(
        scheme="header", default_version="1.0", allowed_versions=["1.0", "2.0"],
        project_id=project,
    )
    assert result.success, result.message
    settings = (project / "demo" / "settings.py").read_text()
    assert 'DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.HeaderVersioning"' in settings
    assert 'ALLOWED_VERSIONS = ["1.0", "2.0"]' in settings

    bad = await agents.configure_versioning(scheme="path", project_id=project)
    assert not bad.success


# ---------------------------------------------------------------------------
# Error-code coverage across swept modules
# ---------------------------------------------------------------------------


async def test_describe_table_suggests_close_table(db_project):
    result = await agents.describe_table("post", project_id=db_project)
    assert not result.success
    assert result.data["error_code"] == "table_not_found"
    assert "posts" in result.data["suggestions"]


async def test_manage_settings_missing_key_suggests(project):
    result = await agents.manage_settings("DEBGU", project_id=project)
    assert not result.success
    assert result.data["error_code"] == "setting_not_found"
    assert "DEBUG" in result.data["suggestions"]


async def test_set_env_rejects_invalid_key(project):
    result = await agents.set_env("FOO BAR", "1", project_id=project)
    assert not result.success
    assert result.data["error_code"] == "invalid_input"
    # .env not corrupted
    env = await agents.get_env(project_id=project)
    assert "FOO BAR" not in env.data["env"]


async def test_read_logs_explicit_missing_file(project):
    result = await agents.read_logs(log_file="nope.log", project_id=project)
    assert not result.success
    assert result.data["error_code"] == "log_file_not_found"


async def test_run_query_invalid_sql_has_code(db_project):
    result = await agents.run_query("DELETE FROM posts", project_id=db_project)
    assert not result.success
    assert result.data["error_code"] == "invalid_sql"


async def test_no_project_id_error_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await agents.list_models(project_id=None)
    assert not result.success
    assert result.data["error_code"] == "no_project_id"


def test_fail_derives_recoverable_from_code():
    from zeeb_agents._utils.errors import AgentError, fail

    assert fail("bad type", code="invalid_field_type").data["recoverable"] is True
    assert fail("no app", code="app_not_found").data["recoverable"] is True
    assert fail("exists", code="already_exists").data["recoverable"] is True
    assert fail("no id", code="no_project_id").data["recoverable"] is False
    assert fail("gone", code="project_not_found").data["recoverable"] is False
    assert fail("no urls", code="runtime_not_configured").data["recoverable"] is False
    assert fail("denied", code="permission_denied").data["recoverable"] is False
    # Explicit override wins over the code-derived default.
    assert fail("terminal", code="invalid_input", recoverable=False).data["recoverable"] is False
    assert AgentError("x", code="no_project_id", recoverable=True).result.data["recoverable"] is True
