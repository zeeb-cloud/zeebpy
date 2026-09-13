"""``docs/cli/agents.md`` must describe the agent layer that exists.

``docs/cli/commands.md`` has had a drift guard since the CLI page went stale;
the agent-function page had none, and drifted further. A sweep found twenty
tools missing from it — the entire intent layer (``plan_feature``,
``build_feature``, ``apply_plan``, ``change_feature``, ``verify_project``,
``diagnose_problem``, ``bootstrap_project``, ``configure_auth``) among them —
plus a dozen ``###`` headings whose signatures had lost parameters.

An agent that reads a page like that calls tools that do not exist, or omits
arguments that changed the result.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import zeeb_agents

DOC = Path(__file__).resolve().parent.parent / "docs" / "cli" / "agents.md"

#: ``### `name(a, b=1)` `` — a documented tool heading.
_HEADING = re.compile(r"^### `(\w+)\(([^`]*)\)`", re.M)

#: Headings under the reference sections that describe a concept, not a tool.
_NOT_TOOLS: frozenset[str] = frozenset()


def documented() -> dict[str, str]:
    """Map documented tool name -> its documented parameter list."""
    return {
        name: params
        for name, params in _HEADING.findall(DOC.read_text())
        if name not in _NOT_TOOLS
    }


def tools() -> dict[str, object]:
    """The public tools, keeping the *decorated* object.

    ``@agent_function`` rewrites ``project_root`` into the MCP-facing
    ``project_id``, so the wrapper — not ``inspect.unwrap()`` — carries the
    signature the docs are supposed to describe.
    """
    return {
        name: getattr(zeeb_agents, name)
        for name in zeeb_agents.__all__
        if callable(getattr(zeeb_agents, name, None))
        and not isinstance(getattr(zeeb_agents, name), type)
    }


def test_every_documented_tool_exists():
    unknown = set(documented()) - set(tools())
    assert not unknown, "docs/cli/agents.md documents tools that do not exist: " + ", ".join(
        sorted(unknown)
    )


def test_every_tool_is_documented():
    missing = set(tools()) - set(documented())
    assert not missing, (
        f"tools missing from docs/cli/agents.md ({len(missing)}): "
        + ", ".join(sorted(missing))
    )


@pytest.mark.parametrize("name", sorted(documented()))
def test_documented_signature_names_every_parameter(name):
    """A heading that omits a parameter hides a capability from the agent."""
    func = tools().get(name)
    if func is None:
        pytest.skip("covered by test_every_documented_tool_exists")

    real = {
        param
        for param, spec in inspect.signature(func).parameters.items()
        if spec.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }
    shown = set(re.findall(r"\b([a-z_]\w*)\s*(?:=|,|$)", documented()[name]))

    missing = sorted(real - shown)
    assert not missing, (
        f"docs/cli/agents.md heading for {name}() omits: {', '.join(missing)}"
    )


def test_the_page_states_the_real_tool_count():
    """The inventory line drifted to '80+' while the real count passed 110."""
    text = DOC.read_text()
    stale = re.findall(r"functions \((\d+)\+?\)", text)
    for claim in stale:
        assert int(claim) <= len(tools()), (
            f"docs/cli/agents.md claims {claim} functions but only "
            f"{len(tools())} exist"
        )
        assert int(claim) >= len(tools()) - 10, (
            f"docs/cli/agents.md claims {claim} functions; there are now "
            f"{len(tools())} — update the inventory line"
        )
