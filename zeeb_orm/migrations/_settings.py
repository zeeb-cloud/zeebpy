"""Read a generated project's settings for the migration entry points.

A thin layer over :mod:`zeeb_orm.conf.project`, the one resolver for "which
``settings.py`` does this project use". It used to carry its own
discover-and-import loop, which disagreed with the CLI's about which file to
pick and swallowed import errors.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

from zeeb_orm.conf.project import SettingsImportError, resolve_database_url
from zeeb_orm.conf.project import load_settings_module as _load


def load_settings_module(project_root: Path) -> ModuleType | None:
    """Import and return the project's settings module, or ``None`` if it has none.

    Raises:
        SettingsImportError: ``settings.py`` exists but does not import.
    """
    return _load(project_root)


def get_installed_apps(project_root: Path) -> list[str]:
    """Return ``INSTALLED_APPS`` from the project's settings (empty if absent)."""
    module = load_settings_module(project_root)
    if module is None:
        return []
    return list(getattr(module, "INSTALLED_APPS", []))


def get_database_url(
    project_root: Path, default: str = "sqlite:///db.sqlite3"
) -> str:
    """Return ``DATABASE["url"]`` from the project's settings, or *default*.

    *default* applies only when the project has no settings module or it
    declares no ``DATABASE`` URL — a settings module that fails to import
    raises instead of silently pointing the caller at a SQLite file.
    """
    module = load_settings_module(project_root)
    if module is None:
        return default
    database = getattr(module, "DATABASE", {}) or {}
    return database.get("url", default)


__all__ = [
    "SettingsImportError",
    "get_database_url",
    "get_installed_apps",
    "load_settings_module",
    "resolve_database_url",
]
