"""Characterization tests for the ``zeeb_agents`` package.

All public agent functions return :class:`zeeb_agents.AgentResult` and must
never raise — failures are reported as ``AgentResult(success=False, ...)``.

Intentionally untested modules (runtime / subprocess / network paths):

- ``testing.py`` (``run_tests``) — spawns a nested pytest subprocess; running
  pytest-inside-pytest is slow and recursive.
- ``server.py`` (``start_server`` / ``stop_server`` / ``get_server_status``) —
  spawns and signals long-lived OS processes (dev server) with sleeps.
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
    res = await agents.create_app("blog", project_root=root)
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


async def test_no_project_root_fails_gracefully(tmp_path, monkeypatch):
    """Without manage.py anywhere up the tree, functions fail, never raise."""
    monkeypatch.chdir(tmp_path)
    for fn in (
        agents.list_apps,
        agents.get_project_info,
        agents.get_settings,
        agents.list_tables,
    ):
        result = await fn(project_root=None)
        assert isinstance(result, AgentResult)
        assert result.success is False
        assert "project root" in result.message.lower()


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
    result = await agents.list_apps(project_root=project)
    assert result.success
    assert result.data["apps"] == ["blog"]

    info = await agents.get_project_info(project_root=project)
    assert info.success
    assert info.data["project_package"] == "demo"
    assert info.data["apps"] == ["blog"]
    assert "sqlite" in info.data["database_url"]


async def test_get_project_structure(project):
    result = await agents.get_project_structure(project_root=project, max_depth=3)
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
    result = await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    assert result.success, result.message
    content = (project / "apps" / "blog" / "models.py").read_text()
    assert "class Post(Model):" in content
    assert "title = fields.CharField(max_length=200)" in content
    assert "body = fields.TextField()" in content
    assert 'table_name = "blog_post"' in content
    assert result.data["fields"] == ["title", "body", "published"]


async def test_create_model_duplicate_fails(project):
    assert (await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)).success
    result = await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    assert not result.success
    assert "already exists" in result.message


async def test_add_field(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    result = await agents.add_field(
        "blog", "Post", {"name": "views", "type": "IntegerField", "default": 0},
        project_root=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "models.py").read_text()
    assert "views = fields.IntegerField(default=0)" in content


async def test_add_field_missing_model_fails(project):
    result = await agents.add_field(
        "blog", "Nope", {"name": "x", "type": "IntegerField"}, project_root=project
    )
    assert not result.success
    assert "not found" in result.message


async def test_create_serializer(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    result = await agents.create_serializer(
        "blog", "Post",
        fields=["id", "title", "body"],
        read_only_fields=["id"],
        project_root=project,
    )
    assert result.success, result.message
    content = (project / "apps" / "blog" / "serializers.py").read_text()
    assert "class PostSerializer(ModelSerializer):" in content
    assert "from .models import Post" in content
    assert '"title"' in content


async def test_create_viewset(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    await agents.create_serializer("blog", "Post", project_root=project)
    result = await agents.create_viewset("blog", "Post", project_root=project)
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "class PostViewSet(ModelViewSet):" in content
    assert "serializer_class = PostSerializer" in content
    assert "permissions.IsAuthenticatedOrReadOnly" in content


async def test_generate_crud(project):
    result = await agents.generate_crud("blog", "Article", POST_FIELDS, project_root=project)
    assert result.success, result.message
    assert len(result.data["steps"]) == 4
    app_dir = project / "apps" / "blog"
    assert "class Article(Model):" in (app_dir / "models.py").read_text()
    assert "class ArticleSerializer(ModelSerializer):" in (app_dir / "serializers.py").read_text()
    assert "class ArticleViewSet(ModelViewSet):" in (app_dir / "views.py").read_text()
    assert 'router.register("blog", ArticleViewSet)' in (app_dir / "urls.py").read_text()


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
        project_root=project,
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
    r1 = await agents.create_route("blog", "/ping", "get", "ping", project_root=project)
    assert r1.success, r1.message
    r2 = await agents.create_route(
        "blog", "/items/{item_id}", "get", "get_item", project_root=project
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
        "blog", "/x", "options", "do_x", project_root=project
    )
    assert not result.success
    assert "Invalid method" in result.message


async def test_add_viewset_action_with_body(project):
    import ast

    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    await agents.create_serializer("blog", "Post", project_root=project)
    await agents.create_viewset("blog", "Post", project_root=project)
    result = await agents.add_viewset_action(
        "blog", "Post", "publish", detail=True, methods=["post"],
        body="""
            post = await self.get_object()
            post.published = True
            await post.save()
            return {"status": "published"}
        """,
        project_root=project,
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


# ---------------------------------------------------------------------------
# Config: .env + settings
# ---------------------------------------------------------------------------


async def test_env_round_trip(project):
    # No .env yet
    result = await agents.get_env(project_root=project)
    assert not result.success

    result = await agents.set_env("API_KEY", "abc123", project_root=project)
    assert result.success
    assert result.data["action"] == "added"
    assert "API_KEY=abc123" in (project / ".env").read_text()

    result = await agents.set_env("API_KEY", "xyz", project_root=project)
    assert result.success
    assert result.data["action"] == "updated"

    result = await agents.get_env(project_root=project)
    assert result.success
    assert result.data["env"] == {"API_KEY": "xyz"}

    result = await agents.delete_env("API_KEY", project_root=project)
    assert result.success
    result = await agents.get_env(project_root=project)
    assert result.success is True or result.data["env"] == {}

    result = await agents.delete_env("MISSING", project_root=project)
    assert not result.success
    assert "not found" in result.message


async def test_get_settings(project):
    result = await agents.get_settings(project_root=project)
    assert result.success
    settings = result.data["settings"]
    assert "sqlite" in settings["DATABASE"]["url"]
    assert isinstance(settings["INSTALLED_APPS"], list)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


async def test_write_and_read_file(project):
    result = await agents.write_file("notes/hello.txt", "hi there", project_root=project)
    assert result.success
    assert result.data["action"] == "created"

    result = await agents.read_file("notes/hello.txt", project_root=project)
    assert result.success
    assert result.data["content"] == "hi there"

    result = await agents.write_file("notes/hello.txt", "again", project_root=project)
    assert result.success
    assert result.data["action"] == "updated"


async def test_read_file_missing(project):
    result = await agents.read_file("nope/missing.txt", project_root=project)
    assert not result.success
    assert "not found" in result.message.lower()


async def test_list_files(project):
    result = await agents.list_files(".", pattern="*.py", project_root=project)
    assert result.success
    names = {e["name"] for e in result.data["entries"]}
    assert "manage.py" in names


async def test_search_code(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    result = await agents.search_code("class Post", glob="apps/**/*.py", project_root=project)
    assert result.success
    assert result.data["total_matches"] >= 1
    files = {f["file"] for f in result.data["files"]}
    assert "apps/blog/models.py" in files


async def test_search_code_bad_regex(project):
    result = await agents.search_code("(*invalid", project_root=project)
    assert not result.success


async def test_read_file_outside_project_root_fails(tmp_path):
    """Security: paths escaping the project root are rejected, never raised."""
    root = tmp_path / "proj"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")

    result = await agents.read_file(str(secret), project_root=root)
    assert isinstance(result, AgentResult)
    assert not result.success
    assert "outside the project root" in result.message

    result = await agents.read_file("../secret.txt", project_root=root)
    assert not result.success
    assert "outside the project root" in result.message


async def test_write_file_outside_project_root_fails(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    result = await agents.write_file("../evil.txt", "x", project_root=root)
    assert not result.success
    assert "outside the project root" in result.message
    assert not (tmp_path / "evil.txt").exists()


# ---------------------------------------------------------------------------
# Permission scaffolding
# ---------------------------------------------------------------------------


async def test_create_and_list_permission_classes(project):
    result = await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_root=project
    )
    assert result.success, result.message
    perms = project / "apps" / "blog" / "permissions.py"
    content = perms.read_text()
    assert "class IsPostOwner(BasePermission):" in content
    assert "has_object_permission" in content

    # Duplicate rejected
    result = await agents.create_permission_class(
        "blog", "IsPostOwner", logic="owner_only", project_root=project
    )
    assert not result.success
    assert "already exists" in result.message

    # Unknown preset rejected
    result = await agents.create_permission_class(
        "blog", "Whatever", logic="not_a_preset", project_root=project
    )
    assert not result.success

    result = await agents.list_permission_classes("blog", project_root=project)
    assert result.success
    assert result.data["permissions"] == ["IsPostOwner"]


async def test_list_permission_classes_no_file(project):
    result = await agents.list_permission_classes("blog", project_root=project)
    assert result.success
    assert result.data["permissions"] == []


# ---------------------------------------------------------------------------
# Database introspection (functions build their own sync engine from settings)
# ---------------------------------------------------------------------------


async def test_list_tables(db_project):
    result = await agents.list_tables(project_root=db_project)
    assert result.success
    assert "posts" in result.data["tables"]


async def test_describe_table(db_project):
    result = await agents.describe_table("posts", project_root=db_project)
    assert result.success
    cols = {c["name"] for c in result.data["columns"]}
    assert {"id", "title", "created_at", "updated_at"} <= cols


async def test_run_query_select(db_project):
    result = await agents.run_query(
        "SELECT id, title FROM posts ORDER BY id", project_root=db_project
    )
    assert result.success, result.message
    assert result.data["count"] == 3
    assert result.data["rows"][0]["title"] == "first"


async def test_run_query_created_at_column_is_not_false_positive(db_project):
    """Word-boundary check: created_at/updated_at must not trip 'create'/'update'."""
    result = await agents.run_query(
        "SELECT created_at, updated_at FROM posts WHERE created_at IS NOT NULL",
        project_root=db_project,
    )
    assert result.success, result.message
    assert result.data["count"] == 3


async def test_run_query_with_cte(db_project):
    result = await agents.run_query(
        "WITH t AS (SELECT title FROM posts) SELECT * FROM t ORDER BY title",
        project_root=db_project,
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
    result = await agents.run_query(sql, project_root=db_project)
    assert isinstance(result, AgentResult)
    assert not result.success, f"should have rejected: {sql!r}"
    # Table is untouched
    assert _post_count(db_project) == 3


async def test_run_query_rolls_back(db_project):
    """Even an allowed query runs in a transaction that is always rolled back."""
    result = await agents.run_query("SELECT COUNT(*) AS n FROM posts", project_root=db_project)
    assert result.success
    assert result.data["rows"][0]["n"] == 3
    assert _post_count(db_project) == 3


# ---------------------------------------------------------------------------
# Signal scaffolding (filesystem only)
# ---------------------------------------------------------------------------


async def test_signal_receiver_round_trip(project):
    result = await agents.create_signal_receiver(
        "blog", "post_save", "Post", "notify_on_save", project_root=project
    )
    assert result.success, result.message
    signals_py = project / "apps" / "blog" / "signals.py"
    content = signals_py.read_text()
    assert "@receiver(post_save, sender=Post)" in content
    assert "async def notify_on_save" in content

    result = await agents.list_signal_receivers("blog", project_root=project)
    assert result.success
    assert result.data["count"] == 1
    assert result.data["receivers"][0]["func_name"] == "notify_on_save"

    result = await agents.create_signal_receiver(
        "blog", "bogus_signal", "Post", "x", project_root=project
    )
    assert not result.success


# ---------------------------------------------------------------------------
# Tool discovery — list_capabilities
# ---------------------------------------------------------------------------


async def test_list_capabilities_covers_all_exports():
    result = await agents.list_capabilities()
    assert result.success, result.message
    names = {t["name"] for t in result.data["tools"]}
    # Every callable export (everything in __all__ except the non-functions)
    expected = {
        n
        for n in agents.__all__
        if n not in {"AgentResult", "RESOURCE_URIS"} and callable(getattr(agents, n))
    }
    assert expected <= names
    assert result.data["count"] == len(result.data["tools"])
    # Each entry has the documented shape and hides project_root from signatures.
    entry = next(t for t in result.data["tools"] if t["name"] == "create_model")
    assert entry["module"] == "models"
    assert "project_root" not in entry["signature"]
    assert entry["summary"]
    assert "doc" not in entry  # omitted unless include_docstrings=True


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
    """Every callable public tool keyed by name (non-functions excluded)."""
    return {
        n: getattr(agents, n)
        for n in agents.__all__
        if n not in {"AgentResult", "RESOURCE_URIS"} and callable(getattr(agents, n))
    }


def _strip_strings(text: str) -> str:
    """Blank out quoted string contents so ``=``/``,`` inside them don't parse."""
    return re.sub(r'"[^"]*"|\'[^\']*\'', '', text)


