"""The zeeb_agents public surface only ever grows.

Downstream projects install zeebpy from ``main``, so a push there is a release,
and an MCP host calls these functions by name, by position and by the ``data``
keys they document. The drift tests compare the code with its own docs — both
of which one change can edit together. This compares the surface with a golden
file, ``tests/contract/zeeb_agents.json``, that only additive changes may move:

- every exported name stays exported;
- a function's parameters keep their names and order, a new one is appended
  after them and has a default, and a parameter with a default keeps it;
- every ``data`` key in a ``Returns data:`` block stays documented.

After an additive change, refresh the file::

    ZEEB_UPDATE_CONTRACT=1 pytest tests/test_agents_contract_snapshot.py

The refresh refuses to write while any rule above is broken.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import zeeb_agents
from zeeb_agents.capabilities import _returns

GOLDEN = Path(__file__).parent / "contract" / "zeeb_agents.json"


def _surface() -> dict[str, dict]:
    surface: dict[str, dict] = {}
    for name in sorted(zeeb_agents.__all__):
        obj = getattr(zeeb_agents, name)
        entry: dict = {}
        if inspect.isfunction(obj) or inspect.iscoroutinefunction(obj):
            params = inspect.signature(obj).parameters.values()
            entry["params"] = [
                [p.name, p.default is not inspect.Parameter.empty] for p in params
            ]
            entry["data_keys"] = sorted({r["key"] for r in _returns(inspect.getdoc(obj))})
        surface[name] = entry
    return surface


def _violations(golden: dict[str, dict], current: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    for name, before in golden.items():
        now = current.get(name)
        if now is None:
            problems.append(f"{name}: removed from zeeb_agents.__all__")
            continue
        if "params" in before:
            if "params" not in now:
                problems.append(f"{name}: no longer a function")
                continue
            old, new = before["params"], now["params"]
            if [p for p, _ in new[: len(old)]] != [p for p, _ in old]:
                problems.append(
                    f"{name}: parameters {[p for p, _ in old]} became "
                    f"{[p for p, _ in new[: len(old)]]} — never rename, remove or reorder"
                )
            else:
                for (param, had_default), (_, has_default) in zip(old, new):
                    if had_default and not has_default:
                        problems.append(f"{name}: parameter '{param}' lost its default")
            for param, has_default in new[len(old):]:
                if not has_default:
                    problems.append(f"{name}: new parameter '{param}' must have a default")
            gone = sorted(set(before.get("data_keys", [])) - set(now.get("data_keys", [])))
            if gone:
                problems.append(f"{name}: documented data keys removed: {gone}")
    return problems


def test_the_public_surface_only_grows():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    current = _surface()
    problems = _violations(golden, current)
    assert not problems, "zeeb_agents contract broken:\n  " + "\n  ".join(problems)

    if os.environ.get("ZEEB_UPDATE_CONTRACT"):
        GOLDEN.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    else:
        added = sorted(set(current) - set(golden))
        assert not added, (
            f"new exports {added} are not in {GOLDEN.name}; refresh it with "
            "ZEEB_UPDATE_CONTRACT=1 pytest tests/test_agents_contract_snapshot.py"
        )


def test_the_rules_catch_what_they_promise():
    def fn(params, keys=("x", "y")):
        return {"f": {"params": params, "data_keys": list(keys)}}

    golden = fn([["a", False], ["b", True]])
    assert _violations(golden, fn([["a", False], ["b", True], ["c", True]], "xyz")) == []
    assert _violations(golden, {}) == ["f: removed from zeeb_agents.__all__"]
    assert _violations(golden, fn([["b", True], ["a", False]]))  # reordered
    assert _violations(golden, fn([["a", False], ["b", False]]))  # default dropped
    assert _violations(golden, fn([["a", False], ["b", True], ["c", False]]))  # required added
    assert _violations(golden, fn([["a", False], ["b", True]], "x"))  # data key removed
