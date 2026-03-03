"""Project management tools for MCP."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from zeeb_orm.mcp.server import register_tool
from zeeb_orm.mcp.utils.project_utils import (
    find_project_root,
    get_apps_dir,
    get_project_name,
    get_project_settings,
    list_apps,
    to_class_name,
    to_title,
)


@register_tool(
    name="zeeb_create_project",
    description="Create a new Zeeb project with the standard directory structure",
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Project name (must be a valid Python identifier)"
            },
            "directory": {
                "type": "string",
                "description": "Directory to create project in (default: current directory)"
            },
            "database": {
                "type": "string",
                "enum": ["sqlite", "postgresql", "mysql"],
                "description": "Database type (default: sqlite)"
            }
        },
        "required": ["name"]
    }
)
def zeeb_create_project(
    name: str,
    directory: str | None = None,
    database: str = "sqlite"
) -> dict[str, Any]:
    """Create a new Zeeb project."""
    from zeeb_orm.cli.commands.startproject import run_startproject
    
    # Validate name
    if not name.isidentifier():
        return {
            "success": False,
            "error": f"'{name}' is not a valid Python identifier"
        }
    
    # Set up directory
    base_dir = directory or os.getcwd()
    project_path = Path(base_dir) / name
    
    if project_path.exists():
        return {
            "success": False,
            "error": f"Directory '{project_path}' already exists"
        }
    
    # Run the startproject command
    result = run_startproject(name, base_dir)
    
    if result != 0:
        return {
            "success": False,
            "error": "Failed to create project"
        }
    
    # Update database URL if not sqlite
    if database != "sqlite":
        settings_path = project_path / name / "settings.py"
        content = settings_path.read_text()
        
        if database == "postgresql":
            new_url = "postgresql+asyncpg://user:password@localhost:5432/dbname"
        elif database == "mysql":
            new_url = "mysql+aiomysql://user:password@localhost:3306/dbname"
        else:
            new_url = "sqlite+aiosqlite:///db.sqlite3"
        
        content = content.replace(
            "sqlite+aiosqlite:///db.sqlite3",
            new_url
        )
        settings_path.write_text(content)
    
    return {
        "success": True,
        "project_path": str(project_path),
        "project_name": name,
        "database": database,
        "created_files": [
            f"{name}/manage.py",
            f"{name}/{name}/settings.py",
            f"{name}/{name}/urls.py",
            f"{name}/{name}/asgi.py",
            f"{name}/apps/",
            f"{name}/migrations/",
            f"{name}/requirements.txt",
        ],
        "next_steps": [
            f"cd {name}",
            "python -m venv .venv",
            "source .venv/bin/activate",
            "pip install -r requirements.txt",
            "zeeb-manage startapp myapp",
            "zeeb-manage init",
            "zeeb-manage makemigrations",
            "zeeb-manage migrate",
            "zeeb-manage runserver",
        ]
    }


@register_tool(
    name="zeeb_create_app",
    description="Create a new app within an existing Zeeb project",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "App name (must be a valid Python identifier)"
            },
            "project_path": {
                "type": "string",
                "description": "Path to project root (default: auto-detect)"
            }
        },
        "required": ["app_name"]
    }
)
def zeeb_create_app(
    app_name: str,
    project_path: str | None = None
) -> dict[str, Any]:
    """Create a new app in a Zeeb project."""
    from zeeb_orm.cli.commands.startapp import run_startapp
    
    # Validate name
    if not app_name.isidentifier():
        return {
            "success": False,
            "error": f"'{app_name}' is not a valid Python identifier"
        }
    
    # Find project root
    if project_path:
        root = Path(project_path)
        if not (root / "manage.py").exists():
            return {
                "success": False,
                "error": f"'{project_path}' is not a Zeeb project (no manage.py)"
            }
    else:
        root = find_project_root()
        if root is None:
            return {
                "success": False,
                "error": "Could not find project root. Run from within a Zeeb project or specify project_path"
            }
    
    # Change to project directory and run startapp
    original_dir = os.getcwd()
    try:
        os.chdir(root)
        result = run_startapp(app_name)
    finally:
        os.chdir(original_dir)
    
    if result != 0:
        return {
            "success": False,
            "error": "Failed to create app"
        }
    
    app_path = root / "apps" / app_name
    
    return {
        "success": True,
        "app_path": str(app_path),
        "app_name": app_name,
        "created_files": [
            f"apps/{app_name}/__init__.py",
            f"apps/{app_name}/models.py",
            f"apps/{app_name}/serializers.py",
            f"apps/{app_name}/views.py",
            f"apps/{app_name}/urls.py",
            f"apps/{app_name}/tests.py",
        ],
        "next_steps": [
            f"Add 'apps.{app_name}' to INSTALLED_APPS in settings.py",
            f"Define models in apps/{app_name}/models.py",
            f"Create serializers in apps/{app_name}/serializers.py",
            f"Create viewsets in apps/{app_name}/views.py",
            f"Register routes in apps/{app_name}/urls.py",
            "Include router in project urls.py",
            "Run 'zeeb-manage makemigrations'",
            "Run 'zeeb-manage migrate'",
        ]
    }


@register_tool(
    name="zeeb_delete_app",
    description="Delete an app from a Zeeb project",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name of the app to delete"
            },
            "project_path": {
                "type": "string",
                "description": "Path to project root (default: auto-detect)"
            },
            "remove_migrations": {
                "type": "boolean",
                "description": "Also remove app migrations (default: False)"
            }
        },
        "required": ["app_name"]
    }
)
def zeeb_delete_app(
    app_name: str,
    project_path: str | None = None,
    remove_migrations: bool = False
) -> dict[str, Any]:
    """Delete an app from a Zeeb project."""
    # Find project root
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {
            "success": False,
            "error": "Could not find project root"
        }
    
    app_path = root / "apps" / app_name
    if not app_path.exists():
        return {
            "success": False,
            "error": f"App '{app_name}' not found"
        }
    
    # Remove app directory
    shutil.rmtree(app_path)
    removed = [str(app_path)]
    
    # Optionally remove migrations
    if remove_migrations:
        # Note: This is a simplified approach
        # Real migration cleanup would need to handle the migration history properly
        pass
    
    return {
        "success": True,
        "removed": removed,
        "warnings": [
            f"Remember to remove 'apps.{app_name}' from INSTALLED_APPS in settings.py",
            "You may need to create a migration to drop the app's tables",
        ]
    }


@register_tool(
    name="zeeb_project_info",
    description="Get information about the current Zeeb project structure",
    input_schema={
        "type": "object",
        "properties": {
            "project_path": {
                "type": "string",
                "description": "Path to project root (default: auto-detect)"
            }
        }
    }
)
def zeeb_project_info(project_path: str | None = None) -> dict[str, Any]:
    """Get comprehensive project information."""
    # Find project root
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {
            "success": False,
            "error": "Could not find project root. Not in a Zeeb project directory."
        }
    
    project_name = get_project_name(root)
    apps = list_apps(root)
    settings = get_project_settings(root)
    
    # Get model info for each app
    apps_info = {}
    for app in apps:
        app_path = root / "apps" / app
        models_file = app_path / "models.py"
        
        app_info = {
            "path": str(app_path),
            "models": [],
            "has_serializers": (app_path / "serializers.py").exists(),
            "has_views": (app_path / "views.py").exists(),
            "has_urls": (app_path / "urls.py").exists(),
            "has_tests": (app_path / "tests.py").exists(),
        }
        
        # Parse models
        if models_file.exists():
            from zeeb_orm.mcp.utils.code_gen import parse_model_file
            models = parse_model_file(models_file)
            app_info["models"] = list(models.keys())
        
        apps_info[app] = app_info
    
    # Check migrations status
    migrations_dir = root / "migrations"
    has_migrations = migrations_dir.exists()
    
    return {
        "success": True,
        "project_name": project_name,
        "project_path": str(root),
        "apps": apps_info,
        "settings": settings,
        "migrations_initialized": has_migrations,
        "structure": {
            "manage.py": (root / "manage.py").exists(),
            "settings.py": (root / project_name / "settings.py").exists() if project_name else False,
            "urls.py": (root / project_name / "urls.py").exists() if project_name else False,
            "asgi.py": (root / project_name / "asgi.py").exists() if project_name else False,
            "apps/": (root / "apps").exists(),
            "migrations/": has_migrations,
        }
    }
