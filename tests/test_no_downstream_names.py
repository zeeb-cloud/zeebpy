"""Guard: zeebpy's public surface never names a product built on it.

zeebpy is an open-source framework. Hosted products consume it (they may
install ``main`` directly), but the docs, the agent-served markdown, the
messages the library returns, the code it generates and the guides in this
repository describe zeebpy on its own terms. A downstream product is "a host"
or "another tool", never named, and zeebpy is not described as one.

The forbidden names are assembled at runtime so this file does not trip its
own check.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_FORBIDDEN = re.compile(
    "|".join(
        [
            "zeeb" + r"[-_ ]?" + "mcp",
            "zeeb" + "_codegen",
            "libs/" + "zeeb",
            # zeebpy is a framework, not a hosted backend service, and a
            # runtime someone else manages is "a host"/"a hosted preview".
            r"\bba" + r"as\b",
            "platform" + "-managed",
            "platform" + "-published",
        ]
    ),
    re.IGNORECASE,
)

#: Everything that ships or is published with the repository.
_SCANNED_DIRS = (
    ".agents",
    ".github",
    "demo_blog",
    "docs",
    "tests",
    "zeeb_agents",
    "zeeb_api",
    "zeeb_orm",
    "zeebpy-course",
)
_TEXT_SUFFIXES = {
    ".cfg", ".html", ".ini", ".json", ".md", ".mdc", ".py", ".toml", ".txt", ".yaml", ".yml",
}


def _files() -> list[Path]:
    files = [p for p in _REPO_ROOT.iterdir() if p.is_file() and p.suffix in _TEXT_SUFFIXES]
    for name in _SCANNED_DIRS:
        base = _REPO_ROOT / name
        if base.is_dir():
            files += [
                p
                for p in base.rglob("*")
                if p.is_file() and p.suffix in _TEXT_SUFFIXES and "__pycache__" not in p.parts
            ]
    return sorted(files)


def test_the_scan_covers_the_public_surface():
    names = {p.relative_to(_REPO_ROOT).as_posix() for p in _files()}
    assert {"README.md", "AGENTS.md", "docs/cli/agents.md"} <= names
    assert "zeeb_agents/feature_manifest.py" in names


def test_no_downstream_product_is_named():
    problems = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _FORBIDDEN.finditer(text):
            line = text[: match.start()].count("\n") + 1
            problems.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{line}: {match.group()}")

    assert not problems, (
        "a downstream product is named in zeebpy's public surface — describe it as "
        "'a host' or 'another tool':\n  " + "\n  ".join(problems)
    )
