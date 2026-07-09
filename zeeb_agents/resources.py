"""MCP resource content functions for zeeb_agents documentation.

Provides callable functions that return rich markdown documentation
suitable for serving as MCP resource content.  Each function corresponds
to one MCP resource URI:

    mcp://docs/principles           → get_principles_doc()
    mcp://docs/capabilities         → get_capabilities_doc()
    mcp://docs/project-lifecycle    → get_project_lifecycle_doc()
    mcp://docs/backend-generation   → get_backend_generation_doc()
    mcp://docs/frontend-generation  → get_frontend_generation_doc()
    mcp://docs/deployment           → get_deployment_doc()

The markdown source lives in ``zeeb_agents/agent_docs/`` so it can be
edited, versioned, and diffed like any other documentation file.

All functions return an :class:`~zeeb_agents._utils.AgentResult` with:

- ``data["content"]`` — markdown string ready to serve as resource body
- ``data["uri"]``     — the canonical ``mcp://`` URI
- ``data["mime_type"]`` — always ``"text/markdown"``

When *project_root* is supplied, live project context (apps, migration
status, readiness check) is appended to the static documentation.

Usage from an MCP server::

    from zeeb_agents import get_resource, get_capabilities_doc

    # Dispatch by URI
    result = await get_resource("mcp://docs/capabilities")
    print(result.data["content"])

    # Or call directly
    result = await get_capabilities_doc()
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function
from zeeb_agents._utils.project import detect_framework

# ---------------------------------------------------------------------------
# URI registry
# ---------------------------------------------------------------------------

RESOURCE_URIS = {
    "principles":          "mcp://docs/principles",
    "capabilities":        "mcp://docs/capabilities",
    "project-lifecycle":   "mcp://docs/project-lifecycle",
    "backend-generation":  "mcp://docs/backend-generation",
    "frontend-generation": "mcp://docs/frontend-generation",
    "deployment":          "mcp://docs/deployment",
    "recipes":             "mcp://docs/recipes",
    "error-recovery":      "mcp://docs/error-recovery",
}

_MIME = "text/markdown"

# agent_docs/ lives next to this file; each framework has its own subdirectory
# (agent_docs/<framework>/<key>.md).  zeebpy is the built-in fallback set.
_DOCS_DIR = Path(__file__).parent / "agent_docs"
_FALLBACK_FRAMEWORK = "zeebpy"


def _read_doc(key: str, framework: str = _FALLBACK_FRAMEWORK) -> str:
    """Read a markdown doc *key* for *framework*, falling back to zeebpy.

    Serves ``agent_docs/<framework>/<key>.md`` when it exists, otherwise the
    built-in ``agent_docs/zeebpy/<key>.md``.
    """
    path = _DOCS_DIR / framework / f"{key}.md"
    if not path.exists():
        path = _DOCS_DIR / _FALLBACK_FRAMEWORK / f"{key}.md"
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Dynamic project context helpers
# ---------------------------------------------------------------------------

async def _build_project_context(project_root: Path) -> str:
    """Return a markdown section with live project state."""
    from zeeb_agents.migrations import get_migration_status
    from zeeb_agents.project import get_project_info
    from zeeb_agents.project import list_apps as _list_apps

    lines: list[str] = ["\n---\n\n## Current Project Context\n"]

    try:
        info = await get_project_info(project_root)
        if info.success and info.data:
            lines.append(f"**Project root:** `{info.data.get('root', project_root)}`\n\n")
    except Exception:
        pass

    try:
        apps_result = await _list_apps(project_root)
        if apps_result.success and apps_result.data:
            apps = apps_result.data.get("apps", [])
            if apps:
                lines.append(f"**Apps ({len(apps)}):** " + ", ".join(f"`{a}`" for a in apps) + "\n\n")
    except Exception:
        pass

    try:
        mig = await get_migration_status(project_root)
        if mig.success and mig.data:
            pending = mig.data.get("pending", [])
            applied = mig.data.get("applied", [])
            lines.append(f"**Migrations:** {len(applied)} applied, {len(pending)} pending")
            if pending:
                lines.append(" ⚠️ — run `await run_migrations()` to apply")
            lines.append("\n\n")
    except Exception:
        pass

    return "".join(lines) if len(lines) > 1 else ""


async def _build_deployment_context(project_root: Path) -> str:
    """Return readiness check as markdown."""
    from zeeb_agents.deploy import check_production_readiness

    lines: list[str] = ["\n---\n\n## Current Readiness Check\n"]
    try:
        result = await check_production_readiness(project_root)
        if result.data:
            for issue in result.data.get("issues", []):
                lines.append(f"- ❌ {issue}\n")
            for passed in result.data.get("passed", []):
                lines.append(f"- ✅ {passed}\n")
    except Exception:
        lines.append("_Could not run readiness check._\n")

    return "".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Public resource functions
# ---------------------------------------------------------------------------

@agent_function(optional_project=True)
async def get_principles_doc(
    project_root: Path | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Return the operating-principles and special-cases guide.

    Reads ``zeeb_agents/agent_docs/principles.md``.
    The single most useful document to read first: it explains the
    ``AgentResult`` contract, the ``@agent_function`` semantics (functions
    never raise, ``project_root`` auto-detection), the return-shape
    conventions, the security model (read-only SQL gate, path boundary),
    the documented gotchas, and the framework concepts a coding agent needs
    (how auth works, how URLs get registered, the generation flow).

    Maps to MCP resource ``mcp://docs/principles``.

    Args:
        project_id: When provided, a live project context section is appended.
        tool_prefix: String prepended to every ``{prefix}`` placeholder in the
            markdown.  Defaults to ``""`` (no prefix).

    Returns data (on success):
        content (str): the guide as markdown, with ``{prefix}`` placeholders
            replaced by *tool_prefix*.
        uri (str): ``"mcp://docs/principles"``.
        mime_type (str): ``"text/markdown"``.
    """
    framework = framework or detect_framework(project_root)
    content = await asyncio.to_thread(_read_doc, "principles", framework)
    content = content.replace("{prefix}", tool_prefix)
    if project_root is not None:
        content += await _build_project_context(project_root)
    return AgentResult(
        success=True,
        message="Principles documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["principles"], "mime_type": _MIME},
    )


