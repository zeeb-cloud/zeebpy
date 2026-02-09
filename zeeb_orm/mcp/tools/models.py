"""Model management tools for MCP."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from zeeb_orm.mcp.server import register_tool
from zeeb_orm.mcp.utils.project_utils import (
    find_project_root,
    get_app_path,
    list_apps,
    to_class_name,
    to_snake_case,
)
from zeeb_orm.mcp.utils.code_gen import (
    generate_model_code,
    parse_model_file,
    update_model_file,
    generate_field_line,
)
from zeeb_orm.mcp.utils.field_types import FIELD_TYPES


@register_tool(
    name="zeeb_create_model",
    description="Create a new model in an app's models.py file",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "Name of the app to add the model to"
            },
            "model_name": {
                "type": "string",
                "description": "Name of the model class (PascalCase)"
            },
            "fields": {
                "type": "array",
                "description": "List of field definitions",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Field name"},
                        "type": {"type": "string", "description": "Field type (string, text, integer, boolean, datetime, email, foreign_key, etc.)"},
                        "max_length": {"type": "integer", "description": "Max length for string fields"},
                        "null": {"type": "boolean", "description": "Allow NULL values"},
                        "blank": {"type": "boolean", "description": "Allow blank values"},
                        "unique": {"type": "boolean", "description": "Unique constraint"},
                        "default": {"description": "Default value"},
                        "to": {"type": "string", "description": "Target model for relationships"},
                        "auto_now": {"type": "boolean", "description": "Auto-update on save (datetime)"},
                        "auto_now_add": {"type": "boolean", "description": "Auto-set on create (datetime)"},
                    },
                    "required": ["name", "type"]
                }
            },
            "table_name": {
                "type": "string",
                "description": "Custom database table name (optional)"
            },
            "ordering": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Default ordering fields (e.g., ['-created_at'])"
            },
            "project_path": {
                "type": "string",
                "description": "Path to project root (default: auto-detect)"
            }
        },
        "required": ["app_name", "model_name", "fields"]
    }
)
def zeeb_create_model(
    app_name: str,
    model_name: str,
    fields: list[dict[str, Any]],
    table_name: str | None = None,
    ordering: list[str] | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Create a new model in an app."""
    # Find project and app
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    app_path = root / "apps" / app_name
    if not app_path.exists():
        return {"success": False, "error": f"App '{app_name}' not found"}
    
    models_file = app_path / "models.py"
    
    # Check if model already exists
    if models_file.exists():
        existing_models = parse_model_file(models_file)
        if model_name in existing_models:
            return {"success": False, "error": f"Model '{model_name}' already exists in {app_name}"}
    
    # Generate model code
    model_code = generate_model_code(
        model_name=model_name,
        fields=fields,
        table_name=table_name or f"{app_name}_{to_snake_case(model_name)}s",
        ordering=ordering,
    )
    
    # Append to models.py
    current_content = models_file.read_text() if models_file.exists() else ""
    
    # Add necessary imports if not present
    imports_needed = []
    if "from zeeb_orm import Model, fields" not in current_content:
        imports_needed.append("from zeeb_orm import Model, fields")
    
    # Build new content
    if imports_needed:
        if current_content.strip():
            new_content = "\n".join(imports_needed) + "\n\n" + current_content.strip() + "\n\n\n" + model_code + "\n"
        else:
            new_content = '"""Models for {app}."""\n\n'.format(app=app_name)
            new_content += "\n".join(imports_needed) + "\n\n\n" + model_code + "\n"
    else:
        new_content = current_content.rstrip() + "\n\n\n" + model_code + "\n"
    
    models_file.write_text(new_content)
    
    return {
        "success": True,
        "app_name": app_name,
        "model_name": model_name,
        "file_path": str(models_file),
        "fields_created": [f["name"] for f in fields],
        "model_code": model_code,
        "next_steps": [
            "Run 'zeeb-manage makemigrations' to create migration",
            "Run 'zeeb-manage migrate' to apply migration",
            f"Create serializer with zeeb_create_serializer(app_name='{app_name}', model_name='{model_name}')",
            f"Create viewset with zeeb_create_viewset(app_name='{app_name}', model_name='{model_name}')",
        ]
    }


