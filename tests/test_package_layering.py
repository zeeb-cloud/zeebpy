"""The three packages layer strictly: zeeb_api on zeeb_orm, zeeb_agents on both.

The ORM's migration tooling used to import zeeb_api's auth models by hand to
put their tables in the model state. They now arrive through the ORM's model
provider hook (``zeeb_orm.models.providers``), and these tests keep it that way.

``zeeb_orm/cli`` and ``zeeb_orm/scaffold`` are exempt: they are the framework's
command line and the source of generated projects, which use every layer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOLING = ("zeeb_orm/cli/", "zeeb_orm/scaffold/")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def _violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    problems = []
    for path in sorted((REPO / package).rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(TOOLING):
            continue
        for module in _imports(path):
            if module.split(".")[0] in forbidden:
                problems.append(f"{rel} imports {module}")
    return problems


def test_the_orm_core_imports_neither_layer_above_it():
    assert _violations("zeeb_orm", ("zeeb_api", "zeeb_agents")) == []


def test_the_api_layer_does_not_import_the_agent_layer():
    assert _violations("zeeb_api", ("zeeb_agents",)) == []


def test_zeeb_api_is_a_registered_model_provider():
    from importlib.metadata import entry_points

    from zeeb_orm.models.providers import ENTRY_POINT_GROUP, model_providers

    assert "zeeb_api" in {ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)}
    assert any(type(p).__name__ == "AuthModels" for p in model_providers())


def test_a_failing_provider_aborts_registration(tmp_path, monkeypatch):
    from zeeb_orm.exceptions import ModelRegistrationError
    from zeeb_orm.migrations.cli import _register_models
    from zeeb_orm.models import providers

    class Broken:
        app_labels = frozenset()

        def before_apps(self, project_root, settings):
            raise RuntimeError("cannot build my tables")

        def after_apps(self, project_root, settings):
            pass

    monkeypatch.setitem(providers._registered, "broken", Broken())
    (tmp_path / "manage.py").write_text("")
    with pytest.raises(ModelRegistrationError, match="cannot build my tables"):
        _register_models(tmp_path)
