"""Agent functions for creating standalone FastAPI route handlers."""

from __future__ import annotations

import asyncio
import re
import textwrap
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.code_gen import ensure_import
from zeeb_agents._utils.errors import AgentError
from zeeb_agents._utils.project import get_app_path, require_project_root
from zeeb_agents._utils.validation import ensure_identifier

_VALID_METHODS = frozenset({"get", "post", "put", "patch", "delete"})

# Standalone function routes live on a FastAPI ``APIRouter`` in ``views.py``.
# ``zeeb_api`` does NOT export a ``Router`` — the router type is FastAPI's own.
_ROUTER_IMPORT = "from fastapi import APIRouter, Request"
_ROUTER_INIT = "router = APIRouter()\n"

_ROUTE_TEMPLATE = """\

@router.{method}("{path}"{response_model_part})
async def {function_name}({params}):
    \"\"\"{summary}\"\"\"
{body}
"""


def _views_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "views.py"


def _urls_file(app: str, root: Path) -> Path:
    return get_app_path(app, root) / "urls.py"


def _indent_body(body: str | None, function_name: str) -> str:
    """Return a 4-space-indented function body, with a sensible default."""
    if body is None or not body.strip():
        return f'    return {{"message": "{function_name}"}}  # TODO: implement'
    # Normalize the caller's snippet to a clean, 4-space-indented block so the
    # generated function is always valid regardless of how the body was passed.
    dedented = textwrap.dedent(body).strip("\n")
    return textwrap.indent(dedented, "    ")


