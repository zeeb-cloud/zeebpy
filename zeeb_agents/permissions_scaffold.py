"""Agent functions for scaffolding custom permission classes."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.errors import AgentError
from zeeb_agents._utils.project import get_app_path

_PERMISSIONS_HEADER = '''\
"""Custom permission classes for the {app} app.

Usage::

    from zeeb_api.viewsets import ModelViewSet
    from apps.{app}.permissions import {example_class}

    class MyViewSet(ModelViewSet):
        permission_classes = [{example_class}]
"""

from __future__ import annotations

from typing import Any
from fastapi import Request

from zeeb_api.permissions import BasePermission

if False:  # TYPE_CHECKING
    from zeeb_api.viewsets.base import ViewSet
'''

_LOGIC_PRESETS: dict[str, str] = {
    "deny_all": """\
        return False""",
    "allow_all": """\
        return True""",
    "owner_only": """\
        if not request.user or not request.user.is_authenticated:
            return False
        # Adjust the object ownership check for your model
        if obj is not None:
            return getattr(obj, "user_id", None) == request.user.id
        return True""",
    "staff_only": """\
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_staff", False))""",
    "authenticated": """\
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_authenticated", False))""",
}

_CLASS_TEMPLATE = '''\


class {class_name}(BasePermission):
    """{docstring}"""

    message = "{message}"

    async def has_permission(self, request: Request, view: "ViewSet") -> bool:
{body}

    async def has_object_permission(
        self,
        request: Request,
        view: "ViewSet",
        obj: Any,
    ) -> bool:
{body}
'''

_LOGIC_DOCSTRINGS: dict[str, str] = {
    "deny_all": "Deny all requests.  Useful as a safe default during development.",
    "allow_all": "Allow all requests unconditionally.",
    "owner_only": "Allow only the owner of the object (checks ``obj.user_id == request.user.id``).",
    "staff_only": "Allow only users with ``is_staff=True``.",
    "authenticated": "Allow any authenticated user.",
}

_LOGIC_MESSAGES: dict[str, str] = {
    "deny_all": "Permission denied.",
    "allow_all": "Permission granted.",
    "owner_only": "You do not have permission to access this resource.",
    "staff_only": "Staff access required.",
    "authenticated": "Authentication required.",
}


def _permissions_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "permissions.py"


@agent_function
async def create_permission_class(
    app: str,
    class_name: str,
    logic: str = "deny_all",
    project_root: Path | None = None,
) -> AgentResult:
    """Scaffold a ``BasePermission`` subclass in ``apps/{app}/permissions.py``.

    Creates ``permissions.py`` with a header if it does not yet exist.

    Args:
        app: App directory name.
        class_name: PascalCase name for the permission class.
        logic: Permission preset.  One of:

            - ``"deny_all"`` — reject all requests (safe default)
            - ``"allow_all"`` — accept all requests
            - ``"owner_only"`` — allow if ``obj.user_id == request.user.id``
            - ``"staff_only"`` — allow if ``request.user.is_staff``
            - ``"authenticated"`` — allow any authenticated user

            Defaults to ``"deny_all"``.
        project_id: The host-assigned project id (required).

    Example::

        await create_permission_class("blog", "IsPostOwner", logic="owner_only")

    Returns data (on success):
        app (str): echoes *app*
        class_name (str): echoes *class_name*
        logic (str): the preset applied
        path (str): ``permissions.py`` path relative to the project root
        file_created (bool): ``True`` if ``permissions.py`` was newly created,
            ``False`` if an existing file was appended to

    Notes:
        - An unknown ``logic`` preset returns ``success=False`` with
          ``data=None`` (no file is touched).
        - If the class already exists, the underlying ``ValueError`` is wrapped
          by the decorator into ``success=False`` with ``data=None``; when the
          file was created in the same call it is left on disk.
    """
    if logic not in _LOGIC_PRESETS:
        return AgentResult(
            success=False,
            message=f"Unknown logic preset '{logic}'. Choose from: {', '.join(_LOGIC_PRESETS)}.",
        )
    root = project_root
    perms_file = _permissions_file(app, root)

    def _write() -> bool:
        created = False
        if not perms_file.exists():
            header = _PERMISSIONS_HEADER.format(app=app, example_class=class_name)
            perms_file.write_text(header, encoding="utf-8")
            created = True

        content = perms_file.read_text(encoding="utf-8")
        if re.search(rf"^class {re.escape(class_name)}\b", content, re.MULTILINE):
            raise AgentError(
                f"Permission class '{class_name}' already exists in permissions.py.",
                code="already_exists",
                class_name=class_name,
            )

        body = _LOGIC_PRESETS[logic]
        docstring = _LOGIC_DOCSTRINGS.get(logic, "Custom permission.")
        message = _LOGIC_MESSAGES.get(logic, "Permission denied.")
        block = _CLASS_TEMPLATE.format(
            class_name=class_name,
            docstring=docstring,
            message=message,
            body=body,
        )
        perms_file.write_text(content.rstrip("\n") + block, encoding="utf-8")
        return created

    created = await asyncio.to_thread(_write)
    rel = str(perms_file.relative_to(root))
    action = "created" if created else "updated"
    return AgentResult(
        success=True,
        message=f"Permission class '{class_name}' added — {rel} {action}.",
        data={
            "app": app,
            "class_name": class_name,
            "logic": logic,
            "path": rel,
            "file_created": created,
        },
    )


@agent_function
async def list_permission_classes(
    app: str,
    project_root: Path | None = None,
) -> AgentResult:
    """Return all ``BasePermission`` subclasses in ``apps/{app}/permissions.py``.

    Args:
        app: App directory name.
        project_id: The host-assigned project id (required).

    Returns data (on success):
        app (str): echoes *app* (omitted when ``permissions.py`` is absent)
        permissions (list[str]): class names found (empty if no file)
        count (int): len(permissions)

    Notes:
        - A missing ``permissions.py`` still returns ``success=True`` with
          ``data={"permissions": [], "count": 0}`` (no ``app`` key).
    """
    perms_file = _permissions_file(app, project_root)

    if not perms_file.exists():
        return AgentResult(
            success=True,
            message=f"No permissions.py found for app '{app}'.",
            data={"permissions": [], "count": 0},
        )

    def _read() -> list[str]:
        source = perms_file.read_text(encoding="utf-8")
        return re.findall(r"^class (\w+)\s*\(", source, re.MULTILINE)

    classes = await asyncio.to_thread(_read)
    return AgentResult(
        success=True,
        message=f"Found {len(classes)} permission class(es) in apps/{app}/permissions.py.",
        data={"app": app, "permissions": classes, "count": len(classes)},
    )