def _real_params(func) -> list[str]:
    """Real parameter names, minus the auto-resolved ``project_root``."""
    return [p for p in inspect.signature(func).parameters if p != "project_root"]


def _doc_sig_params(sig_str: str) -> list[str]:
    """Parse the parameter names out of a documented ``NAME(sig)`` string."""
    parts = [p.strip() for p in _strip_strings(sig_str).split(",") if p.strip()]
    names = [p.split("=", 1)[0].strip() for p in parts]
    return [n for n in names if n and n != "project_root"]


# Matches a ``{prefix}name(...)`` call whose parentheses close on one line.
_CALL_RE = re.compile(r"\{prefix\}(\w+)\(([^()\n]*)\)")
# Matches a capabilities.md table cell like `` `{prefix}name(sig)` ``.
_TABLE_RE = re.compile(r"`\{prefix\}(\w+)\(([^`]*)\)`")


def test_capabilities_md_signatures_match_code():
    """Each signature in capabilities.md's tables must match the real function."""
    funcs = _tool_funcs()
    text = (_DOCS_DIR / "capabilities.md").read_text(encoding="utf-8")
    checked = 0
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue  # only parse table rows, not prose/blockquote examples
        m = _TABLE_RE.search(line)
        if not m:
            continue
        name, sig = m.group(1), m.group(2)
        assert name in funcs, f"capabilities.md references unknown tool '{name}'"
        assert _doc_sig_params(sig) == _real_params(funcs[name]), (
            f"capabilities.md signature for '{name}' is stale:\n"
            f"  documented: {_doc_sig_params(sig)}\n"
            f"  actual:     {_real_params(funcs[name])}"
        )
        checked += 1
    assert checked > 40, f"expected to check most tools, only saw {checked}"


