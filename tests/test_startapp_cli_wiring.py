"""`zeeb startapp` registers the app it creates, like the agent path does.

An app that is not in ``INSTALLED_APPS`` has no models in any migration, and an
app whose router is not included in the project ``urls.py`` 404s on every
endpoint it registers. Leaving both edits to the user was the single most
common way a scaffolded app silently did nothing.
"""

from pathlib import Path

import pytest

from zeeb_orm.cli.commands.startapp import run_startapp
from zeeb_orm.cli.commands.startproject import run_startproject


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    assert run_startproject("demo", str(tmp_path)) == 0
    root = tmp_path / "demo"
    monkeypatch.chdir(root)
    return root


def settings_text(root: Path) -> str:
    return (root / "demo" / "settings.py").read_text()


def urls_text(root: Path) -> str:
    return (root / "demo" / "urls.py").read_text()


def test_startapp_wires_by_default(project):
    assert run_startapp("shop") == 0

    assert '"apps.shop",' in settings_text(project)
    urls = urls_text(project)
    assert "from apps.shop.urls import router as shop_router" in urls
    assert "router.include(shop_router)" in urls
    compile(settings_text(project), "settings.py", "exec")
    compile(urls, "urls.py", "exec")


def test_startapp_no_wire_leaves_the_project_untouched(project):
    before_settings = settings_text(project)
    before_urls = urls_text(project)

    assert run_startapp("shop", wire=False) == 0

    assert (project / "apps" / "shop" / "models.py").is_file()
    assert settings_text(project) == before_settings
    assert urls_text(project) == before_urls


def test_startapp_is_idempotent_across_apps(project):
    assert run_startapp("shop") == 0
    assert run_startapp("blog") == 0

    settings = settings_text(project)
    assert settings.count('"apps.shop",') == 1
    assert settings.count('"apps.blog",') == 1
    urls = urls_text(project)
    assert urls.count("router.include(shop_router)") == 1
    assert urls.count("router.include(blog_router)") == 1
    compile(settings, "settings.py", "exec")
    compile(urls, "urls.py", "exec")


def test_startapp_refuses_an_existing_app(project):
    assert run_startapp("shop") == 0
    assert run_startapp("shop") == 1


def test_startapp_writes_only_modules_the_framework_reads(project):
    """No admin.py (there is no admin package) and no apps.py.

    An app-config class and ``default_app_config`` were Django cargo: nothing
    in zeeb_orm, zeeb_api or zeeb_agents ever read either, and INSTALLED_APPS
    is a plain list of dotted paths.
    """
    assert run_startapp("shop") == 0

    app_dir = project / "apps" / "shop"
    assert sorted(p.name for p in app_dir.iterdir()) == [
        "__init__.py",
        "models.py",
        "serializers.py",
        "urls.py",
        "views.py",
    ]


def test_startapp_puts_the_apps_test_where_the_fixtures_apply(project):
    """pytest.ini collects tests/ only, and a conftest there does not reach
    into apps/<name>/ — a test written next to the app would never run."""
    assert run_startapp("shop") == 0

    assert not (project / "apps" / "shop" / "tests.py").exists()
    test_file = project / "tests" / "test_shop.py"
    assert test_file.is_file()
    compile(test_file.read_text(), "test_shop.py", "exec")
    # The harness the test needs was retrofitted at the same time.
    assert (project / "tests" / "conftest.py").is_file()
    assert (project / "pytest.ini").is_file()


def test_startapp_with_a_model_produces_a_registered_resource(project):
    assert run_startapp("blog", model="Post") == 0

    app_dir = project / "apps" / "blog"
    assert "class Post(Model):" in (app_dir / "models.py").read_text()
    assert "class PostSerializer(serializers.ModelSerializer):" in (
        app_dir / "serializers.py"
    ).read_text()
    assert "class PostViewSet(viewsets.ModelViewSet):" in (app_dir / "views.py").read_text()
    # The route exists the moment migrate runs — no further edit needed.
    assert 'router.register("posts", PostViewSet)' in (app_dir / "urls.py").read_text()

    for name in ("models.py", "serializers.py", "views.py", "urls.py"):
        compile((app_dir / name).read_text(), name, "exec")
    compile((project / "tests" / "test_blog.py").read_text(), "test_blog.py", "exec")


def test_startapp_refuses_a_model_name_it_cannot_use(project):
    assert run_startapp("blog", model="post") == 1
    assert run_startapp("blog", model="Post Model") == 1
    assert not (project / "apps" / "blog").exists()


def test_startapp_reports_failure_without_deleting_the_app(project):
    """A wiring failure must leave the scaffolded files on disk to be repaired."""
    urls = project / "demo" / "urls.py"
    urls.write_text(urls.read_text().replace("router = DefaultRouter()", "api = DefaultRouter()"))

    assert run_startapp("shop") == 1
    assert (project / "apps" / "shop" / "models.py").is_file()
    # The INSTALLED_APPS half succeeded before the urls.py half failed.
    assert '"apps.shop",' in settings_text(project)


def test_cli_accepts_no_wire(project, monkeypatch):
    from zeeb_orm.cli.main import main

    monkeypatch.setattr("sys.argv", ["zeeb-manage", "startapp", "shop", "--no-wire"])
    assert main() == 0
    assert '"apps.shop",' not in settings_text(project)

    monkeypatch.setattr("sys.argv", ["zeeb-manage", "startapp", "blog"])
    assert main() == 0
    assert '"apps.blog",' in settings_text(project)
