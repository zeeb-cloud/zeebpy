"""Agent functions for Zeeb project and app scaffolding."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import close_matches, fail
from zeeb_agents._utils.project import (
    get_app_path,
    load_project_settings,
    require_project_root,
    write_framework_marker,
)
from zeeb_agents._utils.project import (
    list_apps as list_apps_util,
)
from zeeb_agents._utils.resolver import resolve_project_id
from zeeb_agents._utils.validation import ensure_app_exists, ensure_identifier
from zeeb_agents._utils.wiring import (
    ensure_app_urls_included,
    ensure_installed_app,
    find_project_package,
)


@agent_function(resolve_project=False)
async def create_project(
    name: str,
    project_id: str | None = None,
    framework: str = "zeebpy",
    directory: str = ".",
) -> AgentResult:
    """Create a new Zeeb project and record its framework.

    Delegates to the existing ``run_startproject`` CLI logic so the generated
    project structure is always in sync with the CLI, then writes a
    ``[tool.zeeb] framework`` marker to the project's ``pyproject.toml`` (read
    back by framework-aware doc serving).

    When *project_id* is given, the project is created at the location the host
    resolver maps it to (its parent directory is used as the scaffold target, so
    the resolved path becomes the project root); the resolved basename should
    equal *name*. When *project_id* is omitted, the project is created at
    ``directory/name`` and the caller registers the id with the host itself.

    Returns data (on success):
        name (str): the project name
        project_id (str | None): the id passed in (echoed back)
        framework (str): the recorded framework (default ``"zeebpy"``)
        path (str): absolute path to the created project directory

    Notes:
        - A non-zero CLI exit code returns ``success=False`` with ``data=None``.
        - Unlike other tools, ``project_id`` is optional here (this is the
          bootstrapping call) and resolves a not-yet-existing location.
    """
    ensure_identifier(name, "project name")
    if project_id is not None:
        target = resolve_project_id(project_id, must_exist=False)
        directory = str(target.parent)

    def _run() -> int:
        from zeeb_orm.cli.commands.startproject import run_startproject
        return run_startproject(name, directory)

    rc = await asyncio.to_thread(_run)
    if rc == 0:
        project_path = Path(directory).resolve() / name
        write_framework_marker(project_path, framework)
        return AgentResult(
            success=True,
            message=f"Project '{name}' created at {project_path}",
            data={
                "name": name,
                "project_id": project_id,
                "framework": framework,
                "path": str(project_path),
            },
        )
    return AgentResult(success=False, message=f"Project creation failed (exit code {rc})")


@agent_function(aliases={"app": "name"})
async def create_app(
    name: str,
    wire: bool = True,
    project_root: Path | None = None,
) -> AgentResult:
    """Create a new app inside an existing Zeeb project — wired and served.

    Delegates to the existing ``run_startapp`` CLI logic to scaffold
    ``apps/<name>/``, then (when *wire* is true, the default) **registers the
    app so it is actually served**: appends ``"apps.<name>"`` to
    ``INSTALLED_APPS`` (so migrations see its models) and includes the app's
    router in the project ``urls.py`` (so its endpoints are routed). Both edits
    are idempotent.

    Args:
        name: App directory name (a valid Python identifier). Also accepted as
            ``app=`` for consistency with the other app-scoped tools.
        wire: When true (default) auto-register the app in ``INSTALLED_APPS``
            and include its router in the project ``urls.py``. Pass ``False`` to
            scaffold the files only (wire later with
            :func:`install_app` / :func:`wire_app_urls`).
        project_id: The host-assigned project id (required).

    Returns data (on success):
        name (str): the app name
        path (str): absolute path to the created app directory
        installed_apps_updated (bool): whether ``INSTALLED_APPS`` was changed
            (``False`` when *wire* is false or the app was already registered)
        urls_wired (bool): whether the project ``urls.py`` was changed
            (``False`` when *wire* is false or the router was already included)

    Notes:
        - A non-zero CLI exit code returns ``success=False`` with
          ``data=None``.
        - Wiring the router with **no prefix** is intentional: ``register_route``
          mounts each ViewSet under its own segment (the app name by default),
          and ``DefaultRouter.include`` nests prefixes.
    """
    ensure_identifier(name, "app name")
    root = require_project_root(project_root)

    def _run() -> int:
        old_cwd = os.getcwd()
        os.chdir(root)
        try:
            from zeeb_orm.cli.commands.startapp import run_startapp
            return run_startapp(name)
        finally:
            os.chdir(old_cwd)

    rc = await asyncio.to_thread(_run)
    if rc != 0:
        return AgentResult(success=False, message=f"App creation failed (exit code {rc})")

    installed_updated = False
    urls_wired = False
    if wire:
        def _wire() -> tuple[bool, bool]:
            return (
                ensure_installed_app(root, name),
                ensure_app_urls_included(root, name),
            )

        installed_updated, urls_wired = await asyncio.to_thread(_wire)

    app_path = str(get_app_path(name, root))
    served = " and wired into the project" if wire else ""
    return AgentResult(
        success=True,
        message=f"App '{name}' created{served}",
        data={
            "name": name,
            "path": app_path,
            "installed_apps_updated": installed_updated,
            "urls_wired": urls_wired,
        },
    )


@agent_function
async def install_app(app: str, project_root: Path | None = None) -> AgentResult:
    """Register an app in ``INSTALLED_APPS`` so migrations see its models.

    Idempotent recovery/explicit-control tool: appends ``"apps.<app>"`` to
    ``INSTALLED_APPS`` in ``settings.py`` if not already present. ``create_app``
    does this automatically; use this to register a pre-existing app (or one
    created with ``wire=False``).

    Returns data (on success):
        app (str): the app name
        changed (bool): whether ``INSTALLED_APPS`` was modified (``False`` if the
            app was already registered)
    """
    root = require_project_root(project_root)
    ensure_app_exists(app, root)
    changed = await asyncio.to_thread(ensure_installed_app, root, app)
    verb = "registered in" if changed else "already in"
    return AgentResult(
        success=True,
        message=f"App '{app}' {verb} INSTALLED_APPS",
        data={"app": app, "changed": changed},
    )


@agent_function
async def wire_app_urls(
    app: str,
    prefix: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Include an app's router in the project ``urls.py`` so its routes are served.

    Idempotent recovery/explicit-control tool: adds
    ``from apps.<app>.urls import router as <app>_router`` and
    ``router.include(<app>_router)`` to the project ``urls.py``. ``create_app``
    does this automatically; use this to wire a pre-existing app (or one created
    with ``wire=False``).

    Args:
        app: App directory name.
        prefix: Optional include prefix. Leave unset (the default) — ViewSets
            registered via ``register_route`` already carry their own URL segment
            and ``include`` nests prefixes, so a prefix here would double it.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): the app name
        changed (bool): whether the project ``urls.py`` was modified (``False``
            if the router was already included)
    """
    root = require_project_root(project_root)
    ensure_app_exists(app, root)
    changed = await asyncio.to_thread(ensure_app_urls_included, root, app, prefix)
    verb = "wired into" if changed else "already in"
    return AgentResult(
        success=True,
        message=f"App '{app}' router {verb} project urls.py",
        data={"app": app, "changed": changed},
    )


