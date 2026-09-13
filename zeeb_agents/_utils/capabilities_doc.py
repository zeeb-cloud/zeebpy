"""Generate the ``capabilities.md`` tool-inventory tables from live code.

``capabilities.md`` keeps hand-written prose, but its per-category tool tables
are generated from :func:`zeeb_agents.list_capabilities` so signatures can never
drift.  The generated span in the markdown file is delimited by::

    <!-- BEGIN AUTO-GENERATED INVENTORY -->
    ...
    <!-- END AUTO-GENERATED INVENTORY -->

Regenerate after changing any tool signature::

    python -m zeeb_agents._utils.capabilities_doc --write

A test (``test_capabilities_md_inventory_is_generated``) asserts the committed
span equals a fresh render, so a stale table fails CI.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

BEGIN = "<!-- BEGIN AUTO-GENERATED INVENTORY -->"
END = "<!-- END AUTO-GENERATED INVENTORY -->"

# ``project_id`` is on every project-operating tool; documented once above the
# tables and omitted from each row for readability.
_HIDDEN_PARAM = "project_id"

_DOCS_FILE = (
    Path(__file__).resolve().parent.parent / "agent_docs" / "zeebpy" / "capabilities.md"
)


def _fmt_default(default_repr: str) -> str:
    """Render a param default readably (single-quoted str reprs → double)."""
    if len(default_repr) >= 2 and default_repr[0] == "'" and default_repr[-1] == "'":
        return '"' + default_repr[1:-1] + '"'
    return default_repr


def _clean_signature(params: list[dict]) -> str:
    """Build a clean, annotation-free ``(a, b=None)`` signature, hiding project_id."""
    parts: list[str] = []
    for p in params:
        if p["name"] == _HIDDEN_PARAM:
            continue
        if p["required"]:
            parts.append(p["name"])
        else:
            parts.append(f"{p['name']}={_fmt_default(p['default'])}")
    return f"({', '.join(parts)})"


def _escape_cell(text: str) -> str:
    """Escape a markdown table cell (pipes only; text is a one-line summary)."""
    return text.replace("|", "\\|").strip()


def _table(entries: list[dict], prefix: str) -> list[str]:
    """Render one markdown table body for *entries*, sorted by tool name."""
    lines = ["| Tool | Description |", "|---|---|"]
    for t in sorted(entries, key=lambda e: e["name"]):
        call = f"`{prefix}{t['name']}{_clean_signature(t['params'])}`"
        lines.append(f"| {call} | {_escape_cell(t['summary'])} |")
    return lines


async def _render_async(prefix: str = "") -> str:
    from zeeb_agents import list_capabilities
    from zeeb_agents.capabilities import CATEGORY_ORDER

    result = await list_capabilities()
    tools = result.data["tools"]
    blocks: list[str] = []

    # The tiers lead: an agent reading top-down meets the ~20 tools that do
    # almost everything before the long per-object inventory.
    core = [t for t in tools if t["tier"] == "core"]
    hatches = [t for t in tools if t["tier"] == "escape_hatch"]
    advanced = [t for t in tools if t["tier"] == "advanced"]

    blocks.append(
        "\n".join(
            [
                f"### Core ({len(core)})",
                "",
                "Start here. These are declarative and cover almost every task.",
                "",
                *_table(core, prefix),
            ]
        )
    )
    blocks.append(
        "\n".join(
            [
                f"### Escape hatches ({len(hatches)})",
                "",
                "Precise, general-purpose control for what a feature spec cannot",
                "express. Reaching for these first usually means the wrong problem",
                "is being solved.",
                "",
                *_table(hatches, prefix),
            ]
        )
    )
    blocks.append(
        "\n".join(
            [
                f"### Advanced ({len(advanced)})",
                "",
                "One tool per object. Fully supported — the feature compiler runs",
                "exactly these — but reach for them only when a spec cannot express",
                "the change. Grouped by category below.",
            ]
        )
    )

    by_cat: dict[str, list[dict]] = {}
    for t in advanced:
        by_cat.setdefault(t["category"], []).append(t)
    ordered = CATEGORY_ORDER + [c for c in by_cat if c not in CATEGORY_ORDER]
    for cat in ordered:
        entries = by_cat.get(cat)
        if not entries:
            continue
        blocks.append("\n".join([f"#### {cat}", "", *_table(entries, prefix)]))
    return "\n\n".join(blocks)


def render_inventory(prefix: str = "") -> str:
    """Return the generated inventory markdown (all categories, `{prefix}` applied)."""
    return asyncio.run(_render_async(prefix))


def splice(markdown: str, inventory: str) -> str:
    """Return *markdown* with the region between the markers replaced by *inventory*."""
    pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.DOTALL)
    replacement = f"{BEGIN}\n\n{inventory}\n\n{END}"
    if not pattern.search(markdown):
        raise ValueError("capabilities.md is missing the AUTO-GENERATED INVENTORY markers")
    return pattern.sub(lambda _m: replacement, markdown, count=1)


def regenerate(path: Path = _DOCS_FILE, *, write: bool = False) -> str:
    """Splice a fresh inventory into *path*; write it back when *write* is true.

    Returns the full new file contents (whether or not it was written).
    """
    current = path.read_text(encoding="utf-8")
    # The committed doc carries `{prefix}` placeholders, substituted at serve
    # time by get_capabilities_doc(tool_prefix=...).
    updated = splice(current, render_inventory(prefix="{prefix}"))
    if write and updated != current:
        path.write_text(updated, encoding="utf-8")
    return updated


if __name__ == "__main__":
    import sys

    write = "--write" in sys.argv[1:]
    regenerate(write=write)
    print("capabilities.md " + ("regenerated." if write else "checked (no --write)."))