@agent_function
async def create_route(
    app: str,
    path: str,
    method: str,
    function_name: str,
    response_model: str | None = None,
    body: str | None = None,
    imports: list[str] | None = None,
    project_root: Path | None = None,
) -> AgentResult:
    """Append a standalone FastAPI route handler to ``apps/{app}/views.py`` **and wire it up**.

    Use this instead of hand-writing ``@router.get(...)`` wrappers with
    ``write_file`` — it generates a valid, importable, *mounted* handler in one
    call. Unlike :func:`~zeeb_agents.viewsets.create_viewset`, this creates a
    plain ``@router.<method>(path)`` function rather than a class-based ViewSet,
    for non-CRUD endpoints (webhooks, computed/aggregate endpoints, custom auth
    flows, …).

    Args:
        app: App directory name.
        path: URL path string (e.g. ``"/hello"`` or ``"/items/{item_id}"``).
        method: HTTP method — one of ``get``, ``post``, ``put``, ``patch``, ``delete``.
        function_name: Snake-case name for the handler function.
        response_model: Optional Pydantic model name for the response
            (e.g. ``"ItemResponse"``).  Adds ``response_model=<name>`` to the decorator.
        body: The handler implementation, as Python source. Pass the real logic
            here so you **never need ``write_file`` for the endpoint body**. The
            snippet is dedented and indented to 4 spaces automatically, so you
            may pass it flush-left or pre-indented. May span multiple lines and
            should ``return`` a JSON-serializable value (dict / list / Pydantic
            model). When omitted, a ``return {"message": "<function_name>"}``
            placeholder is generated. The handler always receives a typed
            ``request: Request`` parameter you can use.
        imports: Optional list of import lines the body needs (e.g.
            ``["from .models import Post", "from .serializers import PostSerializer"]``).
            Each is added to ``views.py`` only if not already present — so the
            route is self-contained and you don't have to edit imports by hand.
        project_root: Auto-detected if ``None``.

    Returns data (on success):
        app (str), path (str), method (str), function_name (str),
        response_model (str | None): the registered route's details.
        wired (bool): ``True`` when the handler's router was auto-included into
            ``apps/{app}/urls.py`` (so it is actually served); ``False`` when
            ``urls.py`` was missing and you must include it yourself.

    Notes:
        - The handler is created on a module-level ``router = APIRouter()`` in
          ``views.py`` (FastAPI's ``APIRouter`` — ``zeeb_api`` does not export a
          ``Router``). The import and instance are added if not already present.
        - **Auto-wiring:** ``apps/{app}/urls.py`` is updated to
          ``from .views import router as {app}_api_router`` and
          ``router.include({app}_api_router)`` so the route is mounted as soon
          as the app's router is included by the project ``urls.py`` (the same
          inclusion a ViewSet needs). This is idempotent.
        - ``{name}`` segments in *path* (e.g. ``"/items/{item_id}"``) are
          auto-extracted and added to the handler as ``str`` parameters
          (``async def handler(request: Request, item_id: str): ...``).
        - Fails if *method* is invalid, ``views.py`` is missing, or a function
          named *function_name* already exists.

    Example::

        # A computed endpoint with real logic — no write_file needed:
        await create_route(
            "blog", "/posts/featured", "get", "get_featured_posts",
            response_model=None,
            imports=["from .models import Post"],
            body='''
                posts = await Post.objects.filter(featured=True).all()
                return [{"id": str(p.id), "title": p.title} for p in posts]
            ''',
        )
    """
    ensure_identifier(function_name, "function name")
    method = method.lower()
    if method not in _VALID_METHODS:
        allowed = ", ".join(sorted(_VALID_METHODS))
        return AgentResult(
            success=False,
            message=f"Invalid method '{method}'. Must be one of: {allowed}",
            data={"error_code": "invalid_input"},
        )
    views = _views_file(app, project_root)
    if not views.exists():
        return AgentResult(success=False, message=f"views.py not found at {views}")

    def _write() -> bool:
        content = views.read_text(encoding="utf-8")

        # Check for duplicate function
        if re.search(rf"\basync def {re.escape(function_name)}\b", content):
            raise AgentError(
                f"Function '{function_name}' already exists in {views.name}",
                code="already_exists",
                function=function_name,
            )

        # Ensure the FastAPI router type is importable, plus any body imports.
        ensure_import(views, _ROUTER_IMPORT)
        for imp in imports or []:
            ensure_import(views, imp)

        # Ensure a router instance exists in the file
        content = views.read_text(encoding="utf-8")
        if "router = APIRouter()" not in content and "router=APIRouter()" not in content:
            # Insert after imports (after the last import line)
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
        params = ["request: Request"] + [f"{p}: str" for p in path_params]
        params_str = ", ".join(params)

        response_model_part = (
            f", response_model={response_model}" if response_model else ""
        )

        summary = f"{method.upper()} {path}"
        block = _ROUTE_TEMPLATE.format(
            method=method,
            path=path,
            response_model_part=response_model_part,
            function_name=function_name,
            params=params_str,
            summary=summary,
            body=_indent_body(body, function_name),
        )

        content = views.read_text(encoding="utf-8")
        views.write_text(content.rstrip("\n") + "\n" + block, encoding="utf-8")

        # Auto-wire the views router into the app's urls.py so it is served.
        return _wire_urls()

    def _wire_urls() -> bool:
        urls = _urls_file(app, require_project_root(project_root))
        if not urls.exists():
            return False
        alias = f"{app}_api_router"
        include_line = f"router.include({alias})"
        content = urls.read_text(encoding="utf-8")
        if include_line in content:
            return True
        ensure_import(urls, f"from .views import router as {alias}")
        content = urls.read_text(encoding="utf-8")
        content = content.rstrip("\n") + f"\n{include_line}\n"
        urls.write_text(content, encoding="utf-8")
        return True

    wired = await asyncio.to_thread(_write)
    served = (
        "" if wired
        else " (urls.py missing — include the views router manually to serve it)"
    )
    return AgentResult(
        success=True,
        message=(
            f"Route '{method.upper()} {path}' created as '{function_name}' "
            f"in apps/{app}/views.py{served}"
        ),
        data={
            "app": app,
            "path": path,
            "method": method,
            "function_name": function_name,
            "response_model": response_model,
            "wired": wired,
        },
    )