@agent_function(aliases={"app": "name"})
async def delete_app(name: str, project_root: Path | None = None) -> AgentResult:
    """Delete an existing app directory from the project.

    ``name`` is also accepted as ``app=`` for consistency with the other
    app-scoped tools.

    Returns data (on success):
        name (str): the app name
        path (str): absolute path to the deleted app directory

    Notes:
        - If the app directory does not exist, returns ``success=False``
          with ``data=None``.
    """
    root = require_project_root(project_root)
    app_path = get_app_path(name, root)
    if not app_path.exists():
        apps = list_apps_util(root)
        suggestions = close_matches(name, apps)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        return fail(
            f"App '{name}' not found at {app_path}.{hint}",
            code="app_not_found",
            suggestions=suggestions,
            apps=apps,
        )

    await asyncio.to_thread(shutil.rmtree, app_path)
    return AgentResult(
        success=True,
        message=f"App '{name}' deleted",
        data={"name": name, "path": str(app_path)},
    )


@agent_function
async def get_project_info(project_root: Path | None = None) -> AgentResult:
    """Return a summary of the current project structure and settings.

    Returns data (on success):
        root (str): absolute path to the project root
        project_package (str | None): name of the dir containing settings.py,
            or ``None`` if not found
        apps (list[str]): app directory names under ``apps/``
        installed_apps (list): ``INSTALLED_APPS`` from settings
        database_url (str | None): the configured database url, if any
        auth_user_model (str | None): the ``AUTH_USER_MODEL`` setting, if any
    """
    root = project_root

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