@register_tool(
    name="zeeb_update_model",
    description="Update an existing model's fields",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model name to update"},
            "fields": {
                "type": "array",
                "description": "Complete list of fields (replaces existing)",
                "items": {"type": "object"}
            },
            "project_path": {"type": "string", "description": "Project path (optional)"}
        },
        "required": ["app_name", "model_name", "fields"]
    }
)
def zeeb_update_model(
    app_name: str,
    model_name: str,
    fields: list[dict[str, Any]],
    project_path: str | None = None,
) -> dict[str, Any]:
    """Update an existing model with new fields."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    app_path = root / "apps" / app_name
    models_file = app_path / "models.py"
    
    if not models_file.exists():
        return {"success": False, "error": f"models.py not found in {app_name}"}
    
    existing_models = parse_model_file(models_file)
    if model_name not in existing_models:
        return {"success": False, "error": f"Model '{model_name}' not found"}
    
    # For simplicity, we regenerate the entire model
    # In production, you'd want more sophisticated AST manipulation
    model_info = existing_models[model_name]
    table_name = model_info.get("meta", {}).get("table_name")
    
    # Generate new model code
    model_code = generate_model_code(
        model_name=model_name,
        fields=fields,
        table_name=table_name,
    )
    
    # Replace model in file (simplified - would need better implementation)
    content = models_file.read_text()
    
    # This is a simplified replacement - in production use AST
    import re
    pattern = rf'class {model_name}\(Model\):.*?(?=\nclass |\Z)'
    new_content = re.sub(pattern, model_code, content, flags=re.DOTALL)
    
    models_file.write_text(new_content)
    
    return {
        "success": True,
        "model_name": model_name,
        "fields_updated": [f["name"] for f in fields],
        "next_steps": [
            "Run 'zeeb-manage makemigrations' to create migration",
            "Run 'zeeb-manage migrate' to apply changes",
        ]
    }


@register_tool(
    name="zeeb_delete_model",
    description="Delete a model from an app",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model name to delete"},
            "project_path": {"type": "string", "description": "Project path (optional)"}
        },
        "required": ["app_name", "model_name"]
    }
)
def zeeb_delete_model(
    app_name: str,
    model_name: str,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Delete a model from an app."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    models_file = root / "apps" / app_name / "models.py"
    if not models_file.exists():
        return {"success": False, "error": f"models.py not found in {app_name}"}
    
    content = models_file.read_text()
    
    # Remove model class (simplified)
    import re
    pattern = rf'\n*class {model_name}\(Model\):.*?(?=\nclass |\Z)'
    new_content = re.sub(pattern, '', content, flags=re.DOTALL)
    
    models_file.write_text(new_content)
    
    return {
        "success": True,
        "deleted_model": model_name,
        "warnings": [
            "Create a migration to drop the table: zeeb-manage makemigrations",
            "Remove any serializers/viewsets that reference this model",
        ]
    }


@register_tool(
    name="zeeb_list_models",
    description="List all models in the project or a specific app",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {
                "type": "string",
                "description": "App name (optional - lists all apps if not provided)"
            },
            "project_path": {
                "type": "string",
                "description": "Project path (optional)"
            }
        }
    }
)
def zeeb_list_models(
    app_name: str | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """List all models in the project or a specific app."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    apps_to_check = [app_name] if app_name else list_apps(root)
    
    all_models = {}
    for app in apps_to_check:
        models_file = root / "apps" / app / "models.py"
        if models_file.exists():
            models = parse_model_file(models_file)
            all_models[app] = {
                name: {
                    "fields": list(info["fields"].keys()),
                    "field_details": info["fields"],
                    "meta": info.get("meta", {}),
                }
                for name, info in models.items()
            }
    
    return {
        "success": True,
        "models": all_models,
        "total_models": sum(len(m) for m in all_models.values()),
    }


