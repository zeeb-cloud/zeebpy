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
