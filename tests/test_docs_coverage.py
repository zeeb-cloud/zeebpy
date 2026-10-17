"""Documentation coverage is 100%, and stays there.

``test_docs_examples_drift.py`` checks that the three top-level ``__all__``
lists are mentioned somewhere under ``docs/``. That missed every subpackage:
a sweep found the authentication backends, the OAuth helpers, ``SimpleRouter``,
the project-discovery API and ``prefetch_related_objects`` exported and nowhere
documented, and 32 public objects without a docstring.

Three measures, all at 100%:

- **reference** — every name in the ``__all__`` of every public module appears
  under ``docs/``; modules that are implementation detail are listed in
  :data:`INTERNAL_MODULES` with the reason, so excluding one is a decision.
- **docstrings** — every public module, and every public function, class and
  method defined in one, has a docstring (an override inherits its base's, as
  ``inspect.getdoc`` and the API docs do).
- **index** — every page under ``docs/`` is reachable from ``docs/readme.md``.

The module walk imports everything, which registers optional tables (the OAuth
``ExternalIdentity`` model) in the model registry, so it runs in a subprocess.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

#: Modules whose ``__all__`` is plumbing for the framework itself, not API a
#: project calls. Their behaviour is documented through what uses them.
INTERNAL_MODULES: dict[str, str] = {
    "zeeb_orm.cli.commands.startapp": "templates behind `startapp` (docs/cli/commands.md)",
    "zeeb_orm.cli.commands.startproject": "templates behind `startproject` (docs/cli/commands.md)",
    "zeeb_orm.scaffold": "templates and AST wiring shared by the CLI and zeeb_agents",
    "zeeb_orm.models.sa_builder": "SQLAlchemy table construction for the model metaclass",
    "zeeb_orm.models.relations": "pending-relation registry for the model metaclass",
    "zeeb_orm.models.deletion": "on_delete collector (behaviour in docs/orm/relationships.md)",
    "zeeb_orm.models.permissions_gen": "default permission generation for the model metaclass",
    "zeeb_orm.models.related_m2m": "descriptor behind ManyToManyField (docs/orm/relationships.md)",
    "zeeb_orm.query.joins": "join compiler shared by filter() and select_related()",
    "zeeb_orm.query.transforms": "per-dialect SQL for the __year/__month/... lookups",
}

#: Single exported names that are internal although their module is public.
INTERNAL_NAMES: dict[str, str] = {
    "zeeb_orm.query.prefetch.PrefetchedRelated": "the list prefetch_related() stores",
    "zeeb_orm.ForeignKeyField": "backwards-compatible alias; ForeignKey is documented",
    "zeeb_orm.RelationInfo": "relation-registry internals (zeeb_orm/models/relations.py)",
    "zeeb_orm.resolve_relation": "relation-registry internals (zeeb_orm/models/relations.py)",
}

_WALK = r"""
import importlib, inspect, json, pkgutil

def modules():
    for root in ("zeeb_orm", "zeeb_api", "zeeb_agents"):
        package = importlib.import_module(root)
        yield package
        for info in pkgutil.walk_packages(package.__path__, root + "."):
            if any(part.startswith("_") for part in info.name.split(".")[1:]):
                continue
            try:
                yield importlib.import_module(info.name)
            except ModuleNotFoundError as exc:
                # A missing optional extra (httpx for oauth) is the environment.
                if exc.name and exc.name.startswith("zeeb_"):
                    raise

def member_doc(cls, name, value):
    if isinstance(value, property):
        for klass in cls.__mro__:
            attr = vars(klass).get(name)
            if isinstance(attr, property) and attr.__doc__:
                return attr.__doc__
        return None
    return inspect.getdoc(getattr(cls, name, None))

exports, undocumented = [], []
for module in modules():
    name = module.__name__
    exports += [[name, n] for n in getattr(module, "__all__", None) or ()]
    if not (module.__doc__ or "").strip():
        undocumented.append(name)
    for attr, obj in vars(module).items():
        if attr.startswith("_") or getattr(obj, "__module__", None) != name:
            continue
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        if not inspect.getdoc(obj):
            undocumented.append(f"{name}.{attr}")
        if not inspect.isclass(obj):
            continue
        for member, value in vars(obj).items():
            if member.startswith("_"):
                continue
            func = getattr(value, "__func__", value)
            if not (inspect.isfunction(func) or isinstance(value, property)):
                continue
            if not member_doc(obj, member, value):
                undocumented.append(f"{name}.{attr}.{member}")

print(json.dumps({"exports": exports, "undocumented": undocumented}))
"""


@pytest.fixture(scope="module")
def walk() -> dict[str, list]:
    result = subprocess.run(
        [sys.executable, "-c", _WALK],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_every_public_export_is_documented(walk):
    prose = "".join(p.read_text() for p in sorted(DOCS.rglob("*.md")))

    missing = sorted(
        f"{module}.{name}"
        for module, name in walk["exports"]
        if module not in INTERNAL_MODULES
        and f"{module}.{name}" not in INTERNAL_NAMES
        and not name.startswith("_")
        and not re.search(rf"\b{re.escape(name)}\b", prose)
    )

    assert not missing, (
        f"public exports that appear nowhere in docs/ ({len(missing)}) — document "
        "them, or list the module in INTERNAL_MODULES with a reason:\n  "
        + "\n  ".join(missing)
    )


def test_every_public_object_has_a_docstring(walk):
    missing = walk["undocumented"]
    assert not missing, (
        f"public modules/classes/functions/methods without a docstring ({len(missing)}):\n  "
        + "\n  ".join(missing)
    )


def test_the_exclusions_are_still_real(walk):
    """An exclusion for a module or name that is gone hides nothing; drop it."""
    modules = {module for module, _ in walk["exports"]}
    names = {f"{module}.{name}" for module, name in walk["exports"]}

    stale = sorted(set(INTERNAL_MODULES) - modules) + sorted(set(INTERNAL_NAMES) - names)
    assert not stale, "stale documentation exclusions: " + ", ".join(stale)


def test_every_page_is_reachable_from_the_index():
    index = (DOCS / "readme.md").read_text()
    linked = {
        (DOCS / target.split("#")[0]).resolve()
        for target in re.findall(r"\]\(([^)]+\.md)(?:#[^)]*)?\)", index)
    }

    orphans = sorted(
        str(page.relative_to(DOCS))
        for page in DOCS.rglob("*.md")
        if page.name != "readme.md" and page.resolve() not in linked
    )
    assert not orphans, "docs pages not linked from docs/readme.md: " + ", ".join(orphans)
