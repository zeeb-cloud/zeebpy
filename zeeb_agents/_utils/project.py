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


DEFAULT_FRAMEWORK = "zeebpy"


def write_framework_marker(project_root: Path, framework: str) -> None:
    """Record ``framework`` under ``[tool.zeeb]`` in the project's pyproject.toml.

    Creates ``pyproject.toml`` if absent; otherwise ensures a ``[tool.zeeb]``
    section with a ``framework`` key (a light, dependency-free text edit — no
    TOML writer needed for this one key).
    """
    path = project_root / "pyproject.toml"
    marker = f'[tool.zeeb]\nframework = "{framework}"\n'
    if not path.exists():
        path.write_text(marker, encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    if "[tool.zeeb]" not in text:
        sep = "" if text.endswith("\n") else "\n"
        path.write_text(f"{text}{sep}\n{marker}", encoding="utf-8")


def detect_framework(project_root: Path | None) -> str:
    """Return the project's framework id, defaulting to ``"zeebpy"``.

    Reads ``[tool.zeeb] framework`` from the project's ``pyproject.toml`` when
    present; otherwise falls back to :data:`DEFAULT_FRAMEWORK`. Never raises.
    """
    if project_root is None:
        return DEFAULT_FRAMEWORK
    path = project_root / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_FRAMEWORK
    import re

    m = re.search(
        r"\[tool\.zeeb\][^\[]*?\bframework\s*=\s*[\"']([^\"']+)[\"']",
        text,
        re.DOTALL,
    )
    return m.group(1) if m else DEFAULT_FRAMEWORK


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
                    # Capture every top-level setting by Django convention:
                    # UPPERCASE, non-dunder module attributes (DATABASE,
                    # INSTALLED_APPS, CORS_*, SECRET_KEY, DEBUG, …).
                    for attr in dir(module):
                        if attr.isupper() and not attr.startswith("_"):
                            settings[attr] = getattr(module, attr)
                except Exception:
                    pass
                finally:
                    if str(project_root) in sys.path:
                        sys.path.remove(str(project_root))
            break
    return settings


def resolve_db_url(settings: dict[str, Any], project_root: Path) -> str:
    """Return the settings DATABASE url with relative sqlite paths anchored
    at *project_root*.

    zeeb_agents operates on target projects by path and must not depend on
    the process CWD — but a relative sqlite URL (``sqlite:///db.sqlite3``)
    resolves against the CWD when passed to SQLAlchemy.  Absolute URLs and
    ``:memory:`` are returned unchanged.
    """
    import re

    url: str = settings.get("DATABASE", {}).get("url", "sqlite+aiosqlite:///db.sqlite3")
    m = re.match(r"^(sqlite(?:\+\w+)?)://(/?)(?!/)(.*)$", url)
    if m:
        driver, _slash, rel = m.groups()
        if rel and rel != ":memory:" and not rel.startswith("/"):
            return f"{driver}:///{(project_root / rel).resolve()}"
    return url


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
