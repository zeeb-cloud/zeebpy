"""Naming and project-location helpers shared by the scaffolding commands."""

from __future__ import annotations

import keyword
import sys
from pathlib import Path

#: zeebpy's own top-level packages.
FRAMEWORK_PACKAGES = frozenset({"zeeb_orm", "zeeb_api", "zeeb_agents"})

#: Import names of zeebpy's runtime dependencies (and theirs, and the database
#: drivers and test tools a generated project uses). A project package with
#: one of these names sits first on ``sys.path`` and shadows the real module,
#: so the project fails its own first import.
DEPENDENCY_IMPORT_NAMES = frozenset(
    {
        # direct dependencies
        "sqlalchemy", "alembic", "greenlet", "fastapi", "pydantic", "uvicorn",
        "jwt", "bcrypt", "email_validator",
        # what those pull in
        "starlette", "anyio", "sniffio", "idna", "typing_extensions",
        "typing_inspection", "annotated_types", "pydantic_core", "mako", "markupsafe",
        "h11", "click", "httptools", "uvloop", "watchfiles", "websockets", "dotenv",
        "yaml", "dns",
        # optional extras: database drivers, OAuth
        "asyncpg", "psycopg2", "aiomysql", "pymysql", "aiosqlite", "httpx", "httpcore",
        "certifi", "multipart", "python_multipart", "cryptography", "cffi",
        # what a generated project's tests run on
        "pytest", "_pytest", "pluggy", "pytest_asyncio", "iniconfig", "packaging",
    }
)

#: Top-level names a generated project already uses for its own directories.
PROJECT_LAYOUT_NAMES = frozenset({"apps", "tests", "migrations", "logs"})


def identifier_problem(name: str, kind: str = "name") -> str | None:
    """Why *name* cannot be a Python package/module name, or ``None`` if it can.

    A keyword (``class``) passes ``str.isidentifier()`` but makes every
    ``import`` that names it a syntax error. Soft keywords (``match``,
    ``type``) are fine in imports and are allowed.
    """
    if not isinstance(name, str) or not name.isidentifier():
        return f"'{name}' is not a valid Python identifier, so it cannot be a {kind}."
    if keyword.iskeyword(name):
        return (
            f"'{name}' is a Python keyword, so it cannot be a {kind} "
            f"(`import {name}` is a syntax error)."
        )
    return None


def app_name_problem(name: str) -> str | None:
    """Why *name* cannot be an app (``apps/<name>/``), or ``None`` if it can."""
    return identifier_problem(name, "package name")


def project_name_problem(name: str) -> str | None:
    """Why *name* cannot be a project, or ``None`` if it can.

    The project package lands first on ``sys.path``, so besides being an
    identifier that is not a keyword it must not shadow a standard-library
    module, one of zeebpy's packages or a dependency, nor collide with the
    directories every project has (``apps``, ``tests``, …).
    """
    problem = identifier_problem(name, "package name")
    if problem:
        return problem
    lowered = name.lower()
    if lowered in sys.stdlib_module_names:
        return (
            f"'{name}' is the name of a Python standard-library module; a project "
            f"package called that would shadow it."
        )
    if lowered in FRAMEWORK_PACKAGES or lowered in DEPENDENCY_IMPORT_NAMES:
        return (
            f"'{name}' is the import name of a package zeebpy depends on; a project "
            f"package called that would shadow it and the project could not import it."
        )
    if lowered in PROJECT_LAYOUT_NAMES:
        return f"'{name}' is a directory every Zeeb project already has."
    return None


def find_project_root() -> Path | None:
    """Find the project root: the nearest directory with ``manage.py``.

    Delegates to :func:`zeeb_orm.conf.project.find_project_root`.
    """
    from zeeb_orm.conf.project import find_project_root as _find

    return _find()


def to_class_name(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def to_title(name: str) -> str:
    """Convert snake_case to Title Case."""
    return " ".join(word.capitalize() for word in name.split("_"))


def pluralize(word: str) -> str:
    """English-plural a lowercase model name for a default route prefix.

    Regular rules only (company → companies, status → statuses, box → boxes),
    so plans and directly issued tool calls mount resources under identical
    prefixes.

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
