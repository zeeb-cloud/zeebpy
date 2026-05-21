"""Agent functions for Zeeb project and app scaffolding."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.project import (
    find_project_root,
    get_app_path,
    list_apps as list_apps_util,
    load_project_settings,
    require_project_root,
    to_class_name,
)


async def create_project(name: str, directory: str = ".") -> AgentResult:
    """Create a new Zeeb project at *directory*/*name*.

    Delegates to the existing ``run_startproject`` CLI logic so the generated
    project structure is always in sync with the CLI.
    """
    def _run() -> int:
        from zeeb_orm.cli.commands.startproject import run_startproject
        return run_startproject(name, directory)

    try:
        rc = await asyncio.to_thread(_run)
        if rc == 0:
            project_path = str(Path(directory).resolve() / name)
            return AgentResult(
                success=True,
                message=f"Project '{name}' created at {project_path}",
                data={"name": name, "path": project_path},
            )
        return AgentResult(success=False, message=f"Project creation failed (exit code {rc})")
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def create_app(name: str, project_root: Path | None = None) -> AgentResult:
    """Create a new app inside an existing Zeeb project.

    Delegates to the existing ``run_startapp`` CLI logic.
    """
    def _run() -> int:
        if project_root is not None:
            import os
            old_cwd = os.getcwd()
            os.chdir(project_root)
            try:
                from zeeb_orm.cli.commands.startapp import run_startapp
                return run_startapp(name)
            finally:
                os.chdir(old_cwd)
        else:
            from zeeb_orm.cli.commands.startapp import run_startapp
            return run_startapp(name)

    try:
        rc = await asyncio.to_thread(_run)
        if rc == 0:
            root = require_project_root(project_root)
            app_path = str(get_app_path(name, root))
            return AgentResult(
                success=True,
                message=f"App '{name}' created",
                data={"name": name, "path": app_path},
            )
        return AgentResult(success=False, message=f"App creation failed (exit code {rc})")
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def delete_app(name: str, project_root: Path | None = None) -> AgentResult:
    """Delete an existing app directory from the project."""
    try:
        root = require_project_root(project_root)
        app_path = get_app_path(name, root)
        if not app_path.exists():
            return AgentResult(success=False, message=f"App '{name}' not found at {app_path}")

        await asyncio.to_thread(shutil.rmtree, app_path)
        return AgentResult(
            success=True,
            message=f"App '{name}' deleted",
            data={"name": name, "path": str(app_path)},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def get_project_info(project_root: Path | None = None) -> AgentResult:
    """Return a summary of the current project structure and settings."""
    try:
        root = require_project_root(project_root)

        def _load() -> dict:
            settings = load_project_settings(root)
            apps = list_apps_util(root)
            # Find project package name (directory with settings.py, excluding apps/)
            project_pkg = None
            for item in root.iterdir():
                if item.is_dir() and (item / "settings.py").exists():
                    project_pkg = item.name
                    break
            return {
                "root": str(root),
                "project_package": project_pkg,
                "apps": apps,
                "installed_apps": settings.get("INSTALLED_APPS", []),
                "database_url": settings.get("DATABASE", {}).get("url"),
                "auth_user_model": settings.get("AUTH_USER_MODEL"),
            }

        info = await asyncio.to_thread(_load)
        return AgentResult(
            success=True,
            message=f"Project info for '{info.get('project_package', root.name)}'",
            data=info,
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def list_apps(project_root: Path | None = None) -> AgentResult:
    """Return all app directory names found under ``apps/`` in the project.

    Args:
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)
        apps = await asyncio.to_thread(list_apps_util, root)
        return AgentResult(
            success=True,
            message=f"Found {len(apps)} app(s)",
            data={"apps": apps, "count": len(apps)},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def get_project_structure(
    project_root: Path | None = None,
    max_depth: int = 3,
) -> AgentResult:
    """Return a nested directory tree for the project.

    Skips hidden directories, ``__pycache__``, ``.git``, ``node_modules``,
    ``*.pyc`` files, and ``*.egg-info`` directories.

    Args:
        project_root: Auto-detected if ``None``.
        max_depth: Maximum directory depth to traverse (default: 3).

    Returns:
        ``AgentResult`` with ``tree`` (nested dict), ``root``, and ``file_count``.
    """
    _SKIP_DIRS = frozenset({
        "__pycache__", ".git", "node_modules", ".mypy_cache",
        ".ruff_cache", ".pytest_cache", ".venv", "venv", ".tox",
    })

    try:
        root = require_project_root(project_root)

        def _walk(path: Path, depth: int) -> dict:
            node: dict = {"name": path.name, "type": "dir", "children": []}
            if depth <= 0:
                return node
            try:
                entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
            except PermissionError:
                return node
            for entry in entries:
                if entry.name.startswith(".") and entry.name != ".env":
                    continue
                if entry.is_dir():
                    if entry.name in _SKIP_DIRS or entry.name.endswith(".egg-info"):
                        continue
                    node["children"].append(_walk(entry, depth - 1))
                else:
                    if entry.suffix in (".pyc", ".pyo"):
                        continue
                    node["children"].append({"name": entry.name, "type": "file"})
            return node

        def _build() -> tuple[dict, int]:
            tree = _walk(root, max_depth)
            # Count files
            def _count(node: dict) -> int:
                if node["type"] == "file":
                    return 1
                return sum(_count(c) for c in node.get("children", []))
            return tree, _count(tree)

        tree, file_count = await asyncio.to_thread(_build)
        return AgentResult(
            success=True,
            message=f"Project structure for '{root.name}' ({file_count} files, depth {max_depth})",
            data={"root": str(root), "max_depth": max_depth, "file_count": file_count, "tree": tree},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def rename_app(
    old_name: str,
    new_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Rename an app directory from *old_name* to *new_name*.

    Note: does not update references inside Python files — that must be done
    manually or via ``update_model`` / ``update_serializer`` etc.
    """
    try:
        root = require_project_root(project_root)
        old_path = get_app_path(old_name, root)
        new_path = get_app_path(new_name, root)

        if not old_path.exists():
            return AgentResult(success=False, message=f"App '{old_name}' not found")
        if new_path.exists():
            return AgentResult(success=False, message=f"App '{new_name}' already exists")

        await asyncio.to_thread(old_path.rename, new_path)
        return AgentResult(
            success=True,
            message=f"App renamed from '{old_name}' to '{new_name}'",
            data={"old_name": old_name, "new_name": new_name, "path": str(new_path)},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
