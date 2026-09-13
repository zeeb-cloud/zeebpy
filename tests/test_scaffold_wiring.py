"""The scaffolding wiring lives in zeeb_orm and the agent layer adapts it.

``zeeb_orm`` sits below ``zeeb_agents`` and raises :class:`ScaffoldError`; the
adapter in ``zeeb_agents._utils.wiring`` must re-raise each one as an
``AgentError`` carrying the *same* ``error_code``, because that code is the
agent-facing contract every tool's failure payload is built from.
"""

from pathlib import Path

import pytest

from zeeb_agents._utils import wiring as agent_wiring
from zeeb_agents._utils.errors import AgentError
from zeeb_orm.scaffold import wiring as orm_wiring
from zeeb_orm.scaffold.errors import ScaffoldError


def make_project(root: Path, *, settings: str = "INSTALLED_APPS = []\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manage.py").write_text("")
    package = root / "demo"
    package.mkdir()
    (package / "settings.py").write_text(settings)
    (package / "urls.py").write_text(orm_wiring.STANDARD_URLS_TEMPLATE)
    return root


# ---------------------------------------------------------------------------
# One implementation, two entry points
# ---------------------------------------------------------------------------


def test_the_agent_layer_delegates_to_the_scaffold_package():
    for name in (
        "find_project_package",
        "ensure_installed_app",
        "append_router_include",
        "ensure_app_urls_included",
    ):
        adapter = getattr(agent_wiring, name)
        assert adapter.__wrapped__ is getattr(orm_wiring, name), name


def test_the_urls_template_has_a_single_source():
    assert agent_wiring._STANDARD_URLS_TEMPLATE is orm_wiring.STANDARD_URLS_TEMPLATE


# ---------------------------------------------------------------------------
# ScaffoldError -> AgentError, code preserved
# ---------------------------------------------------------------------------


def test_missing_settings_raises_file_not_found(tmp_path):
    with pytest.raises(ScaffoldError) as orm_exc:
        orm_wiring.find_project_package(tmp_path)
    assert orm_exc.value.code == "file_not_found"
    assert orm_exc.value.data["missing"] == "settings.py"

    with pytest.raises(AgentError) as agent_exc:
        agent_wiring.find_project_package(tmp_path)
    assert agent_exc.value.result.data["error_code"] == "file_not_found"
    assert agent_exc.value.result.data["missing"] == "settings.py"


def test_unparsable_settings_raises_invalid_input(tmp_path):
    make_project(tmp_path, settings="INSTALLED_APPS = [\n")

    with pytest.raises(ScaffoldError) as orm_exc:
        orm_wiring.ensure_installed_app(tmp_path, "blog")
    assert orm_exc.value.code == "invalid_input"

    with pytest.raises(AgentError) as agent_exc:
        agent_wiring.ensure_installed_app(tmp_path, "blog")
    assert agent_exc.value.result.data["error_code"] == "invalid_input"


def test_non_list_installed_apps_raises_setting_not_found(tmp_path):
    make_project(tmp_path, settings='INSTALLED_APPS = ("apps.blog",)\n')

    with pytest.raises(ScaffoldError) as orm_exc:
        orm_wiring.ensure_installed_app(tmp_path, "blog")
    assert orm_exc.value.code == "setting_not_found"
    assert orm_exc.value.data["setting"] == "INSTALLED_APPS"

    with pytest.raises(AgentError) as agent_exc:
        agent_wiring.ensure_installed_app(tmp_path, "blog")
    assert agent_exc.value.result.data["error_code"] == "setting_not_found"
    assert agent_exc.value.result.data["setting"] == "INSTALLED_APPS"


def test_renamed_router_raises_invalid_input(tmp_path):
    make_project(tmp_path)
    urls = tmp_path / "demo" / "urls.py"
    urls.write_text(urls.read_text().replace("router = DefaultRouter()", "api = DefaultRouter()"))

    with pytest.raises(ScaffoldError) as orm_exc:
        orm_wiring.ensure_app_urls_included(tmp_path, "blog")
    assert orm_exc.value.code == "invalid_input"

    with pytest.raises(AgentError) as agent_exc:
        agent_wiring.ensure_app_urls_included(tmp_path, "blog")
    assert agent_exc.value.result.data["error_code"] == "invalid_input"


# ---------------------------------------------------------------------------
# The happy path is unchanged through either entry point
# ---------------------------------------------------------------------------


def test_both_entry_points_wire_identically(tmp_path):
    orm_root = make_project(tmp_path / "via_orm")
    agent_root = make_project(tmp_path / "via_agent")

    assert orm_wiring.ensure_installed_app(orm_root, "blog") is True
    assert orm_wiring.ensure_app_urls_included(orm_root, "blog") is True
    assert agent_wiring.ensure_installed_app(agent_root, "blog") is True
    assert agent_wiring.ensure_app_urls_included(agent_root, "blog") is True

    for name in ("settings.py", "urls.py"):
        assert (orm_root / "demo" / name).read_text() == (
            agent_root / "demo" / name
        ).read_text(), name

    # Idempotent through either entry point.
    assert orm_wiring.ensure_installed_app(orm_root, "blog") is False
    assert agent_wiring.ensure_app_urls_included(agent_root, "blog") is False
