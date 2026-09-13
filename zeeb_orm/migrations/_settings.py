"""Discover and load a generated project's ``settings.py`` module.

Generated Zeeb projects keep settings at ``<project>/<project_name>/settings.py``.
Several migration entry points need to read ``INSTALLED_APPS`` /
``AUTH_USER_MODEL`` / ``DATABASE`` from there; this centralizes the
discover-and-import dance that used to be copy-pasted across the codebase.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_settings_module(project_root: Path) -> ModuleType | None:
    """Import and return the project's ``settings.py`` module, or ``None``.

    Searches the immediate subdirectories of *project_root* for a
    ``settings.py`` and loads the first one found. *project_root* is
    temporarily placed on ``sys.path`` (if not already there) so the settings
    module can import project-local packages.
    """
    added_to_path = False
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
        added_to_path = True
    try:
        for item in sorted(project_root.iterdir()):
            if item.is_dir() and (item / "settings.py").exists():
                spec = importlib.util.spec_from_file_location(
                    "settings", item / "settings.py"
                )
                if spec is None or spec.loader is None:
                    return None
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
    except OSError:
        return None
    finally:
        if added_to_path and str(project_root) in sys.path:
            sys.path.remove(str(project_root))
    return None


def get_installed_apps(project_root: Path) -> list[str]:
    """Return ``INSTALLED_APPS`` from the project's settings (empty if absent)."""
    module = load_settings_module(project_root)
    if module is None:
        return []
    return list(getattr(module, "INSTALLED_APPS", []))


def get_database_url(
    project_root: Path, default: str = "sqlite:///db.sqlite3"
) -> str:
    """Return ``DATABASE["url"]`` from the project's settings, or *default*."""
    module = load_settings_module(project_root)
    if module is None:
        return default
    database = getattr(module, "DATABASE", {}) or {}
    return database.get("url", default)