@agent_function
async def list_apps(project_root: Path | None = None) -> AgentResult:
    """Return all app directory names found under ``apps/`` in the project.

    Args:
        project_id: The host-assigned project id (required).

    Returns data (on success):
        apps (list[str]): app directory names under ``apps/``
        count (int): len(apps)
    """
    apps = await asyncio.to_thread(list_apps_util, project_root)
    return AgentResult(
        success=True,
        message=f"Found {len(apps)} app(s)",
        data={"apps": apps, "count": len(apps)},
    )


@agent_function
async def get_project_structure(
    project_root: Path | None = None,
    max_depth: int = 3,
) -> AgentResult:
    """Return a nested directory tree for the project.

    Skips hidden directories, ``__pycache__``, ``.git``, ``node_modules``,
    ``*.pyc`` files, and ``*.egg-info`` directories.

    Args:
        project_id: The host-assigned project id (required).
        max_depth: Maximum directory depth to traverse (default: 3).

    Returns data (on success):
        tree (dict): nested tree. Directory nodes are
            ``{"name": str, "type": "dir", "children": [...]}``; file nodes
            are ``{"name": str, "type": "file"}``.
        root (str): absolute path of the project root.
        max_depth (int): the depth that was traversed.
        file_count (int): number of file nodes in the tree.

    Notes:
        - Directories deeper than ``max_depth`` appear as ``dir`` nodes with
          empty ``children`` — an empty list does not mean the directory is
          empty.
    """
    _SKIP_DIRS = frozenset({
        "__pycache__", ".git", "node_modules", ".mypy_cache",
        ".ruff_cache", ".pytest_cache", ".venv", "venv", ".tox",
    })

    root = project_root

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


