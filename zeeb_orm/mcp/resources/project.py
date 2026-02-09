"""Project resources for MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zeeb_orm.mcp.server import register_resource
from zeeb_orm.mcp.utils.project_utils import (
    find_project_root,
    get_project_name,
    get_project_settings,
    list_apps,
)
from zeeb_orm.mcp.utils.code_gen import parse_model_file


@register_resource(
    uri="zeeb://project/structure",
    name="Project Structure",
    description="Current Zeeb project directory structure and files"
)
def get_project_structure() -> dict[str, Any]:
    """Get the project directory structure."""
    root = find_project_root()
    if root is None:
        return {"error": "Not in a Zeeb project"}
    
    def build_tree(path: Path, prefix: str = "") -> list[str]:
        """Build a tree representation of a directory."""
        items = []
        entries = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name))
        
        # Filter out __pycache__, .venv, etc.
        skip = {"__pycache__", ".venv", ".git", ".pytest_cache", ".ruff_cache", "node_modules"}
        entries = [e for e in entries if e.name not in skip]
        
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            items.append(f"{prefix}{connector}{entry.name}")
            
            if entry.is_dir() and not entry.name.startswith("."):
                extension = "    " if is_last else "│   "
                items.extend(build_tree(entry, prefix + extension))
        
        return items
    
    tree_lines = [root.name + "/"] + build_tree(root)
    
    return {
        "project_name": get_project_name(root),
        "project_path": str(root),
        "tree": "\n".join(tree_lines),
        "apps": list_apps(root),
    }


@register_resource(
    uri="zeeb://project/models",
    name="Project Models",
    description="All models defined in the project"
)
def get_project_models() -> dict[str, Any]:
    """Get all models in the project."""
    root = find_project_root()
    if root is None:
        return {"error": "Not in a Zeeb project"}
    
    all_models = {}
    
    for app in list_apps(root):
        models_file = root / "apps" / app / "models.py"
        if models_file.exists():
            models = parse_model_file(models_file)
            if models:
                all_models[app] = {}
                for name, info in models.items():
                    all_models[app][name] = {
                        "fields": info["fields"],
                        "meta": info.get("meta", {}),
                    }
    
    # Count totals
    total_models = sum(len(m) for m in all_models.values())
    total_fields = sum(
        len(model["fields"])
        for app_models in all_models.values()
        for model in app_models.values()
    )
    
    return {
        "models": all_models,
        "total_models": total_models,
        "total_fields": total_fields,
    }


@register_resource(
    uri="zeeb://project/endpoints",
    name="Project Endpoints",
    description="All API endpoints in the project"
)
def get_project_endpoints() -> dict[str, Any]:
    """Get all API endpoints in the project."""
    from zeeb_orm.mcp.tools.viewsets import zeeb_list_endpoints
    
    result = zeeb_list_endpoints()
    return result


@register_resource(
    uri="zeeb://project/settings",
    name="Project Settings",
    description="Current project settings (sanitized)"
)
def get_settings_resource() -> dict[str, Any]:
    """Get project settings (sanitized)."""
    root = find_project_root()
    if root is None:
        return {"error": "Not in a Zeeb project"}
    
    settings = get_project_settings(root)
    
    return {
        "project_name": get_project_name(root),
        "settings": settings,
    }
