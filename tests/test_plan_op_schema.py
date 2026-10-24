"""A plan operation means the same thing to everything that touches it.

An op is a plain dict. The compiler writes it; the executor, the feature
manifest and the archive read it. With no schema between them a reader could
ask for a key the compiler never writes and get ``None`` without an error —
the manifest read ``op.get("url_prefix")`` while the compiler wrote
``"prefix"``, and recorded every route prefix as ``None``.

``_OP_REQUIRED`` + ``_OP_OPTIONAL`` is the schema. These tests read every
module that dispatches on an op's kind and check each key it reads from the op
is in that kind's schema, and that a compiled feature writes nothing else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from zeeb_agents.feature_spec import (
    _OP_OPTIONAL,
    _OP_REQUIRED,
    KNOWN_OPS,
    compile_feature_spec,
)

READERS = (
    "zeeb_agents/feature_spec.py",
    "zeeb_agents/feature_manifest.py",
    "zeeb_agents/feature_archive.py",
    "zeeb_agents/intent.py",
)
REPO = Path(__file__).resolve().parent.parent


def _schema(kind: str) -> set[str]:
    return {"op", *_OP_REQUIRED.get(kind, ()), *_OP_OPTIONAL.get(kind, ())}


class _KindBranches(ast.NodeVisitor):
    """``(kinds, key, line)`` for each ``op[...]``/``op.get(...)`` in an ``if kind ...`` branch."""

    def __init__(self) -> None:
        self.reads: list[tuple[tuple[str, ...], str, int]] = []

    def visit_If(self, node: ast.If) -> None:
        kinds = self._kinds(node.test)
        if not kinds:
            self.generic_visit(node)
            return
        for stmt in node.body:
            for sub in ast.walk(stmt):
                key = self._key(sub)
                if key is not None:
                    self.reads.append((kinds, key, sub.lineno))
        for stmt in node.orelse:
            self.visit(stmt)

    def _kinds(self, test: ast.expr) -> tuple[str, ...]:
        if isinstance(test, ast.BoolOp):
            return tuple(k for value in test.values for k in self._kinds(value))
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "kind"
        ):
            target = test.comparators[0]
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                return (target.value,)
            if isinstance(target, (ast.Tuple, ast.Set, ast.List)):
                return tuple(
                    e.value for e in target.elts if isinstance(e, ast.Constant)
                )
        return ()

    @staticmethod
    def _key(node: ast.AST) -> str | None:
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "op"
            and isinstance(node.slice, ast.Constant)
        ):
            return node.slice.value
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            return node.args[0].value
        return None


def test_the_schema_covers_every_op_and_nothing_else():
    assert set(_OP_OPTIONAL) <= set(KNOWN_OPS)
    for kind, optional in _OP_OPTIONAL.items():
        assert not set(optional) & set(_OP_REQUIRED[kind]), kind


@pytest.mark.parametrize("module", READERS)
def test_readers_only_read_keys_in_the_schema(module):
    visitor = _KindBranches()
    visitor.visit(ast.parse((REPO / module).read_text(encoding="utf-8")))
    unknown = [
        f"{module}:{line} reads op[{key!r}] for {'/'.join(kinds)}"
        for kinds, key, line in visitor.reads
        if all(kind in KNOWN_OPS for kind in kinds)
        and not any(key in _schema(kind) for kind in kinds)
    ]
    assert not unknown, "\n".join(unknown)


def test_the_compiler_writes_only_keys_in_the_schema():
    spec = {
        "name": "shop",
        "app": "shop",
        "entities": [
            {"name": "Category", "fields": [{"name": "name", "type": "string"}]},
            {
                "name": "Order",
                "fields": [
                    {"name": "category", "type": "fk", "to": "Category"},
                    {"name": "tags", "type": "m2m", "to": "Category"},
                ],
                "workflow": {
                    "field": "status",
                    "states": ["new", "paid"],
                    "initial": "new",
                    "transitions": [{"name": "pay", "from": "new", "to": "paid"}],
                },
                "functions": [{"name": "summary", "kind": "action", "body": "return {}"}],
            },
        ],
        "functions": [
            {"name": "ping", "kind": "endpoint", "path": "/ping", "method": "GET",
             "body": "return {}"},
            {"name": "on_order", "kind": "hook", "entity": "Order", "trigger": "post_save",
             "body": "pass"},
            {"name": "nightly", "kind": "task", "body": "pass"},
            {"name": "IsOwner", "kind": "rule", "logic": "owner_only"},
        ],
    }
    plan = compile_feature_spec(spec, [], [])
    kinds = {op["op"] for op in plan["operations"]}
    assert {"create_model", "register_route", "add_viewset_action", "create_route"} <= kinds
    unknown = [
        f"{op['op']}: {sorted(set(op) - _schema(op['op']))}"
        for op in plan["operations"]
        if set(op) - _schema(op["op"])
    ]
    assert not unknown, unknown
