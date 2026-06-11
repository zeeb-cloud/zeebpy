"""Agent functions for creating standalone FastAPI route handlers."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import ensure_import
from zeeb_agents._utils.project import get_app_path

_VALID_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

_ROUTER_INIT = "router = Router()\n"

_ROUTE_TEMPLATE = """\

@router.{method}("{path}"{response_model_part})
async def {function_name}({params}):
    \"\"\"TODO: implement {function_name}.\"\"\"
    pass
"""


def _views_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "views.py"


@agent_function
async def create_route(
    app: str,
    path: str,
    method: str,
    function_name: str,
    response_model: str | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a standalone FastAPI route handler to ``apps/{app}/views.py``.

    Unlike :func:`~zeeb_agents.viewsets.create_viewset`, this creates a plain
    ``@router.<method>(path)`` function rather than a class-based ViewSet.

    Args:
        app: App directory name.
        path: URL path string (e.g. ``"/hello"`` or ``"/items/{item_id}"``).
        method: HTTP method — one of ``get``, ``post``, ``put``, ``patch``, ``delete``.
        function_name: Snake-case name for the handler function.
        response_model: Optional Pydantic model name for the response
            (e.g. ``"ItemResponse"``).  Adds ``response_model=<name>`` to the decorator.
        project_root: Auto-detected if ``None``.

    Example::

        await create_route("blog", "/posts/featured", "get", "get_featured_posts")
    """
    method = method.lower()
    if method not in _VALID_METHODS:
        return AgentResult(
            success=False,
            message=f"Invalid method '{method}'. Must be one of: {', '.join(sorted(_VALID_METHODS))}",
        )
    views = _views_file(app, project_root)
    if not views.exists():
        return AgentResult(success=False, message=f"views.py not found at {views}")

    def _write() -> None:
        content = views.read_text(encoding="utf-8")

        # Check for duplicate function
        if re.search(rf"\basync def {re.escape(function_name)}\b", content):
            raise ValueError(f"Function '{function_name}' already exists in {views.name}")

        # Ensure router is importable
        ensure_import(views, "from zeeb_api import Router")

        # Ensure router instance exists in the file
        content = views.read_text(encoding="utf-8")
        if "router = Router()" not in content and "router=Router()" not in content:
            # Insert after imports (first blank line after last import)
            lines = content.splitlines(keepends=True)
            insert_at = 0
            for idx, line in enumerate(lines):
                if line.startswith(("import ", "from ")):
                    insert_at = idx + 1
            lines.insert(insert_at, "\n" + _ROUTER_INIT)
            content = "".join(lines)
            views.write_text(content, encoding="utf-8")

        # Build route params (path params extracted from path string)
        path_params = re.findall(r"\{(\w+)\}", path)
        params = ["request"] + [f"{p}: str" for p in path_params]
        params_str = ", ".join(params)

        response_model_part = (
            f", response_model={response_model}" if response_model else ""
        )

        block = _ROUTE_TEMPLATE.format(
            method=method,
            path=path,
            response_model_part=response_model_part,
            function_name=function_name,
            params=params_str,
        )

        content = views.read_text(encoding="utf-8")
        views.write_text(content.rstrip("\n") + "\n" + block, encoding="utf-8")

    await asyncio.to_thread(_write)
    return AgentResult(
        success=True,
        message=f"Route '{method.upper()} {path}' created as '{function_name}' in apps/{app}/views.py",
        data={
            "app": app,
            "path": path,
            "method": method,
            "function_name": function_name,
            "response_model": response_model,
        },
    )
