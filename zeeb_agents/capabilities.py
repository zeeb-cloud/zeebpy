"""Machine-readable discovery for the ``zeeb_agents`` tool surface.

:func:`list_capabilities` introspects every public agent function exported
from :mod:`zeeb_agents` and returns a structured inventory: name, module,
signature, one-line summary, and (optionally) the full docstring.

Because it is built purely from ``inspect`` against ``zeeb_agents.__all__``,
the inventory can never drift out of sync with the code — there is no
hand-maintained registry.  An MCP server can register this as a tool so a
coding agent can discover what is available, with what arguments, and what
each call returns (via the docstring's ``Returns data:`` block).
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function

# Names exported from the package that are not callable agent tools (result
# type, URI registry, and the vendor-configuration hooks).
_NON_FUNCTION_EXPORTS = frozenset(
    {"AgentResult", "RESOURCE_URIS", "configure", "set_project_resolver"}
)

# Human-facing category per source module.  One source of truth, shared with the
# auto-generated capabilities.md inventory (``_utils/capabilities_doc.py``).
CATEGORY_BY_MODULE: dict[str, str] = {
    "intent": "Intent Workflows",
    "project": "Project & App Management",
    "models": "Model Management",
    "serializers": "Serializers",
    "viewsets": "ViewSets & Routing",
    "routes": "ViewSets & Routing",
    "migrations": "Migrations",
    "runtime": "Platform Runtime",
    "logs": "Logs",
    "config": "Config & Environment",
    "files": "File System",
    "database": "Database Introspection",
    "testing": "Testing",
    "shell": "Shell / Management",
    "seed": "Seed Data",
    "signals": "ORM Signals",
    "users": "Users (BaaS)",
    "cors": "CORS (BaaS)",
    "tasks": "Background Tasks (BaaS)",
    "health": "Health (BaaS)",
    "schema": "Schema & Routes (BaaS)",
    "deploy": "Deployment (BaaS)",
    "permissions_scaffold": "Permissions (BaaS)",
    "auth_scaffold": "Auth Scaffolding",
    "filters_scaffold": "FilterSets",
    "api_config": "API Configuration",
    "capabilities": "Discovery",
    "resources": "Docs & Resources",
}

# Order categories appear in the generated inventory.
CATEGORY_ORDER: list[str] = list(dict.fromkeys(CATEGORY_BY_MODULE.values()))


def category_for(module: str) -> str:
    """Return the human category for a short *module* name."""
    return CATEGORY_BY_MODULE.get(module, "Other")


def _summary(doc: str | None) -> str:
    """Return the first non-empty line of *doc* as a one-line summary."""
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _module_name(func: object) -> str:
    """Return the short module name (e.g. ``models``) a function lives in."""
    full = getattr(func, "__module__", "") or ""
    # "zeeb_agents.models" -> "models"; "zeeb_agents" -> "zeeb_agents"
    return full.rsplit(".", 1)[-1] if "." in full else full


def _params(sig: inspect.Signature) -> list[dict]:
    """Structured parameters: name, annotation, default, required."""
    out: list[dict] = []
    for p in sig.parameters.values():
        annotation = "" if p.annotation is inspect.Parameter.empty else str(p.annotation)
        has_default = p.default is not inspect.Parameter.empty
        out.append(
            {
                "name": p.name,
                "annotation": annotation,
                "default": None if not has_default else repr(p.default),
                "required": not has_default,
            }
        )
    return out


# A ``Returns data:`` entry line, e.g. ``    path (str): relative path.``
_RETURN_LINE = re.compile(r"^\s*([a-zA-Z_]\w*)\s*\(([^)]*)\):\s*(.*)$")


def _returns(doc: str | None) -> list[dict]:
    """Best-effort parse of the ``Returns data:`` block into ``{key, type, description}``.

    Scans from a ``Returns data`` heading to the next blank-line-separated
    section, collecting ``key (type): description`` entries (continuation lines
    are appended to the previous entry's description).
    """
    if not doc:
        return []
    lines = doc.splitlines()
    out: list[dict] = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if not in_block:
            if stripped.lower().startswith("returns data"):
                in_block = True
            continue
        # A new top-level docstring section ends the block.
        if stripped and not line[0].isspace():
            break
        if stripped.rstrip(":").lower() in {"notes", "example", "examples", "raises"}:
            break
        m = _RETURN_LINE.match(line)
        if m:
            out.append(
                {"key": m.group(1), "type": m.group(2).strip(), "description": m.group(3).strip()}
            )
        elif stripped and out:
            out[-1]["description"] = f"{out[-1]['description']} {stripped}".strip()
    return out


# The canonical zero-to-served build order. One source of truth, surfaced by
# get_started() and mirrored in agent_docs/project-lifecycle.md.
_BUILD_STEPS: list[dict] = [
    {
        "step": "create_project",
        "why": "Scaffold the project (bootstrapping call — project_id optional).",
    },
    {
        "step": "create_app",
        "why": "Scaffold an app AND wire it in (INSTALLED_APPS + project urls) so "
        "its endpoints are served and its models migrate. Auto-wires by default.",
    },
    {
        "step": "build_feature",
        "why": "Declarative one shot: a FeatureSpec (entities + relations + api) "
        "becomes models, serializers, endpoints, routes, and migrations — "
        "verified. plan_feature previews the same plan without writing. For a "
        "single model, generate_crud; for full control: create_model → "
        "create_serializer → create_viewset → register_route.",
    },
    {
        "step": "make_migrations",
        "why": "Write a migration for the new models (build_feature already did "
        "this unless migrate=false).",
    },
    {
        "step": "run_migrations",
        "why": "Apply the migration to the database.",
    },
    {
        "step": "verify_project",
        "why": "The acceptance gate: structure, migrations, and the live OpenAPI "
        "contract in one verdict. describe_project shows the raw snapshot; "
        "diagnose_problem investigates failures.",
    },
]


@agent_function(optional_project=True)
async def get_started(project_root: Path | None = None) -> AgentResult:
    """Return the canonical build recipe — the one call to make first.

    Turns discovery from "read three docs first" into a single entrypoint: the
    ordered zero-to-served sequence every project follows, plus pointers to the
    reference docs. When a ``project_id`` is given, it also inspects the current
    state (via :func:`~zeeb_agents.project.describe_project`) and returns the
    **next recommended action**, so you always know where you are.

    Args:
        project_id: Optional. When given, the response includes the project's
            current state and the recommended next action; when omitted, only
            the generic recipe is returned.

    Returns data (on success):
        steps (list[dict]): the ordered recipe, each ``{"step", "why"}``.
        docs (dict): resource URIs to read for depth — ``principles``,
            ``project_lifecycle``, ``backend_generation``, ``frontend_generation``,
            ``capabilities``.
        discover (str): how to enumerate every tool (``"list_capabilities()"``).
        next_action (str | None): the recommended next call, present only when a
            ``project_id`` was supplied and its state could be read.
        state (dict | None): the ``describe_project`` snapshot, when available.

    Notes:
        - Never fails on an unreadable/absent project — the recipe is always
          returned; ``next_action`` / ``state`` are simply ``None``.
    """
    data: dict = {
        "steps": _BUILD_STEPS,
        "docs": {
            "principles": "mcp://docs/principles",
            "project_lifecycle": "mcp://docs/project-lifecycle",
            "backend_generation": "mcp://docs/backend-generation",
            "frontend_generation": "mcp://docs/frontend-generation",
            "capabilities": "mcp://docs/capabilities",
        },
        "discover": "list_capabilities()",
        "next_action": None,
        "state": None,
    }

    if project_root is not None:
        from zeeb_agents.project import describe_project

        state_res = await describe_project(project_id=project_root)
        if state_res.success and state_res.data:
            state = state_res.data
            data["state"] = state
            data["next_action"] = _recommend_next_action(state)

    return AgentResult(
        success=True,
        message="Zeeb build recipe" + (
            f" — next: {data['next_action']}" if data["next_action"] else ""
        ),
        data=data,
    )


def _recommend_next_action(state: dict) -> str:
    """Derive the single most useful next call from a describe_project snapshot."""
    apps = state.get("apps", [])
    if not apps:
        return (
            "build_feature(spec) — scaffold your first feature (it creates the "
            "app too), or create_app('<name>') for an empty app."
        )
    for app in apps:
        if app["model_count"] and not app["installed"]:
            return f"install_app('{app['name']}') — it has models but is not registered."
    for ep in state.get("endpoints", []):
        if not ep.get("served"):
            return f"wire_app_urls('{ep['app']}') — its endpoints 404."
    if not state.get("models"):
        return (
            "build_feature(spec) — add your first resource "
            "(or generate_crud for a single model)."
        )
    migrations = state.get("migrations", {})
    if migrations.get("available") and migrations.get("pending_count"):
        return "run_migrations() — pending migrations."
    if not state.get("endpoints"):
        return "build_feature(spec) or generate_crud(...) — expose your models as an API."
    return "verify_project() — confirm the acceptance gate passes, then keep building."


@agent_function(resolve_project=False)
async def list_capabilities(
    include_docstrings: bool = False,
    module: str | None = None,
    project_id: str | None = None,
) -> AgentResult:
    """Return a machine-readable inventory of every ``zeeb_agents`` tool.

    Introspects all callable functions exported from ``zeeb_agents.__all__``
    so the result always matches the installed code.  Use this to discover
    which tools exist, their call signatures, and what they do before calling
    them.  For the exact shape of each tool's ``AgentResult.data``, read the
    ``Returns data:`` block in the per-tool ``doc`` (pass
    ``include_docstrings=True``).

    Args:
        include_docstrings: When ``True``, each tool entry also carries its
            full docstring under ``doc``.  Defaults to ``False`` (summary only)
            to keep the payload small.
        module: When set, only tools defined in this short module name are
            returned (e.g. ``"models"``, ``"migrations"``, ``"users"``).
        project_id: Unused; accepted for signature uniformity.  Not resolved.

    Returns data (on success):
        tools (list[dict]): one entry per tool, each with::

            name (str)        function name, e.g. "create_model"
            module (str)      short module name, e.g. "models"
            category (str)    human category, e.g. "Model Management"
            signature (str)   call signature, e.g.
                              "(app, model_name, fields, meta=None, project_id=None)"
            summary (str)     first line of the docstring
            params (list)     [{name, annotation, default, required}] per argument
            returns (list)    [{key, type, description}] parsed from "Returns data:"
            doc (str)         full docstring — only when include_docstrings=True

        count (int): number of tools returned
        modules (list[str]): sorted unique module names present in ``tools``
        categories (list[str]): human categories present, in display order

    Notes:
        - Tools that operate on a project take a trailing ``project_id`` — the
          opaque id assigned by the host — as the last argument.
        - Entries are sorted by ``(module, name)`` for stable output.
    """
    import zeeb_agents

    tools: list[dict] = []
    for name in zeeb_agents.__all__:
        if name in _NON_FUNCTION_EXPORTS:
            continue
        func = getattr(zeeb_agents, name, None)
        if not callable(func):
            continue

        mod = _module_name(func)
        if module is not None and mod != module:
            continue

        try:
            sig = inspect.signature(func)
            signature = f"({', '.join(str(p) for p in sig.parameters.values())})"
            params = _params(sig)
        except (TypeError, ValueError):
            signature = "(...)"
            params = []

        doc = inspect.getdoc(func)
        entry: dict = {
            "name": name,
            "module": mod,
            "category": category_for(mod),
            "signature": signature,
            "summary": _summary(doc),
            "params": params,
            "returns": _returns(doc),
        }
        if include_docstrings:
            entry["doc"] = doc or ""
        tools.append(entry)

    tools.sort(key=lambda t: (t["module"], t["name"]))
    modules = sorted({t["module"] for t in tools})
    categories = [c for c in CATEGORY_ORDER if any(t["category"] == c for t in tools)]

    return AgentResult(
        success=True,
        message=f"{len(tools)} agent tool(s) available across {len(modules)} module(s).",
        data={
            "tools": tools,
            "count": len(tools),
            "modules": modules,
            "categories": categories,
        },
    )
