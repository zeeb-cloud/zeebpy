"""The prose docs must describe the framework that exists.

Only three doc surfaces were machine-checked (``docs/cli/commands.md``,
``docs/configuration/settings.md``, and eight substrings of
``docs/orm/feature-reference.md``). Everything else drifted silently, and a
sweep found fifteen ``from zeeb_* import ...`` lines that raise ImportError,
five code blocks that are not valid Python, two dead relative links, and whole
subsystems that no page mentioned.

A reader who pastes a documented snippet should get working code — or at worst
a traceback, never a silently wrong result. These tests pin that.
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

import zeeb_agents
import zeeb_api
import zeeb_orm

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

#: Every markdown file that is part of the published documentation.
PAGES: list[Path] = sorted(DOCS.rglob("*.md")) + [ROOT / "README.md", ROOT / "CLAUDE.md"]

#: Names exported for internal/plumbing reasons that no user-facing page needs.
_UNDOCUMENTED_BY_DESIGN: frozenset[str] = frozenset(
    {
        # Not an API name.
        "__version__",
        # Backwards-compatible alias; ForeignKey is the documented spelling.
        "ForeignKeyField",
        # Relation-registry internals (see zeeb_orm/models/relations.py).
        "RelationInfo",
        "resolve_relation",
    }
)

# Unanchored on purpose: a fence nested in a blockquote ("> ```python") is still
# a code block a reader will copy, and anchoring to ^ silently skipped those.
_FENCE = re.compile(r"```python\n(.*?)```", re.S)
_IMPORT = re.compile(r"^\s*from\s+(zeeb_[\w.]+)\s+import\s+\(?([^\n)]*)\)?", re.M)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _pages() -> list[tuple[str, str]]:
    return [(str(p.relative_to(ROOT)), p.read_text()) for p in PAGES if p.exists()]


def _imported_names(clause: str) -> list[str]:
    """Split an import clause into the bare names it binds."""
    names = []
    for part in clause.split(","):
        name = part.split(" as ")[0].strip()
        if name and name != "(":
            names.append(name)
    return names


def test_every_documented_import_resolves():
    """A documented import that raises is worse than no example at all."""
    problems: list[str] = []

    for page, text in _pages():
        for match in _IMPORT.finditer(text):
            module, clause = match.group(1), match.group(2)
            line = text[: match.start()].count("\n") + 1
            try:
                imported = importlib.import_module(module)
            except ModuleNotFoundError as exc:
                # A missing optional extra (httpx for the oauth layer) is an
                # environment gap, not doc drift. A missing zeeb_* module is drift.
                if exc.name and not exc.name.startswith("zeeb_"):
                    continue
                problems.append(f"{page}:{line} no module {module}")
                continue
            except ImportError as exc:  # pragma: no cover - defensive
                problems.append(f"{page}:{line} {module} -> {exc}")
                continue

            for name in _imported_names(clause):
                if not hasattr(imported, name):
                    problems.append(f"{page}:{line} {module} has no {name!r}")

    assert not problems, "documented imports that do not resolve:\n  " + "\n  ".join(
        problems
    )


def test_every_python_block_is_valid_python():
    """A block fenced as ```python must parse; label fragments otherwise."""
    problems: list[str] = []

    for page, text in _pages():
        for match in _FENCE.finditer(text):
            line = text[: match.start()].count("\n") + 2
            try:
                ast.parse(match.group(1))
            except SyntaxError as exc:
                problems.append(f"{page}:{line} {exc.msg}")

    assert not problems, (
        "```python blocks that are not valid Python (use ```text, ```json, "
        "```http or ```pycon for non-Python content):\n  " + "\n  ".join(problems)
    )


def test_every_relative_link_resolves():
    problems: list[str] = []

    for page, text in _pages():
        base = (ROOT / page).parent
        for match in _LINK.finditer(text):
            target = match.group(2).split("#")[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (base / target).resolve().exists():
                line = text[: match.start()].count("\n") + 1
                problems.append(f"{page}:{line} -> {target}")

    assert not problems, "dead relative links:\n  " + "\n  ".join(problems)


@pytest.mark.parametrize(
    "package",
    [zeeb_orm, zeeb_api, zeeb_agents],
    ids=lambda p: p.__name__,
)
def test_every_public_export_is_mentioned_somewhere(package):
    """Shipping a public name nobody can find out about is the same as not shipping it.

    This is deliberately weak — a single mention anywhere under docs/ passes.
    It exists to catch whole subsystems landing with no page at all, which is
    how filters, pagination and the refresh-token store went undocumented.
    """
    prose = "".join(p.read_text() for p in sorted(DOCS.rglob("*.md")))

    missing = sorted(
        name
        for name in getattr(package, "__all__", [])
        if name not in _UNDOCUMENTED_BY_DESIGN
        and not re.search(rf"\b{re.escape(name)}\b", prose)
    )

    assert not missing, (
        f"{package.__name__} exports that appear nowhere in docs/ "
        f"({len(missing)}): " + ", ".join(missing)
    )
