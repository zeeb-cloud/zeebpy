"""One process, several projects: an agent call sees its own project only.

A long-lived server (an MCP host) calls the agent layer on projects by path.
The server's own environment used to be the projects' environment — a
``DATABASE_URL`` it was started with became every project's database — and a
project's imported ``apps.*`` code stayed loaded when the next call was on a
different project, which names its packages ``apps.*`` too.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

import zeeb_agents as agents
from zeeb_agents._utils.isolation import loaded_project_root, project_scope


@pytest.fixture(autouse=True)
def _isolate_global_state():
    from zeeb_orm.models.base import _model_registry, metadata

    registry_before = dict(_model_registry)
    tables_before = set(metadata.tables)
    modules_before = set(sys.modules)
    path_before = list(sys.path)
    yield
    _model_registry.clear()
    _model_registry.update(registry_before)
    for name in set(metadata.tables) - tables_before:
        metadata.remove(metadata.tables[name])
    for name in set(sys.modules) - modules_before:
        if name == "apps" or name.startswith("apps.") or name == "settings":
            del sys.modules[name]
    sys.path[:] = path_before


async def _project(tmp_path: Path, name: str) -> Path:
    res = await agents.create_project(name, directory=str(tmp_path))
    assert res.success, res.message
    return tmp_path / name


async def test_the_servers_database_url_is_not_the_projects(tmp_path, monkeypatch):
    root = await _project(tmp_path, "alpha")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://server/its-own-db")

    res = await agents.get_project_info(project_id=root)

    assert res.success, res.message
    assert "its-own-db" not in res.data["database_url"]
    assert res.data["database_url"].startswith("sqlite")


async def test_the_projects_own_env_file_is_read(tmp_path, monkeypatch):
    root = await _project(tmp_path, "alpha")
    with (root / ".env").open("a", encoding="utf-8") as env:
        env.write("\nDATABASE_URL=sqlite+aiosqlite:///alpha-own.sqlite3\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://server/its-own-db")

    res = await agents.get_project_info(project_id=root)

    assert res.data["database_url"] == "sqlite+aiosqlite:///alpha-own.sqlite3"


def _content_models(registry: dict) -> list[str]:
    """Models of the `content` app, which only project alpha has."""
    return [
        key
        for key, model in registry.items()
        if getattr(model, "__module__", "").startswith("apps.content")
    ]


async def test_switching_projects_drops_the_previous_projects_models(tmp_path):
    from zeeb_orm.models.base import _model_registry

    alpha = await _project(tmp_path, "alpha")
    beta = await _project(tmp_path, "beta")
    spec = {
        "name": "blog",
        "app": "content",
        "entities": [{"name": "Post", "fields": [{"name": "title", "type": "string"}]}],
    }
    res = await agents.build_feature(spec, migrate=False, verify=False, project_id=alpha)
    assert res.success, res.message
    res = await agents.make_migrations(project_id=alpha)
    assert res.success, res.message
    assert _content_models(_model_registry)

    res = await agents.make_migrations(project_id=beta)

    assert res.success, res.message
    assert loaded_project_root() == beta.resolve()
    assert not _content_models(_model_registry)
    assert not list((beta / "migrations").glob("*post*"))


async def test_a_call_on_another_project_waits_for_the_running_one(tmp_path):
    order: list[str] = []
    release = asyncio.Event()

    async def hold(root: Path) -> None:
        async with project_scope(root):
            order.append("alpha-in")
            await release.wait()
            order.append("alpha-out")

    async def other(root: Path) -> None:
        async with project_scope(root):
            order.append("beta-in")

    alpha, beta = tmp_path / "a", tmp_path / "b"
    alpha.mkdir()
    beta.mkdir()
    first = asyncio.create_task(hold(alpha))
    await asyncio.sleep(0)
    second = asyncio.create_task(other(beta))
    await asyncio.sleep(0.01)
    assert order == ["alpha-in"]
    release.set()
    await asyncio.gather(first, second)
    assert order == ["alpha-in", "alpha-out", "beta-in"]
