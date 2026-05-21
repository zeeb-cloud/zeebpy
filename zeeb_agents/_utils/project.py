"""Project discovery and settings helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) looking for ``manage.py``."""
    current = (start or Path.cwd()).resolve()
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent
    return None


def require_project_root(project_root: Path | None) -> Path:
    """Return *project_root* if given, else auto-detect; raise RuntimeError on failure."""
    root = project_root or find_project_root()
    if root is None:
        raise RuntimeError(
            "Could not find project root (no manage.py found). "
            "Pass project_root explicitly or run from a Zeeb project directory."
        )
    return root


def load_project_settings(project_root: Path) -> dict[str, Any]:
    """Dynamically load the project settings module and return its attributes."""
    settings: dict[str, Any] = {
        "DATABASE": {"url": "sqlite+aiosqlite:///db.sqlite3"},
        "INSTALLED_APPS": [],
    }
    for item in project_root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            spec = importlib.util.spec_from_file_location("_zeeb_settings", item / "settings.py")
            if spec and spec.loader:
                sys.path.insert(0, str(project_root))
                try:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)  # type: ignore[union-attr]
                    for attr in ("DATABASE", "INSTALLED_APPS", "AUTH_USER_MODEL"):
                        if hasattr(module, attr):
                            settings[attr] = getattr(module, attr)
                except Exception:
                    pass
                finally:
                    if str(project_root) in sys.path:
                        sys.path.remove(str(project_root))
            break
    return settings


def list_apps(project_root: Path) -> list[str]:
    """Return app directory names found under ``apps/``."""
    apps_dir = project_root / "apps"
    if not apps_dir.exists():
        return []
    return [
        d.name
        for d in sorted(apps_dir.iterdir())
        if d.is_dir() and not d.name.startswith("_")
    ]


def get_app_path(app_name: str, project_root: Path) -> Path:
    """Return the path to an app directory (apps/<app_name>)."""
    return project_root / "apps" / app_name


def to_class_name(name: str) -> str:
    """Convert ``snake_case`` → ``PascalCase``."""
    return "".join(word.capitalize() for word in name.replace("-", "_").split("_"))


def to_table_name(app_name: str, model_name: str) -> str:
    """Return a sensible default table name: ``<app>_<model_lower>``."""
    return f"{app_name}_{model_name.lower()}"
