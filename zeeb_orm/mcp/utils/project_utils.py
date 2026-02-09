"""Project utility functions for MCP tools."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def find_project_root(start_path: str | Path | None = None) -> Path | None:
    """
    Find the Zeeb project root by looking for manage.py.
    
    Args:
        start_path: Starting directory (default: current working directory)
    
    Returns:
        Project root path or None if not found
    """
    current = Path(start_path or os.getcwd()).resolve()
    
    while current != current.parent:
        if (current / "manage.py").exists():
            return current
        current = current.parent
    
    return None


def get_apps_dir(project_root: Path | None = None) -> Path | None:
    """Get the apps directory for a project."""
    root = project_root or find_project_root()
    if root is None:
        return None
    
    apps_dir = root / "apps"
    return apps_dir if apps_dir.exists() else None


def get_project_name(project_root: Path | None = None) -> str | None:
    """Get the project name from the project structure."""
    root = project_root or find_project_root()
    if root is None:
        return None
    
    # Look for settings.py in a subdirectory with same name as root
    for item in root.iterdir():
        if item.is_dir() and (item / "settings.py").exists():
            return item.name
    
    return None


def get_project_settings(project_root: Path | None = None) -> dict[str, Any]:
    """
    Load project settings.
    
    Returns a dict with key settings (sanitized - no secrets).
    """
    root = project_root or find_project_root()
    if root is None:
        return {}
    
    project_name = get_project_name(root)
    if project_name is None:
        return {}
    
    settings_path = root / project_name / "settings.py"
    if not settings_path.exists():
        return {}
    
    # Parse settings file to extract key values
    settings = {}
    content = settings_path.read_text()
    
    # Simple extraction of key settings
    import re
    
    # DEBUG
    match = re.search(r'DEBUG\s*=\s*(.+)', content)
    if match:
        settings["DEBUG"] = "true" in match.group(1).lower()
    
    # INSTALLED_APPS
    match = re.search(r'INSTALLED_APPS\s*=\s*\[(.*?)\]', content, re.DOTALL)
    if match:
        apps = re.findall(r'"([^"]+)"', match.group(1))
        settings["INSTALLED_APPS"] = apps
    
    # DATABASE URL (sanitized)
    match = re.search(r'DATABASE_URL.*?"([^"]+)"', content)
    if match:
        url = match.group(1)
        # Sanitize - remove password
        settings["DATABASE_URL"] = re.sub(r'://[^:]+:[^@]+@', '://***:***@', url)
    
    return settings


def get_app_path(app_name: str, project_root: Path | None = None) -> Path | None:
    """Get the path to a specific app."""
    apps_dir = get_apps_dir(project_root)
    if apps_dir is None:
        return None
    
    app_path = apps_dir / app_name
    return app_path if app_path.exists() else None


def list_apps(project_root: Path | None = None) -> list[str]:
    """List all apps in the project."""
    apps_dir = get_apps_dir(project_root)
    if apps_dir is None:
        return []
    
    apps = []
    for item in apps_dir.iterdir():
        if item.is_dir() and (item / "__init__.py").exists():
            apps.append(item.name)
    
    return sorted(apps)


def to_class_name(name: str) -> str:
    """Convert snake_case to PascalCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def to_snake_case(name: str) -> str:
    """Convert PascalCase to snake_case."""
    import re
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def to_title(name: str) -> str:
    """Convert snake_case to Title Case."""
    return " ".join(word.capitalize() for word in name.split("_"))
