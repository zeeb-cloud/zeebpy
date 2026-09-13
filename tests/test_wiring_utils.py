"""Hardening tests for the wiring/settings file editors (G7–G10).

These guard the string/AST editing seams that scaffolding relies on: the
``INSTALLED_APPS`` locator must survive brackets hiding in comments/strings,
``router.include`` emission must refuse a customized urls.py without a
``router`` symbol (instead of emitting a NameError), and multi-line setting
replacement must not miscount brackets inside string values.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from zeeb_agents._utils.code_gen import set_or_append_setting
from zeeb_agents._utils.errors import AgentError
from zeeb_agents._utils.wiring import (
    append_router_include,
    ensure_app_urls_included,
    ensure_installed_app,
)

STANDARD_URLS = '''"""demo URL configuration."""

from zeeb_api.routers import DefaultRouter

# Main router
router = DefaultRouter()


def get_routes():
    """Return all routes for the FastAPI app."""
    return router.routes
'''


def _project(tmp_path: Path, settings_text: str, urls_text: str | None = STANDARD_URLS) -> Path:
    pkg = tmp_path / "demo"
    pkg.mkdir()
    (pkg / "settings.py").write_text(settings_text)
    if urls_text is not None:
        (pkg / "urls.py").write_text(urls_text)
    return tmp_path


# ---------------------------------------------------------------------------
# G7 — ensure_installed_app must not truncate at a "]" in a comment/string
# ---------------------------------------------------------------------------


TRICKY_SETTINGS = '''"""settings with brackets in awkward places."""

# NOTE: this comment mentions a list literal ["x"] with a closing ] bracket
INSTALLED_APPS = [
    "apps.blog",  # legacy ] bracket in a trailing comment
    "apps.we]ird",
]

OTHER = "unrelated ] value"
'''


def test_ensure_installed_app_survives_brackets_in_comments_and_strings(tmp_path: Path):
    root = _project(tmp_path, TRICKY_SETTINGS)
    assert ensure_installed_app(root, "shop") is True
    text = (root / "demo" / "settings.py").read_text()
    ast.parse(text)  # file must still be valid Python
    apps = {
        node.targets[0].id: node.value
        for node in ast.parse(text).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    values = [c.value for c in apps["INSTALLED_APPS"].elts]
    assert values == ["apps.blog", "apps.we]ird", "apps.shop"]
    # Idempotent.
    assert ensure_installed_app(root, "shop") is False


def test_ensure_installed_app_rejects_non_list_assignment(tmp_path: Path):
    root = _project(tmp_path, "INSTALLED_APPS = get_apps()\n")
    with pytest.raises(AgentError) as exc:
        ensure_installed_app(root, "shop")
    assert (exc.value.result.data or {})["error_code"] == "setting_not_found"


def test_ensure_installed_app_creates_missing_assignment(tmp_path: Path):
    """Repair semantics: a settings.py without INSTALLED_APPS gains one."""
    root = _project(tmp_path, 'DATABASE = "sqlite:///db.sqlite3"\n')
    assert ensure_installed_app(root, "shop") is True
    text = (root / "demo" / "settings.py").read_text()
    ast.parse(text)
    assert 'INSTALLED_APPS = [\n    "apps.shop",\n]' in text
    assert ensure_installed_app(root, "shop") is False  # idempotent


def test_ensure_installed_app_adds_comma_to_inline_list(tmp_path: Path):
    """No implicit string concatenation when the last entry lacks a comma."""
    root = _project(tmp_path, 'INSTALLED_APPS = ["apps.blog"]\n')
    assert ensure_installed_app(root, "shop") is True
    text = (root / "demo" / "settings.py").read_text()
    apps_node = next(
        n.value
        for n in ast.parse(text).body
        if isinstance(n, ast.Assign) and n.targets[0].id == "INSTALLED_APPS"
    )
    assert [c.value for c in apps_node.elts] == ["apps.blog", "apps.shop"]


def test_ensure_installed_app_comma_repair_skips_inline_comment(tmp_path: Path):
    """Last entry without a trailing comma but with an inline comment: the
    repair comma must land after the value, not inside the comment (else the
    two entries implicitly concatenate and both are lost)."""
    root = _project(
        tmp_path,
        'INSTALLED_APPS = [\n    "apps.blog"  # keep blog\n]\n',
    )
    assert ensure_installed_app(root, "shop") is True
    text = (root / "demo" / "settings.py").read_text()
    apps_node = next(
        n.value
        for n in ast.parse(text).body
        if isinstance(n, ast.Assign) and n.targets[0].id == "INSTALLED_APPS"
    )
    assert [c.value for c in apps_node.elts] == ["apps.blog", "apps.shop"]
    assert "# keep blog" in text  # comment preserved


def test_ensure_app_urls_included_recreates_missing_project_urls(tmp_path: Path):
    """Repair semantics: a missing project urls.py is rebuilt from the template."""
    root = _project(tmp_path, "INSTALLED_APPS = []\n", urls_text=None)
    assert ensure_app_urls_included(root, "blog") is True
    text = (root / "demo" / "urls.py").read_text()
    assert "router = DefaultRouter()" in text
    assert "router.include(blog_router)" in text
    assert "def get_routes" in text


def test_ensure_installed_app_rejects_unparseable_settings(tmp_path: Path):
    root = _project(tmp_path, "def broken(:\nINSTALLED_APPS = []\n")
    with pytest.raises(AgentError) as exc:
        ensure_installed_app(root, "shop")
    assert (exc.value.result.data or {})["error_code"] == "invalid_input"


# ---------------------------------------------------------------------------
# G8/G9 — router.include emission requires a `router` symbol
# ---------------------------------------------------------------------------


def test_ensure_app_urls_included_standard_layout(tmp_path: Path):
    root = _project(tmp_path, "INSTALLED_APPS = []\n")
    assert ensure_app_urls_included(root, "blog") is True
    text = (root / "demo" / "urls.py").read_text()
    include_at = text.index("router.include(blog_router)")
    assert text.index("from apps.blog.urls import router as blog_router") < include_at
    assert include_at < text.index("def get_routes")
    # Idempotent.
    assert ensure_app_urls_included(root, "blog") is False


def test_ensure_app_urls_included_refuses_renamed_router(tmp_path: Path):
    renamed = STANDARD_URLS.replace("router = DefaultRouter()", "api = DefaultRouter()").replace(
        "return router.routes", "return api.routes"
    )
    root = _project(tmp_path, "INSTALLED_APPS = []\n", urls_text=renamed)
    before = (root / "demo" / "urls.py").read_text()
    with pytest.raises(AgentError) as exc:
        ensure_app_urls_included(root, "blog")
    assert (exc.value.result.data or {})["error_code"] == "invalid_input"
    assert (root / "demo" / "urls.py").read_text() == before  # byte-identical


def test_append_router_include_dedupe_pattern(tmp_path: Path):
    root = _project(tmp_path, "INSTALLED_APPS = []\n")
    urls_path = root / "demo" / "urls.py"
    assert append_router_include(
        urls_path,
        'router.include(create_auth_router(prefix="/auth"))',
        import_stmt="from zeeb_api.auth import create_auth_router",
        dedupe_pattern=r"create_auth_router",
    ) is True
    # A second call with different arguments still dedupes on the pattern.
    assert append_router_include(
        urls_path,
        'router.include(create_auth_router(prefix="/other"))',
        dedupe_pattern=r"create_auth_router",
    ) is False
    text = urls_path.read_text()
    assert text.count("router.include(create_auth_router(") == 1
    assert text.count("from zeeb_api.auth import create_auth_router") == 1


def test_append_router_include_missing_urls(tmp_path: Path):
    root = _project(tmp_path, "INSTALLED_APPS = []\n", urls_text=None)
    with pytest.raises(AgentError) as exc:
        append_router_include(root / "demo" / "urls.py", "router.include(x)")
    assert (exc.value.result.data or {})["error_code"] == "file_not_found"


# ---------------------------------------------------------------------------
# G10 — set_or_append_setting with brackets inside string values
# ---------------------------------------------------------------------------


def test_set_or_append_setting_replaces_span_with_brackets_in_strings():
    content = (
        'CORS_ALLOW_ORIGINS = [\n'
        '    "https://example.com/[path]",\n'
        '    "weird]origin",\n'
        ']\n'
        'NEXT_SETTING = 1\n'
    )
    out = set_or_append_setting(content, "CORS_ALLOW_ORIGINS", '["*"]')
    ast.parse(out)
    assert 'CORS_ALLOW_ORIGINS = ["*"]' in out
    assert out.count("NEXT_SETTING = 1") == 1
    assert "weird]origin" not in out


def test_set_or_append_setting_appends_missing_key():
    out = set_or_append_setting("A = 1\n", "B", '"x"')
    ast.parse(out)
    assert out.endswith('B = "x"\n')


def test_set_or_append_setting_fallback_on_unparseable_file():
    # A syntax error above the key forces the bracket-scan fallback; the string
    # containing "]" must not truncate the replaced span.
    content = (
        "def broken(:\n"
        "CORS_ALLOW_ORIGINS = [\n"
        '    "ur]l",\n'
        "]\n"
        "AFTER = 2\n"
    )
    out = set_or_append_setting(content, "CORS_ALLOW_ORIGINS", '["*"]')
    assert 'CORS_ALLOW_ORIGINS = ["*"]' in out
    assert "AFTER = 2" in out
    assert "ur]l" not in out