def test_agent_docs_reference_real_tools():
    """Every ``{prefix}name(`` call across the docs names a real tool."""
    funcs = _tool_funcs()
    for md in sorted(_DOCS_DIR.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        for name in {m.group(1) for m in _CALL_RE.finditer(text)}:
            assert name in funcs, f"{md.name} references unknown tool '{name}(...)'"


def test_agent_docs_example_kwargs_are_valid():
    """Keyword args in single-line ``{prefix}name(...)`` examples must be real."""
    funcs = _tool_funcs()
    for md in sorted(_DOCS_DIR.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        for name, raw_args in _CALL_RE.findall(text):
            if name not in funcs:
                continue  # covered by test_agent_docs_reference_real_tools
            valid = set(inspect.signature(funcs[name]).parameters)
            for kw in re.findall(r"(\w+)\s*=", _strip_strings(raw_args)):
                assert kw in valid, (
                    f"{md.name}: {name}(...) uses unknown keyword '{kw}='; "
                    f"valid params: {sorted(valid)}"
                )


# ---------------------------------------------------------------------------
# CORS round-trip
# ---------------------------------------------------------------------------


async def test_cors_round_trip(project):
    result = await agents.get_cors_config(project_root=project)
    assert result.success
    assert isinstance(result.data["cors"], dict)

    result = await agents.configure_cors(
        ["https://app.example.com", "http://localhost:3000"],
        methods=["GET", "POST"],
        project_root=project,
    )
    assert result.success, result.message
    assert "CORS_ALLOW_ORIGINS" in result.data["keys_written"]

    result = await agents.get_cors_config(project_root=project)
    assert result.success
    cors = result.data["cors"]
    assert cors["CORS_ALLOW_ORIGINS"] == ["https://app.example.com", "http://localhost:3000"]
    assert cors["CORS_ALLOW_METHODS"] == ["GET", "POST"]


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
    result = await agents.read_logs(level="ERROR", project_root=project)
    assert result.success, result.message
    lines = result.data["lines"]
    # Exactly the one real ERROR line — the NOTANERROR INFO line must not match.
    assert len(lines) == 1
    assert "boom happened" in lines[0]


async def test_read_logs_no_file_fails(project):
    result = await agents.read_logs(project_root=project)
    assert not result.success
    assert result.data["searched_in"]


async def test_search_logs(project):
    _write_log(project)
    result = await agents.search_logs(r"boom", project_root=project)
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
        project_root=project,
    )
    assert res.success, res.message

    result = await agents.get_model_json_schema("blog", "Post", project_root=project)
    assert result.success, result.message
    schema = result.data["schema"]
    assert schema["title"] == "Post"
    assert schema["type"] == "object"
    assert "title" in schema["properties"]

    missing = await agents.get_model_json_schema("blog", "Nope", project_root=project)
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
        "blgo", "Post", [{"name": "x", "type": "IntegerField"}], project_root=project
    )
    assert not result.success
    assert result.data["error_code"] == "app_not_found"
    assert "blog" in result.data["suggestions"]


