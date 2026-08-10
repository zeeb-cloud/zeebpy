"""Every key an agent tool puts in ``data`` must appear in its ``Returns data:`` block.

The ``data`` keys are the MCP-facing contract, and
``list_capabilities(include_docstrings=True)`` publishes the return schema by
parsing exactly that docstring block. So a key added to ``data`` without a
matching docstring entry does not just go undocumented — it makes the published
schema wrong.

That is how ``affected``, ``verified``, ``operations`` and ``base_class`` all
slipped in: the agent-facing prose in ``agent_docs/`` described them, but the
docstrings the schema is generated from did not.

Scope: this walks each tool's own body plus the private helpers it calls in the
same module. Keys injected by a helper in *another* module are not caught.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re

import pytest

import zeeb_agents
from zeeb_agents.capabilities import _returns

#: Local names a tool builds its result payload in.
_DATA_NAMES = frozenset({"data", "payload"})

#: Keys supplied by the shared failure / partial-failure envelope. They are
#: documented once, centrally, in agent_docs/zeebpy/error-recovery.md rather
#: than repeated in all 111 docstrings.
_ENVELOPE_KEYS = frozenset(
    {
        "error_code",
        "error_type",
        "recoverable",
        "state_changed",
        "suggestions",
        "problems",
        "next_actions",
        "steps_completed",
        "failed_operations",
        "completed_count",
        "total_count",
        "remaining_operations",
    }
)

#: A Returns entry written as ``<columns> (Any): ...`` — a deliberate
#: placeholder for a key set that is data-dependent (the columns of a row, say)
#: and cannot be enumerated in the docstring.
_PLACEHOLDER = re.compile(r"^\s*<\w+>\s*\([^)]*\):", re.M)


class _DataKeys(ast.NodeVisitor):
    """Collect literal string keys written into a ``data`` payload."""

    def __init__(self) -> None:
        self.keys: set[str] = set()
        self.helpers: set[str] = set()

    def _from_dict(self, node: ast.AST) -> None:
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.keys.add(key.value)

    def _target(self, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name) and target.id in _DATA_NAMES:
            self._from_dict(value)
        elif (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id in _DATA_NAMES
            and isinstance(target.slice, ast.Constant)
            and isinstance(target.slice.value, str)
        ):
            self.keys.add(target.slice.value)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._target(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # ``data: dict = {...}`` — how create_viewset builds its payload.
        if node.value is not None:
            self._target(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg == "data":
                self._from_dict(keyword.value)

        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in {"update", "setdefault"}
            and isinstance(func.value, ast.Name)
            and func.value.id in _DATA_NAMES
        ):
            if func.attr == "setdefault" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    self.keys.add(first.value)
            for arg in node.args:
                self._from_dict(arg)
        elif isinstance(func, ast.Name) and func.id.startswith("_"):
            self.helpers.add(func.id)

        self.generic_visit(node)


def _module_functions(module_name: str) -> dict[str, _DataKeys]:
    tree = ast.parse(inspect.getsource(importlib.import_module(module_name)))
    found: dict[str, _DataKeys] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            visitor = _DataKeys()
            visitor.visit(node)
            found[node.name] = visitor
    return found


def _tools() -> list[tuple[str, object]]:
    out = []
    for name in zeeb_agents.__all__:
        func = getattr(zeeb_agents, name, None)
        if callable(func) and not isinstance(func, type):
            out.append((name, inspect.unwrap(func)))
    return out


def _produced_keys(name: str, func: object) -> set[str]:
    """Keys the tool writes, following private helpers in its own module."""
    functions = _module_functions(func.__module__)
    if name not in functions:
        return set()

    keys = set(functions[name].keys)
    pending = list(functions[name].helpers)
    seen: set[str] = set()
    while pending:
        helper = pending.pop()
        if helper in seen or helper not in functions:
            continue
        seen.add(helper)
        keys |= functions[helper].keys
        pending.extend(functions[helper].helpers)
    return keys


@pytest.mark.parametrize("name,func", _tools(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_data_key_is_documented(name, func):
    doc = func.__doc__ or ""
    if _PLACEHOLDER.search(doc):
        pytest.skip("Returns block documents a data-dependent key set")

    documented = {entry["key"] for entry in _returns(doc)}
    allowed = documented | _ENVELOPE_KEYS

    missing = sorted(_produced_keys(name, func) - allowed)

    assert not missing, (
        f"{func.__module__}.{name}() puts keys in data that its 'Returns data:' "
        f"block does not document: {', '.join(missing)}. "
        "list_capabilities(include_docstrings=True) publishes that block as the "
        "MCP return schema, so an undocumented key makes the schema wrong."
    )