@agent_function(optional_project=True)
async def get_capabilities_doc(
    project_root: Path | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Return the full `zeeb_agents` capability reference.

    Reads ``zeeb_agents/agent_docs/capabilities.md``.
    Covers all 80+ public functions organised by module, the ``AgentResult``
    contract, and import examples.

    Maps to MCP resource ``mcp://docs/capabilities``.

    Args:
        project_id: When provided, a live project context section is appended.
        tool_prefix: String prepended to every ``{prefix}`` placeholder in the
            markdown, matching the tool name prefix your MCP server uses.
            For example ``"zeeb_"`` turns ``{prefix}create_model`` into
            ``zeeb_create_model``.  Defaults to ``""`` (no prefix).

    Returns data (on success):
        content (str): the reference as markdown, with ``{prefix}``
            placeholders replaced by *tool_prefix*.
        uri (str): ``"mcp://docs/capabilities"``.
        mime_type (str): ``"text/markdown"``.
    """
    framework = framework or detect_framework(project_root)
    content = await asyncio.to_thread(_read_doc, "capabilities", framework)
    content = content.replace("{prefix}", tool_prefix)
    if project_root is not None:
        content += await _build_project_context(project_root)
    return AgentResult(
        success=True,
        message="Capabilities documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["capabilities"], "mime_type": _MIME},
    )


@agent_function(optional_project=True)
async def get_project_lifecycle_doc(
    project_root: Path | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Return the end-to-end project lifecycle guide.

    Reads ``zeeb_agents/agent_docs/project-lifecycle.md``.
    Covers every step from `create_project` through development, iteration,
    and monitoring.

    Maps to MCP resource ``mcp://docs/project-lifecycle``.

    Args:
        project_id: When provided, current app list and migration status
            are appended.
        tool_prefix: String prepended to every ``{prefix}`` placeholder in the
            markdown.  Defaults to ``""`` (no prefix).

    Returns data (on success):
        content (str): the guide as markdown, with ``{prefix}`` placeholders
            replaced by *tool_prefix*.
        uri (str): ``"mcp://docs/project-lifecycle"``.
        mime_type (str): ``"text/markdown"``.
    """
    framework = framework or detect_framework(project_root)
    content = await asyncio.to_thread(_read_doc, "project-lifecycle", framework)
    content = content.replace("{prefix}", tool_prefix)
    if project_root is not None:
        content += await _build_project_context(project_root)
    return AgentResult(
        success=True,
        message="Project lifecycle documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["project-lifecycle"], "mime_type": _MIME},
    )


@agent_function(optional_project=True)
async def get_backend_generation_doc(
    project_root: Path | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Return the backend code-generation guide.

    Reads ``zeeb_agents/agent_docs/backend-generation.md``.
    Covers models, migrations, serializers, viewsets, routes, permissions,
    signals, tasks, and seed data.

    Maps to MCP resource ``mcp://docs/backend-generation``.

    Args:
        project_id: When provided, current app list is appended.
        tool_prefix: String prepended to every ``{prefix}`` placeholder in the
            markdown.  Defaults to ``""`` (no prefix).

    Returns data (on success):
        content (str): the guide as markdown, with ``{prefix}`` placeholders
            replaced by *tool_prefix*.
        uri (str): ``"mcp://docs/backend-generation"``.
        mime_type (str): ``"text/markdown"``.
    """
    framework = framework or detect_framework(project_root)
    content = await asyncio.to_thread(_read_doc, "backend-generation", framework)
    content = content.replace("{prefix}", tool_prefix)
    if project_root is not None:
        content += await _build_project_context(project_root)
    return AgentResult(
        success=True,
        message="Backend generation documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["backend-generation"], "mime_type": _MIME},
    )


@agent_function(optional_project=True)
async def get_frontend_generation_doc(
    project_root: Path | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Return the frontend integration guide.

    Reads ``zeeb_agents/agent_docs/frontend-generation.md``.
    Covers CORS, JWT auth, OpenAPI export, JSON Schema, route inventory,
    health endpoints, and WebSocket notes.

    Maps to MCP resource ``mcp://docs/frontend-generation``.

    Args:
        project_id: When provided, current CORS settings and route
            inventory are appended.
        tool_prefix: String prepended to every ``{prefix}`` placeholder in the
            markdown.  Defaults to ``""`` (no prefix).

    Returns data (on success):
        content (str): the guide as markdown, with ``{prefix}`` placeholders
            replaced by *tool_prefix*.
        uri (str): ``"mcp://docs/frontend-generation"``.
        mime_type (str): ``"text/markdown"``.
    """
    framework = framework or detect_framework(project_root)
    content = await asyncio.to_thread(_read_doc, "frontend-generation", framework)
    content = content.replace("{prefix}", tool_prefix)
    if project_root is not None:
        extra_lines: list[str] = ["\n---\n\n## Current Project State\n"]
        try:
            from zeeb_agents.cors import get_cors_config
            cors = await get_cors_config(project_root)
            if cors.success and cors.data and cors.data.get("cors"):
                origins = cors.data["cors"].get("CORS_ALLOW_ORIGINS", [])
                extra_lines.append(f"**CORS origins configured:** {origins}\n\n")
            else:
                extra_lines.append("**CORS:** not yet configured — call `configure_cors()`.\n\n")
        except Exception:
            pass
        try:
            from zeeb_agents.schema import list_all_routes
            routes = await list_all_routes(project_root)
            if routes.success and routes.data:
                count = routes.data.get("count", 0)
                extra_lines.append(f"**Routes registered:** {count}\n\n")
        except Exception:
            pass
        content += "".join(extra_lines) if len(extra_lines) > 1 else ""
    return AgentResult(
        success=True,
        message="Frontend integration documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["frontend-generation"], "mime_type": _MIME},
    )


@agent_function(optional_project=True)
async def get_deployment_doc(
    project_root: Path | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Return the deployment guide.

    Reads ``zeeb_agents/agent_docs/deployment.md``.
    Covers production settings, Dockerfile generation, requirements.txt,
    health endpoints, environment variables, and recommended stack.

    Maps to MCP resource ``mcp://docs/deployment``.

    Args:
        project_id: When provided, the current production readiness check
            result is appended.
        tool_prefix: String prepended to every ``{prefix}`` placeholder in the
            markdown.  Defaults to ``""`` (no prefix).

    Returns data (on success):
        content (str): the guide as markdown, with ``{prefix}`` placeholders
            replaced by *tool_prefix*.
        uri (str): ``"mcp://docs/deployment"``.
        mime_type (str): ``"text/markdown"``.
    """
    framework = framework or detect_framework(project_root)
    content = await asyncio.to_thread(_read_doc, "deployment", framework)
    content = content.replace("{prefix}", tool_prefix)
    if project_root is not None:
        content += await _build_deployment_context(project_root)
    return AgentResult(
        success=True,
        message="Deployment documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["deployment"], "mime_type": _MIME},
    )


@agent_function(optional_project=True)
async def get_recipes_doc(
    project_root: Path | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Return the copy-paste task recipes.

    Reads ``zeeb_agents/agent_docs/<framework>/recipes.md``.
    End-to-end recipes (CRUD resource, custom action, standalone route, auth,
    seed, preview) using the exact, current tool calls.

    Maps to MCP resource ``mcp://docs/recipes``.

    Args:
        project_id: When provided, a live project context section is appended.
        tool_prefix: String prepended to every ``{prefix}`` placeholder.
        framework: Doc set to serve; auto-detected from the project otherwise.

    Returns data (on success):
        content (str): the recipes as markdown, ``{prefix}`` substituted.
        uri (str): ``"mcp://docs/recipes"``.
        mime_type (str): ``"text/markdown"``.
    """
    framework = framework or detect_framework(project_root)
    content = await asyncio.to_thread(_read_doc, "recipes", framework)
    content = content.replace("{prefix}", tool_prefix)
    if project_root is not None:
        content += await _build_project_context(project_root)
    return AgentResult(
        success=True,
        message="Recipes documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["recipes"], "mime_type": _MIME},
    )


@agent_function(optional_project=True)
async def get_error_recovery_doc(
    project_root: Path | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Return the error-recovery playbook.

    Reads ``zeeb_agents/agent_docs/<framework>/error-recovery.md``.
    A table keyed by every ``error_code`` → what it means → the corrective call.

    Maps to MCP resource ``mcp://docs/error-recovery``.

    Args:
        project_id: Accepted for uniformity; no live context is appended.
        tool_prefix: String prepended to every ``{prefix}`` placeholder.
        framework: Doc set to serve; auto-detected from the project otherwise.

    Returns data (on success):
        content (str): the playbook as markdown, ``{prefix}`` substituted.
        uri (str): ``"mcp://docs/error-recovery"``.
        mime_type (str): ``"text/markdown"``.
    """
    framework = framework or detect_framework(project_root)
    content = await asyncio.to_thread(_read_doc, "error-recovery", framework)
    content = content.replace("{prefix}", tool_prefix)
    return AgentResult(
        success=True,
        message="Error-recovery documentation ready.",
        data={"content": content, "uri": RESOURCE_URIS["error-recovery"], "mime_type": _MIME},
    )


# ---------------------------------------------------------------------------
# URI dispatcher
# ---------------------------------------------------------------------------

_URI_MAP = {
    RESOURCE_URIS["principles"]:          get_principles_doc,
    RESOURCE_URIS["capabilities"]:        get_capabilities_doc,
    RESOURCE_URIS["project-lifecycle"]:   get_project_lifecycle_doc,
    RESOURCE_URIS["backend-generation"]:  get_backend_generation_doc,
    RESOURCE_URIS["frontend-generation"]: get_frontend_generation_doc,
    RESOURCE_URIS["deployment"]:          get_deployment_doc,
    RESOURCE_URIS["recipes"]:             get_recipes_doc,
    RESOURCE_URIS["error-recovery"]:      get_error_recovery_doc,
}


@agent_function(resolve_project=False)
async def get_resource(
    uri: str,
    project_id: str | None = None,
    tool_prefix: str = "",
    framework: str | None = None,
) -> AgentResult:
    """Fetch MCP resource content by URI.

    Dispatches to the appropriate documentation function based on *uri*.
    The markdown source is read from ``zeeb_agents/agent_docs/<name>.md``.

    Supported URIs:

    - ``mcp://docs/principles``
    - ``mcp://docs/capabilities``
    - ``mcp://docs/project-lifecycle``
    - ``mcp://docs/backend-generation``
    - ``mcp://docs/frontend-generation``
    - ``mcp://docs/deployment``

    Args:
        uri: The ``mcp://`` resource URI.
        project_id: Passed through to the underlying documentation function.
            When supplied, live project context is appended to the static docs.
        tool_prefix: String prepended to every ``{prefix}`` placeholder in the
            returned markdown, matching the tool name prefix your MCP server
            uses.  For example ``"zeeb_"`` turns ``{prefix}create_model`` into
            ``zeeb_create_model`` so agents see the correct tool names.
            Defaults to ``""`` (no prefix — bare function names).

    Returns data (on success):
        content (str): the resolved markdown document.
        uri (str): the resource URI that was fetched.
        mime_type (str): ``"text/markdown"``.

    Notes:
        - An unknown *uri* fails (``success=False``, ``data=None``) with the
          list of available URIs in ``message``.

    Example::

        result = await get_resource(
            "mcp://docs/capabilities",
            tool_prefix="zeeb_",
        )
        print(result.data["content"])
    """
    fn = _URI_MAP.get(uri)
    if fn is None:
        return AgentResult(
            success=False,
            message=(
                f"Unknown resource URI '{uri}'. "
                f"Available: {', '.join(sorted(_URI_MAP))}."
            ),
        )
    return await fn(project_id, tool_prefix, framework)