@register_tool(
    name="zeeb_add_field",
    description="Add a field to an existing model",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model name"},
            "field_name": {"type": "string", "description": "New field name"},
            "field_type": {"type": "string", "description": "Field type"},
            "options": {
                "type": "object",
                "description": "Field options (null, default, max_length, etc.)"
            },
            "project_path": {"type": "string", "description": "Project path (optional)"}
        },
        "required": ["app_name", "model_name", "field_name", "field_type"]
    }
)
def zeeb_add_field(
    app_name: str,
    model_name: str,
    field_name: str,
    field_type: str,
    options: dict[str, Any] | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Add a field to an existing model."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    models_file = root / "apps" / app_name / "models.py"
    if not models_file.exists():
        return {"success": False, "error": f"models.py not found in {app_name}"}
    
    # Build field definition
    field_def = {"name": field_name, "type": field_type, **(options or {})}
    
    # Update the file
    try:
        new_content = update_model_file(models_file, model_name, new_field=field_def)
        models_file.write_text(new_content)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    
    return {
        "success": True,
        "model_name": model_name,
        "field_added": field_name,
        "field_type": field_type,
        "next_steps": [
            "Run 'zeeb-manage makemigrations' to create migration",
            "Run 'zeeb-manage migrate' to apply changes",
        ]
    }


@register_tool(
    name="zeeb_remove_field",
    description="Remove a field from a model",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model name"},
            "field_name": {"type": "string", "description": "Field to remove"},
            "project_path": {"type": "string", "description": "Project path (optional)"}
        },
        "required": ["app_name", "model_name", "field_name"]
    }
)
def zeeb_remove_field(
    app_name: str,
    model_name: str,
    field_name: str,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Remove a field from a model."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    models_file = root / "apps" / app_name / "models.py"
    if not models_file.exists():
        return {"success": False, "error": f"models.py not found in {app_name}"}
    
    try:
        new_content = update_model_file(models_file, model_name, remove_field=field_name)
        models_file.write_text(new_content)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    
    return {
        "success": True,
        "model_name": model_name,
        "field_removed": field_name,
        "next_steps": [
            "Run 'zeeb-manage makemigrations' to create migration",
            "Run 'zeeb-manage migrate' to apply changes",
        ]
    }


@register_tool(
    name="zeeb_add_relationship",
    description="Add a relationship (ForeignKey, OneToOne, ManyToMany) between models",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App containing the source model"},
            "model_name": {"type": "string", "description": "Source model name"},
            "field_name": {"type": "string", "description": "Relationship field name"},
            "target_model": {"type": "string", "description": "Target model (can include app: 'app.Model')"},
            "relationship_type": {
                "type": "string",
                "enum": ["foreign_key", "fk", "one_to_one", "o2o", "many_to_many", "m2m"],
                "description": "Type of relationship"
            },
            "on_delete": {
                "type": "string",
                "enum": ["CASCADE", "PROTECT", "SET_NULL", "SET_DEFAULT", "DO_NOTHING"],
                "description": "On delete behavior (for FK and O2O)"
            },
            "related_name": {"type": "string", "description": "Reverse relation name"},
            "null": {"type": "boolean", "description": "Allow NULL"},
            "project_path": {"type": "string", "description": "Project path (optional)"}
        },
        "required": ["app_name", "model_name", "field_name", "target_model", "relationship_type"]
    }
)
def zeeb_add_relationship(
    app_name: str,
    model_name: str,
    field_name: str,
    target_model: str,
    relationship_type: str,
    on_delete: str = "CASCADE",
    related_name: str | None = None,
    null: bool = False,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Add a relationship field to a model."""
    # Build field options
    options = {
        "to": target_model,
        "on_delete": f"'{on_delete}'",
    }
    if related_name:
        options["related_name"] = related_name
    if null:
        options["null"] = True
    
    # Use add_field with relationship type
    return zeeb_add_field(
        app_name=app_name,
        model_name=model_name,
        field_name=field_name,
        field_type=relationship_type,
        options=options,
        project_path=project_path,
    )
