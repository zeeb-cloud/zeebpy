"""Naming and project-location helpers shared by the scaffolding commands."""

from __future__ import annotations

from pathlib import Path


def find_project_root() -> Path | None:
    """Find the project root by looking for manage.py."""
    current = Path.cwd()

    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent

    return None


def to_class_name(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def to_title(name: str) -> str:
    """Convert snake_case to Title Case."""
    return " ".join(word.capitalize() for word in name.split("_"))


def pluralize(word: str) -> str:
    """English-plural a lowercase model name for a default route prefix.

    Same rule set as the hosting platform's codegen layer (company →
    companies, status → statuses, box → boxes) so plans and directly issued
    tool calls mount resources under identical prefixes.

    Lives here rather than in the agent layer because ``startapp`` needs the
    same prefix the agent tools would produce; ``zeeb_agents._utils.code_gen``
    re-exports it.
    """
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


def singularize(word: str) -> str:
    """Inverse of :func:`pluralize`, deliberately conservative.

    Only the shapes ``pluralize`` can produce are reversed; anything else is
    returned unchanged, because a wrong guess ends up naming a database table::

        companies → company     boxes → box       posts → post
        address   → address     status → status   class → class

    A naive ``rstrip("s")`` turns "address" into "addre" and "class" into
    "clas", so the ``ss``/``us``/``is`` endings are checked before the plain
    trailing ``s``.
    """
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    # "…es" only comes off when what is left is a stem pluralize would have
    # given an "es" to — otherwise "notes" would lose two characters.
    if word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return word[:-2]
    if word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("s") and len(word) > 1:
        return word[:-1]
    return word