@agent_function
async def describe_project(project_root: Path | None = None) -> AgentResult:
    """Return one computed, cross-checked snapshot of the whole project state.

    A single orientation call so you never have to stitch ``list_models`` +
    ``list_endpoints`` + ``get_migration_status`` + manual settings/urls diffs.
    It computes whether each app is actually **served** (registered in
    ``INSTALLED_APPS`` *and* its router included in the project ``urls.py``) and
    collects any wiring/migration gaps into ``warnings`` so silent breakage
    (endpoints that 404, models that never migrate) becomes visible.

    Returns data (on success):
        apps (list[dict]): each ``{"name": str, "installed": bool,
            "urls_included": bool, "model_count": int}``.
        models (list[dict]): as :func:`list_models` (``{app, model, fields}``).
        endpoints (list[dict]): each registered ViewSet as
            ``{"app", "prefix", "viewset", "served": bool}`` — ``served`` is
            ``False`` when the app's router is not included in ``urls.py``.
        migrations (dict): ``{"applied": [...], "pending": [...],
            "pending_count": int}`` (``"available": False`` when the migration
            state could not be read, e.g. the DB is not initialised yet).
        runtime (dict): ``{"configured": bool, "preview_url": str | None,
            "openapi_url": str | None}`` from the platform preview runtime.
        served (bool): whether at least one endpoint is actually routed.
        warnings (list[str]): human-readable wiring/migration gaps to fix.

    Notes:
        - Never hard-fails on a sub-part: an unreadable migration/runtime state
          degrades to ``available``/``configured`` ``False`` instead of erroring.
    """
    from zeeb_agents.migrations import get_migration_status
    from zeeb_agents.models import list_models
    from zeeb_agents.runtime import get_project_reference
    from zeeb_agents.viewsets import list_endpoints

    root = require_project_root(project_root)

    def _wiring_state() -> tuple[set[str], set[str]]:
        """Return (installed app names, apps whose router is included)."""
        settings = load_project_settings(root)
        installed = {
            entry.split(".", 1)[1]
            for entry in (settings.get("INSTALLED_APPS", []) or [])
            if isinstance(entry, str) and entry.startswith("apps.")
        }
        included: set[str] = set()
        try:
            pkg = find_project_package(root)
            urls_text = (pkg / "urls.py").read_text(encoding="utf-8")
        except Exception:
            return installed, included
        import re

        for m in re.finditer(r"router\.include\(\s*(\w+?)_router\b", urls_text):
            included.add(m.group(1))
        return installed, included

    installed, included = await asyncio.to_thread(_wiring_state)

    models_res = await list_models(project_id=root)
    models = (models_res.data or {}).get("models", []) if models_res.success else []
    model_counts: dict[str, int] = {}
    for m in models:
        model_counts[m["app"]] = model_counts.get(m["app"], 0) + 1

    endpoints_res = await list_endpoints(project_id=root)
    raw_endpoints = (
        (endpoints_res.data or {}).get("endpoints", []) if endpoints_res.success else []
    )
    endpoints = [
        {**ep, "served": ep["app"] in included} for ep in raw_endpoints
    ]

    apps = [
        {
            "name": app,
            "installed": app in installed,
            "urls_included": app in included,
            "model_count": model_counts.get(app, 0),
        }
        for app in list_apps_util(root)
    ]

    migr_res = await get_migration_status(project_id=root)
    if migr_res.success:
        migr_data = migr_res.data or {}
        migrations = {
            "applied": migr_data.get("applied", []),
            "pending": migr_data.get("pending", []),
            "pending_count": migr_data.get("pending_count", 0),
            "available": True,
        }
    else:
        migrations = {"applied": [], "pending": [], "pending_count": 0, "available": False}

    runtime_res = await get_project_reference(project_id=root)
    if runtime_res.success:
        rt_data = runtime_res.data or {}
        runtime = {
            "configured": True,
            "preview_url": rt_data.get("preview_url"),
            "openapi_url": rt_data.get("openapi_url"),
        }
    else:
        runtime = {"configured": False, "preview_url": None, "openapi_url": None}

    warnings: list[str] = []
    for a in apps:
        if a["model_count"] and not a["installed"]:
            warnings.append(
                f"App '{a['name']}' has models but is not in INSTALLED_APPS — "
                f"its models will not migrate. Run install_app('{a['name']}')."
            )
    for ep in endpoints:
        if not ep["served"]:
            warnings.append(
                f"Endpoint '{ep['prefix']}' ({ep['viewset']}) is registered but "
                f"app '{ep['app']}' router is not included in urls.py — it 404s. "
                f"Run wire_app_urls('{ep['app']}')."
            )
    if migrations["available"] and migrations["pending_count"]:
        warnings.append(
            f"{migrations['pending_count']} pending migration(s) — run run_migrations()."
        )

    served = any(ep["served"] for ep in endpoints)
    return AgentResult(
        success=True,
        message=(
            f"{len(apps)} app(s), {len(models)} model(s), "
            f"{len(endpoints)} endpoint(s); {len(warnings)} warning(s)"
        ),
        data={
            "apps": apps,
            "models": models,
            "endpoints": endpoints,
            "migrations": migrations,
            "runtime": runtime,
            "served": served,
            "warnings": warnings,
        },
    )


@agent_function
async def rename_app(
    old_name: str,
    new_name: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Rename an app directory from *old_name* to *new_name*.

    Note: does not update references inside Python files — that must be done
    manually or via ``update_model`` / ``update_serializer`` etc.

    Returns data (on success):
        old_name (str): the original app name
        new_name (str): the new app name
        path (str): absolute path to the renamed app directory

    Notes:
        - If *old_name* does not exist or *new_name* already exists, returns
          ``success=False`` with ``data=None``.
    """
    ensure_identifier(new_name, "app name")
    root = project_root
    old_path = get_app_path(old_name, root)
    new_path = get_app_path(new_name, root)

    if not old_path.exists():
        apps = list_apps_util(require_project_root(root))
        suggestions = close_matches(old_name, apps)
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        return fail(
            f"App '{old_name}' not found.{hint}",
            code="app_not_found",
            suggestions=suggestions,
            apps=apps,
        )
    if new_path.exists():
        return fail(f"App '{new_name}' already exists", code="already_exists")

    await asyncio.to_thread(old_path.rename, new_path)
    return AgentResult(
        success=True,
        message=f"App renamed from '{old_name}' to '{new_name}'",
        data={"old_name": old_name, "new_name": new_name, "path": str(new_path)},
    )
