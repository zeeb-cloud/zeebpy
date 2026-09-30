"""Settings discovery reads the configured module, and says so when it cannot.

``get_user_model()`` executed a ``settings.py`` it found by walking up from the
working directory — independently of the module ``create_app`` had loaded — and
swallowed any error on the way, falling back to the framework's ``User``: a
foreign key could bind to the wrong table without a word. ``LazySettings``
imported the first ``settings.py`` in any subdirectory of the working
directory whose text contained ``DEBUG``.
"""

import sys
import textwrap
from pathlib import Path

import pytest

from zeeb_api.auth import backends
from zeeb_api.conf import settings
from zeeb_api.conf.settings import Settings
from zeeb_api.exceptions import ImproperlyConfigured


@pytest.fixture(autouse=True)
def fresh_user_model_cache():
    backends.clear_user_model_cache()
    yield
    backends.clear_user_model_cache()


def _project(root: Path, name: str, settings_body: str) -> Path:
    project = root / name
    (project / name).mkdir(parents=True)
    (project / "manage.py").write_text("")
    (project / name / "__init__.py").write_text("")
    (project / name / "settings.py").write_text(textwrap.dedent(settings_body))
    return project


def _custom_user_module(root: Path, module: str) -> None:
    pkg = root / module
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "models.py").write_text(
        textwrap.dedent(
            """
            from zeeb_api.auth.models import AbstractUser


            class DiscoveryUser(AbstractUser):
                class Meta:
                    table_name = "discovery_users"
            """
        )
    )


def test_configured_settings_win_over_a_settings_py_on_disk(tmp_path, monkeypatch):
    """The module create_app loaded decides AUTH_USER_MODEL, not a file in the CWD."""
    _custom_user_module(tmp_path, "discoveryapp")
    monkeypatch.syspath_prepend(str(tmp_path))
    marker = tmp_path / "executed"
    # A project in the working directory whose settings.py names another user
    # model: it must be neither executed nor believed.
    project = _project(
        tmp_path,
        "ondisk",
        f"""
        from pathlib import Path
        Path({str(marker)!r}).write_text("yes")
        AUTH_USER_MODEL = "discoveryapp.models.DiscoveryUser"
        """,
    )
    monkeypatch.chdir(project)
    saved = (settings._wrapped, settings._configured, set(settings._explicit_settings))
    try:
        settings.configure(AUTH_USER_MODEL=None, DEBUG=True)
        from zeeb_api.auth.models import User

        assert backends.get_user_model() is User
        assert not marker.exists()
    finally:
        settings._wrapped, settings._configured, settings._explicit_settings = saved
        for name in [m for m in sys.modules if m.startswith("discoveryapp")]:
            del sys.modules[name]


def test_configured_auth_user_model_is_used(tmp_path, monkeypatch):
    _custom_user_module(tmp_path, "discoveryapp")
    monkeypatch.syspath_prepend(str(tmp_path))
    saved = (settings._wrapped, settings._configured, set(settings._explicit_settings))
    try:
        settings.configure(AUTH_USER_MODEL="discoveryapp.models.DiscoveryUser")
        assert backends.get_user_model().__name__ == "DiscoveryUser"
    finally:
        settings._wrapped, settings._configured, settings._explicit_settings = saved
        for name in [m for m in sys.modules if m.startswith("discoveryapp")]:
            del sys.modules[name]


def test_broken_project_settings_fail_loudly(tmp_path):
    project = _project(
        tmp_path,
        "broken",
        """
        AUTH_USER_MODEL = "accounts.User"
        raise KeyError("MISSING_ENV_VAR")
        """,
    )
    backends.set_project_root(project)
    try:
        with pytest.raises(ImproperlyConfigured, match="MISSING_ENV_VAR"):
            backends.get_user_model()
    finally:
        backends.set_project_root(None)


# --------------------------------------------------------------------------- #
# LazySettings auto-detection
# --------------------------------------------------------------------------- #


def test_auto_detect_ignores_directories_that_are_not_a_project(tmp_path, monkeypatch):
    stray = tmp_path / "vendored"
    stray.mkdir()
    (stray / "settings.py").write_text("DEBUG = True\n")
    monkeypatch.chdir(tmp_path)  # no manage.py here or above
    assert Settings()._auto_detect_settings() is None


def test_auto_detect_uses_the_declared_settings_module(tmp_path, monkeypatch):
    project = _project(tmp_path, "shop", "DEBUG = False\n")
    (project / "pyproject.toml").write_text('[tool.zeeb]\nsettings_module = "shop.settings"\n')
    stray = project / "docs"  # sorts before "shop"
    stray.mkdir()
    (stray / "settings.py").write_text("DEBUG = True\n")
    monkeypatch.chdir(project)
    assert Settings()._auto_detect_settings() == "shop.settings"


def test_auto_detect_skips_app_packages(tmp_path, monkeypatch):
    project = _project(tmp_path, "zoo", "DEBUG = False\n")
    (project / "apps").mkdir()
    (project / "apps" / "settings.py").write_text("DEBUG = True\n")
    monkeypatch.chdir(project)
    assert Settings()._auto_detect_settings() == "zoo.settings"


def test_auto_detect_from_inside_the_project(tmp_path, monkeypatch):
    project = _project(tmp_path, "deep", "DEBUG = False\n")
    nested = project / "apps" / "blog"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert Settings()._auto_detect_settings() == "deep.settings"
    assert str(project) in sys.path
