"""Agent functions for ViewSet and route management."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult
from zeeb_agents._utils.code_gen import (
    append_block,
    class_exists,
    ensure_import,
    render_viewset_class,
)
from zeeb_agents._utils.project import get_app_path, require_project_root
from zeeb_agents.models import create_model
from zeeb_agents.serializers import create_serializer


def _views_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "views.py"


def _urls_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "urls.py"


async def create_viewset(
    app: str,
    model_name: str,
    serializer_class: str | None = None,
    permission: str = "IsAuthenticatedOrReadOnly",
    project_root: Path | None = None,
) -> AgentResult:
    """Append a ``ModelViewSet`` subclass to ``apps/<app>/views.py``.

    Args:
        app: App directory name.
        model_name: The model this viewset exposes (e.g. ``"Post"``).
        serializer_class: Override the serializer class name.  Defaults to
            ``"<ModelName>Serializer"``.
        permission: Permission class name from ``zeeb_api.permissions``.
            Default: ``"IsAuthenticatedOrReadOnly"``.
        project_root: Auto-detected if ``None``.
    """
    try:
        root = require_project_root(project_root)
        path = _views_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"views.py not found at {path}")

        class_name = f"{model_name}ViewSet"

        def _write() -> None:
            content = path.read_text(encoding="utf-8")
            if class_exists(content, class_name):
                raise ValueError(f"'{class_name}' already exists in {path}")
            class_code = render_viewset_class(model_name, serializer_class, permission)
            ensure_import(path, "from zeeb_api import viewsets, permissions")
            ensure_import(path, "from zeeb_api.viewsets import ModelViewSet")
            ser = serializer_class or f"{model_name}Serializer"
            ensure_import(path, f"from .models import {model_name}")
            ensure_import(path, f"from .serializers import {ser}")
            append_block(path, class_code)

        await asyncio.to_thread(_write)
        return AgentResult(
            success=True,
            message=f"'{class_name}' created in apps/{app}/views.py",
            data={"app": app, "model": model_name, "viewset": class_name},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def add_viewset_action(
    app: str,
    model_name: str,
    action_name: str,
    detail: bool = True,
    methods: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a custom ``@action`` method to an existing ViewSet.

    Args:
        action_name: Snake-case method name (e.g. ``"publish"``).
        detail: Whether the action operates on a single instance (``pk`` in URL).
        methods: HTTP methods (default: ``["get"]``).
    """
    try:
        root = require_project_root(project_root)
        path = _views_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"views.py not found at {path}")

        class_name = f"{model_name}ViewSet"
        action_methods = methods or ["get"]

        def _insert() -> None:
            content = path.read_text(encoding="utf-8")
            if not class_exists(content, class_name):
                raise ValueError(f"'{class_name}' not found in {path}")

            methods_repr = ", ".join(f'"{m}"' for m in action_methods)
            action_code = (
                f'    @action(detail={detail}, methods=[{methods_repr}])\n'
                f'    async def {action_name}(self, request, pk=None):\n'
                f'        pass  # TODO: implement {action_name}\n'
            )

            # Insert before the last line of the class (before next class or EOF)
            pattern = re.compile(
                rf"(class {re.escape(class_name)}\b.*?)(\nclass |\Z)",
                re.DOTALL,
            )
            def _replace(m: re.Match) -> str:
                return m.group(1) + "\n" + action_code + m.group(2)

            new_content = pattern.sub(_replace, content, count=1)
            ensure_import(path, "from zeeb_api.viewsets import action")
            path.write_text(new_content, encoding="utf-8")

        await asyncio.to_thread(_insert)
        return AgentResult(
            success=True,
            message=f"Action '{action_name}' added to '{class_name}'",
            data={"app": app, "viewset": class_name, "action": action_name},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def register_route(
    app: str,
    model_name: str,
    url_prefix: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Register a ViewSet with the app's router in ``apps/<app>/urls.py``."""
    try:
        root = require_project_root(project_root)
        path = _urls_file(app, root)
        if not path.exists():
            return AgentResult(success=False, message=f"urls.py not found at {path}")

        prefix = url_prefix or app
        viewset_name = f"{model_name}ViewSet"

        def _write() -> None:
            content = path.read_text(encoding="utf-8")
            register_line = f'router.register("{prefix}", {viewset_name})'
            if register_line in content:
                raise ValueError(f"Route for '{viewset_name}' already registered")
            ensure_import(path, f"from .views import {viewset_name}")
            # Append the register call after any existing router.register lines,
            # or just append to the file
            content = path.read_text(encoding="utf-8")
            content = content.rstrip("\n") + f"\n{register_line}\n"
            path.write_text(content, encoding="utf-8")

        await asyncio.to_thread(_write)
        return AgentResult(
            success=True,
            message=f"'{viewset_name}' registered at '{prefix}/'",
            data={"app": app, "viewset": viewset_name, "prefix": prefix},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))


async def generate_crud(
    app: str,
    model_name: str,
    fields: list[dict],
    serializer_fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    permission: str = "IsAuthenticatedOrReadOnly",
    project_root: Path | None = None,
) -> AgentResult:
    """One-shot scaffold: create model + serializer + viewset + register route.

    Args:
        app: App directory name.
        model_name: PascalCase class name.
        fields: Field spec dicts (see :func:`~zeeb_agents.models.create_model`).
        serializer_fields: Fields to expose.  Defaults to all model field names
            plus ``"id"``.
        read_only_fields: Read-only serializer fields.
        permission: ViewSet permission class.
        project_root: Auto-detected if ``None``.

    Returns an :class:`AgentResult` with a ``"steps"`` list in ``data``.
    """
    steps: list[str] = []
    errors: list[str] = []

    root = require_project_root(project_root)

    # 1. Model
    result = await create_model(app, model_name, fields, project_root=root)
    if result.success:
        steps.append(f"Created model '{model_name}'")
    else:
        errors.append(f"model: {result.message}")

    # 2. Serializer
    ser_fields = serializer_fields or (["id"] + [f["name"] for f in fields])
    result = await create_serializer(
        app, model_name, ser_fields, read_only_fields, project_root=root
    )
    if result.success:
        steps.append(f"Created serializer '{model_name}Serializer'")
    else:
        errors.append(f"serializer: {result.message}")

    # 3. ViewSet
    result = await create_viewset(app, model_name, permission=permission, project_root=root)
    if result.success:
        steps.append(f"Created viewset '{model_name}ViewSet'")
    else:
        errors.append(f"viewset: {result.message}")

    # 4. Route
    result = await register_route(app, model_name, project_root=root)
    if result.success:
        steps.append(f"Registered route '{app}/'")
    else:
        errors.append(f"route: {result.message}")

    if errors:
        return AgentResult(
            success=False,
            message=f"CRUD generation partially failed: {'; '.join(errors)}",
            data={"steps_completed": steps, "errors": errors},
        )
    return AgentResult(
        success=True,
        message=f"CRUD for '{model_name}' generated successfully",
        data={"steps": steps},
    )


async def list_endpoints(project_root: Path | None = None) -> AgentResult:
    """Return all ``router.register(...)`` calls found across all app ``urls.py`` files."""
    try:
        root = require_project_root(project_root)
        from zeeb_agents._utils.project import list_apps

        def _scan() -> list[dict]:
            endpoints = []
            for app in list_apps(root):
                urls_path = _urls_file(app, root)
                if not urls_path.exists():
                    continue
                content = urls_path.read_text(encoding="utf-8")
                for m in re.finditer(r'router\.register\(["\']([^"\']+)["\'],\s*(\w+)', content):
                    endpoints.append({
                        "app": app,
                        "prefix": m.group(1),
                        "viewset": m.group(2),
                    })
            return endpoints

        endpoints = await asyncio.to_thread(_scan)
        return AgentResult(
            success=True,
            message=f"Found {len(endpoints)} registered endpoint(s)",
            data={"endpoints": endpoints},
        )
    except Exception as exc:
        return AgentResult(success=False, message=str(exc))
