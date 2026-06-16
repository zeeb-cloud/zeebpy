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
from pathlib import Path

from zeeb_agents._utils import AgentResult, agent_function

# Names exported from the package that are not callable agent functions.
_NON_FUNCTION_EXPORTS = frozenset({"AgentResult", "RESOURCE_URIS"})


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


@agent_function(resolve_project_root=False)
async def list_capabilities(
    include_docstrings: bool = False,
    module: str | None = None,
    project_root: Path | None = None,
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
        project_root: Unused; accepted for signature uniformity.  Not resolved.

    Returns data (on success):
        tools (list[dict]): one entry per tool, each with::

            name (str)        function name, e.g. "create_model"
            module (str)      short module name, e.g. "models"
            signature (str)   call signature without project_root, e.g.
                              "(app, model_name, fields, meta=None)"
            summary (str)     first line of the docstring
            doc (str)         full docstring — only when include_docstrings=True

        count (int): number of tools returned
        modules (list[str]): sorted unique module names present in ``tools``

    Notes:
        - ``project_root`` is hidden from each reported ``signature`` because
          it is auto-resolved by the ``@agent_function`` decorator and callers
          normally omit it.
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
            params = [
                str(p)
                for p_name, p in sig.parameters.items()
                if p_name != "project_root"
            ]
            signature = f"({', '.join(params)})"
        except (TypeError, ValueError):
            signature = "(...)"

        doc = inspect.getdoc(func)
        entry: dict = {
            "name": name,
            "module": mod,
            "signature": signature,
            "summary": _summary(doc),
        }
        if include_docstrings:
            entry["doc"] = doc or ""
        tools.append(entry)

    tools.sort(key=lambda t: (t["module"], t["name"]))
    modules = sorted({t["module"] for t in tools})

    return AgentResult(
        success=True,
        message=f"{len(tools)} agent tool(s) available across {len(modules)} module(s).",
        data={"tools": tools, "count": len(tools), "modules": modules},
    )
