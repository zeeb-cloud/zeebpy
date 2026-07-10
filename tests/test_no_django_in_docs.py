"""Guard: no Django/DRF vocabulary anywhere a reader (human or agent) looks.

Coding agents (e.g. Lovable) that read "Django" in zeebpy documentation start
emitting real Django code into generated projects. These tests ban the
vocabulary from every agent-facing surface (agent_docs markdown, public
zeeb_agents docstrings served via ``list_capabilities(include_docstrings=True)``)
and from the human docs (docs/, README.md, zeebpy-course/). Describe zeebpy
features as zeebpy features — never by analogy to another framework.
"""

import inspect
import re
from pathlib import Path

import zeeb_agents

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN = re.compile(r"django|\bdrf\b|rest framework", re.IGNORECASE)


def _hits(text: str, source: str) -> list[str]:
    return [
        f"{source}: …{text[max(0, m.start() - 40) : m.end() + 40]!r}…"
        for m in _FORBIDDEN.finditer(text)
    ]


def test_agent_docs_are_django_free():
    docs_dir = Path(zeeb_agents.__file__).resolve().parent / "agent_docs"
    files = sorted(docs_dir.rglob("*.md"))
    assert files, "agent_docs tree not found"
    problems = [
        hit
        for md in files
        for hit in _hits(md.read_text(encoding="utf-8"), md.name)
    ]
    assert not problems, "Django/DRF wording in agent-served docs:\n" + "\n".join(problems)


def test_public_docstrings_are_django_free():
    """Docstrings of everything in ``zeeb_agents.__all__`` reach agents verbatim
    through ``list_capabilities(include_docstrings=True)``."""
    problems = []
    for name in zeeb_agents.__all__:
        doc = inspect.getdoc(getattr(zeeb_agents, name)) or ""
        problems += _hits(doc, f"zeeb_agents.{name}")
    assert not problems, "Django/DRF wording in public docstrings:\n" + "\n".join(problems)


def test_human_docs_are_django_free():
    targets = [
        *sorted((_REPO_ROOT / "docs").rglob("*.md")),
        _REPO_ROOT / "README.md",
        *sorted((_REPO_ROOT / "zeebpy-course").rglob("*.md")),
    ]
    assert len(targets) > 1, "docs tree not found"
    problems = [
        hit
        for md in targets
        if md.exists()
        for hit in _hits(md.read_text(encoding="utf-8"), str(md.relative_to(_REPO_ROOT)))
    ]
    assert not problems, "Django/DRF wording in human docs:\n" + "\n".join(problems)


def test_package_metadata_is_django_free():
    """`pip show zeebpy` (description/keywords) is documentation too."""
    import tomllib

    project = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    text = project.get("description", "") + " " + " ".join(project.get("keywords", []))
    problems = _hits(text, "pyproject.toml [project]")
    assert not problems, "Django/DRF wording in package metadata:\n" + "\n".join(problems)


def test_model_permissions_class_renamed():
    from zeeb_api import permissions

    assert "ModelPermissions" in permissions.__all__
    assert not hasattr(permissions, "DjangoModelPermissions")

    from zeeb_agents._utils.code_gen import VIEWSET_PERMISSIONS

    assert "ModelPermissions" in VIEWSET_PERMISSIONS
    assert "DjangoModelPermissions" not in VIEWSET_PERMISSIONS