def test_error_codes_documented_in_principles():
    """Drift guard: principles.md lists exactly the ERROR_CODES vocabulary."""
    text = (_DOCS_DIR / "principles.md").read_text(encoding="utf-8")
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
        "blog", "Post", [{"name": "title", "type": "CharFeild"}], project_root=project
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
        project_root=project,
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
        project_root=project,
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
        project_root=project,
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
        project_root=project,
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_meta"
    assert "ordering" in result.data["suggestions"]


async def test_update_model_missing_model_fails(project):
    """Regression: renaming a nonexistent model used to report success."""
    result = await agents.update_model(
        "blog", "Nope", rename_to="StillNope", project_root=project
    )
    assert not result.success
    assert result.data["error_code"] == "model_not_found"


# ---------------------------------------------------------------------------
# Serializer extra fields / viewset options
# ---------------------------------------------------------------------------


async def test_create_serializer_extra_fields_and_validate_stub(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    result = await agents.create_serializer(
        "blog", "Post",
        fields=["id", "title"],
        extra_fields=[
            {"name": "summary", "type": "SerializerMethodField"},
            {"name": "secret", "type": "CharField", "write_only": True, "max_length": 8},
        ],
        validate_fields=["title"],
        project_root=project,
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
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    result = await agents.create_serializer(
        "blog", "Post",
        extra_fields=[{"name": "x", "type": "MethodField"}],
        project_root=project,
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_field_type"


async def test_create_viewset_full_options(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    await agents.create_serializer("blog", "Post", project_root=project)
    result = await agents.create_viewset(
        "blog", "Post",
        pagination="page",
        throttles=["UserRateThrottle"],
        search_fields=["title"],
        ordering_fields=["title"],
        lookup_field="id",
        project_root=project,
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
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    await agents.create_serializer("blog", "Post", project_root=project)
    result = await agents.create_viewset("blog", "Post", read_only=True, project_root=project)
    assert result.success, result.message
    content = (project / "apps" / "blog" / "views.py").read_text()
    assert "class PostViewSet(ReadOnlyModelViewSet):" in content
    assert "from zeeb_api.viewsets import ReadOnlyModelViewSet" in content


async def test_create_viewset_invalid_permission_suggests(project):
    result = await agents.create_viewset(
        "blog", "Post", permission="IsAuthenticatd", project_root=project
    )
    assert not result.success
    assert result.data["error_code"] == "invalid_permission"
    assert "IsAuthenticated" in result.data["suggestions"]


async def test_update_viewset_sets_attrs(project):
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    await agents.create_serializer("blog", "Post", project_root=project)
    await agents.create_viewset("blog", "Post", project_root=project)
    result = await agents.update_viewset(
        "blog", "Post", permission="IsAdminUser", pagination="cursor",
        search_fields=["title"], project_root=project,
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
                                          project_root=project)
    assert not missing.success


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
        project_root=project,
    )
    result = await agents.generate_seed_script("blog", project_root=project)
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
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    result = await agents.generate_seed_script("blog", models=["Pots"], project_root=project)
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
        project_root=project,
    )
    result = await agents.get_model_json_schema("blog", "Post", project_root=project)
    assert result.success, result.message
    props = result.data["schema"]["properties"]
    assert props["status"]["enum"] == ["draft", "pub"]
    assert props["status"]["default"] == "draft"
    assert props["status"]["description"] == "Publication status"
    assert props["status"]["maxLength"] == 10   # parser survives nested brackets
    assert props["rank"]["minimum"] == 0

    missing = await agents.get_model_json_schema("blog", "Pots", project_root=project)
    assert not missing.success
    assert "Post" in missing.data["suggestions"]


def test_json_schema_map_covers_all_field_types():
    from zeeb_agents._utils.field_types import known_field_types
    from zeeb_agents.schema import _FIELD_TYPE_MAP
    missing = set(known_field_types()) - set(_FIELD_TYPE_MAP)
    assert not missing, f"schema._FIELD_TYPE_MAP missing entries for: {sorted(missing)}"


# ---------------------------------------------------------------------------
# New scaffolding: auth, oauth, user model, filterset, throttling, versioning
# ---------------------------------------------------------------------------


async def test_setup_auth_wires_router_idempotent(project):
    result = await agents.setup_auth(access_token_minutes=30, project_root=project)
    assert result.success, result.message
    assert result.data["wired"] is True
    urls = (project / "demo" / "urls.py").read_text()
    assert "create_auth_router" in urls
    settings = (project / "demo" / "settings.py").read_text()
    assert "JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 30" in settings

    again = await agents.setup_auth(project_root=project)
    assert again.success
    assert again.data["already_wired"] is True
    # The include is not duplicated on re-run
    assert (
        (project / "demo" / "urls.py").read_text().count("router.include(create_auth_router(")
        == 1
    )


async def test_setup_oauth_configures_provider(project):
    result = await agents.setup_oauth("google", project_root=project)
    assert result.success, result.message
    settings = (project / "demo" / "settings.py").read_text()
    assert '"google"' in settings
    assert 'os.getenv("GOOGLE_CLIENT_ID")' in settings
    urls = (project / "demo" / "urls.py").read_text()
    assert "create_oauth_router" in urls
    compile(settings, "settings.py", "exec")

    unknown = await agents.setup_oauth("gogle", project_root=project)
    assert not unknown.success
    assert "google" in unknown.data["suggestions"]

    dup = await agents.setup_oauth("google", project_root=project)
    assert not dup.success
    assert dup.data["error_code"] == "already_exists"


async def test_create_user_model_sets_auth_user_model(project):
    result = await agents.create_user_model(
        "blog", "Member",
        extra_fields=[{"name": "phone", "type": "CharField", "max_length": 20, "null": True}],
        project_root=project,
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
    await agents.create_model("blog", "Post", POST_FIELDS, project_root=project)
    result = await agents.create_filterset(
        "blog", "Post", {"title": ["exact", "icontains"]}, project_root=project
    )
    assert result.success, result.message
    path = project / "apps" / "blog" / "filters.py"
    content = path.read_text()
    assert "class PostFilter(FilterSet):" in content
    assert '"title": ["exact", "icontains"],' in content
    compile(content, str(path), "exec")

    bad = await agents.create_filterset(
        "blog", "Post", {"title": ["icontainz"]}, project_root=project
    )
    assert not bad.success
    assert "icontains" in bad.data["suggestions"]


async def test_configure_throttling_round_trip(project):
    result = await agents.configure_throttling(
        default_classes=["AnonRateThrottle"],
        rates={"anon": "100/hour", "user": "1000/day"},
        project_root=project,
    )
    assert result.success, result.message
    read = await agents.manage_settings("DEFAULT_THROTTLE_RATES", project_root=project)
    assert read.success
    assert read.data["value"] == {"anon": "100/hour", "user": "1000/day"}

    bad = await agents.configure_throttling(rates={"anon": "fast"}, project_root=project)
    assert not bad.success
    assert bad.data["error_code"] == "invalid_input"


async def test_configure_versioning(project):
    result = await agents.configure_versioning(
        scheme="header", default_version="1.0", allowed_versions=["1.0", "2.0"],
        project_root=project,
    )
    assert result.success, result.message
    settings = (project / "demo" / "settings.py").read_text()
    assert 'DEFAULT_VERSIONING_CLASS = "zeeb_api.versioning.HeaderVersioning"' in settings
    assert 'ALLOWED_VERSIONS = ["1.0", "2.0"]' in settings

    bad = await agents.configure_versioning(scheme="path", project_root=project)
    assert not bad.success


# ---------------------------------------------------------------------------
# Error-code coverage across swept modules
# ---------------------------------------------------------------------------


async def test_describe_table_suggests_close_table(db_project):
    result = await agents.describe_table("post", project_root=db_project)
    assert not result.success
    assert result.data["error_code"] == "table_not_found"
    assert "posts" in result.data["suggestions"]


async def test_manage_settings_missing_key_suggests(project):
    result = await agents.manage_settings("DEBGU", project_root=project)
    assert not result.success
    assert result.data["error_code"] == "setting_not_found"
    assert "DEBUG" in result.data["suggestions"]


async def test_set_env_rejects_invalid_key(project):
    result = await agents.set_env("FOO BAR", "1", project_root=project)
    assert not result.success
    assert result.data["error_code"] == "invalid_input"
    # .env not corrupted
    env = await agents.get_env(project_root=project)
    assert "FOO BAR" not in env.data["env"]


async def test_read_logs_explicit_missing_file(project):
    result = await agents.read_logs(log_file="nope.log", project_root=project)
    assert not result.success
    assert result.data["error_code"] == "log_file_not_found"


async def test_run_query_invalid_sql_has_code(db_project):
    result = await agents.run_query("DELETE FROM posts", project_root=db_project)
    assert not result.success
    assert result.data["error_code"] == "invalid_sql"


async def test_no_project_root_error_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await agents.list_models(project_root=None)
    assert not result.success
    assert result.data["error_code"] == "no_project_root"
