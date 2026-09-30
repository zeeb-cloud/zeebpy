"""Find a generated project, its settings module and its database — one way.

Every tool that works on a project on disk needs the same three answers, and
they used to be computed four different ways: sorted or in directory order,
with or without skipping ``apps/``, ignoring the ``[tool.zeeb]
settings_module`` key that ``startproject`` writes, and — worst — falling back
to ``sqlite:///db.sqlite3`` without a word when ``settings.py`` existed but
failed to import, so ``migrate`` happily migrated a database nobody uses.

This module is the single resolver:

* :func:`find_project_root` — the nearest directory with ``manage.py``
  (a directory with ``migrations/`` only when no ``manage.py`` is found).
* :func:`find_settings_module` — ``[tool.zeeb] settings_module`` from
  ``pyproject.toml`` when it names an existing file; otherwise the first
  directory, in sorted order and skipping ``apps/`` and hidden directories,
  that holds a ``settings.py``.
* :func:`load_settings_module` — imports it, and raises
  :class:`SettingsImportError` when it exists but does not import.
* :func:`resolve_database_url` — ``DATABASE["url"]`` from those settings,
  else the ORM's own configuration (``DATABASE_URL`` / its default).

Anything that reads a project's settings off disk should go through here
(``zeeb_api``'s discovery included).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

#: Directories that never hold the settings package.
_SKIPPED_DIRS = frozenset({"apps", "migrations", "tests", "logs", "node_modules", "venv"})


class SettingsImportError(RuntimeError):
    """A project's ``settings.py`` exists but could not be imported.

    Attributes:
        path: The settings file.
        module: Its dotted name (``"<package>.settings"``).
    """

    def __init__(self, path: Path, module: str, cause: BaseException) -> None:
        self.path = path
        self.module = module
        self.cause = cause
        super().__init__(
            f"{module} ({path}) could not be imported: {type(cause).__name__}: {cause}"
        )


def find_project_root(
    start: Path | str | None = None, *, allow_migrations_dir: bool = False
) -> Path | None:
    """The project directory containing ``start`` (default: the CWD).

    The nearest ancestor with ``manage.py``. With ``allow_migrations_dir``
    (the programmatic migration API, which also serves projects without a
    ``manage.py``), the nearest directory with a ``migrations/`` directory is
    used when no ``manage.py`` exists anywhere above — a ``manage.py``
    further up always wins over a nested ``migrations/``.
    """
    current = Path(start) if start is not None else Path.cwd()
    chain = [current, *current.parents]
    for directory in chain:
        if (directory / "manage.py").is_file():
            return directory
    if allow_migrations_dir:
        for directory in chain:
            if (directory / "migrations").is_dir():
                return directory
    return None


def _declared_settings_module(project_root: Path) -> str | None:
    """``[tool.zeeb] settings_module`` from ``pyproject.toml``, if declared."""
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    text = pyproject.read_text(encoding="utf-8", errors="replace")
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        tomllib = None  # type: ignore[assignment]
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return None
        value = data.get("tool", {}).get("zeeb", {}).get("settings_module")
        return value if isinstance(value, str) and value else None
    section = re.search(r"^\[tool\.zeeb\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if section is None:
        return None
    match = re.search(r"^settings_module\s*=\s*[\"']([^\"']+)[\"']", section.group(1), re.M)
    return match.group(1) if match else None


def _module_path(project_root: Path, module: str) -> Path:
    return project_root.joinpath(*module.split(".")).with_suffix(".py")


def find_settings_module(project_root: Path | str) -> str | None:
    """The dotted name of the project's settings module, or ``None``.

    Order: ``[tool.zeeb] settings_module`` when the file it names exists;
    then the first top-level directory (sorted by name; ``apps/``, hidden and
    tooling directories skipped) that holds a ``settings.py``.
    """
    root = Path(project_root)
    declared = _declared_settings_module(root)
    if declared and _module_path(root, declared).is_file():
        return declared
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return None
    for item in entries:
        if (
            item.is_dir()
            and item.name not in _SKIPPED_DIRS
            and not item.name.startswith((".", "_"))
            and (item / "settings.py").is_file()
        ):
            return f"{item.name}.settings"
    return None


def find_settings_path(project_root: Path | str) -> Path | None:
    """The settings file :func:`find_settings_module` names, or ``None``."""
    module = find_settings_module(project_root)
    return _module_path(Path(project_root), module) if module else None


def load_settings_module(project_root: Path | str) -> ModuleType | None:
    """Import and return the project's settings module.

    The module is executed fresh (not cached in ``sys.modules``, so two
    projects with the same package name never share settings), with the
    project root temporarily on ``sys.path`` so it can import project-local
    packages.

    Returns ``None`` when the project has no settings module at all.

    Raises:
        SettingsImportError: the settings file exists but raised on import —
            never papered over with defaults.
    """
    root = Path(project_root)
    module_name = find_settings_module(root)
    if module_name is None:
        return None
    path = _module_path(root, module_name)

    added_to_path = False
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
        added_to_path = True
    try:
        spec = importlib.util.spec_from_file_location("settings", path)
        if spec is None or spec.loader is None:
            raise SettingsImportError(path, module_name, ImportError("no loader"))
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise SettingsImportError(path, module_name, exc) from exc
        return module
    finally:
        if added_to_path and str(root) in sys.path:
            sys.path.remove(str(root))


def resolve_database_url(project_root: Path | str | None) -> str:
    """The database URL tooling should use for ``project_root``.

    ``DATABASE["url"]`` from the project's settings when it declares one;
    otherwise the ORM's own configuration (:func:`zeeb_orm.conf.get_settings`:
    ``DATABASE_URL`` from the environment, or its SQLite default). The same
    answer for the CLI and the programmatic migration API.

    Raises:
        SettingsImportError: the settings module exists but does not import.
    """
    if project_root is not None:
        module = load_settings_module(project_root)
        database = getattr(module, "DATABASE", None) if module is not None else None
        if isinstance(database, dict) and database.get("url"):
            return str(database["url"])
    from zeeb_orm.conf.settings import get_settings

    return get_settings().database.url


__all__ = [
    "SettingsImportError",
    "find_project_root",
    "find_settings_module",
    "find_settings_path",
    "load_settings_module",
    "resolve_database_url",
]
