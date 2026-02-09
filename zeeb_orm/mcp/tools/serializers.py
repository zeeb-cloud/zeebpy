"""Serializer management tools for MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from zeeb_orm.mcp.server import register_tool
from zeeb_orm.mcp.utils.project_utils import find_project_root
from zeeb_orm.mcp.utils.code_gen import generate_serializer_code, parse_model_file


@register_tool(
    name="zeeb_create_serializer",
    description="Create a serializer for a model",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "model_name": {"type": "string", "description": "Model to serialize"},
            "serializer_name": {"type": "string", "description": "Custom serializer name (optional)"},
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Fields to include (default: all)"
            },
            "read_only_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Read-only fields"
            },
            "extra_fields": {
                "type": "array",
                "description": "Extra computed fields (SerializerMethodField)",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "code": {"type": "string", "description": "Method body code"}
                    }
                }
            },
            "project_path": {"type": "string", "description": "Project path (optional)"}
        },
        "required": ["app_name", "model_name"]
    }
)
def zeeb_create_serializer(
    app_name: str,
    model_name: str,
    serializer_name: str | None = None,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    extra_fields: list[dict[str, Any]] | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Create a serializer for a model."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    app_path = root / "apps" / app_name
    if not app_path.exists():
        return {"success": False, "error": f"App '{app_name}' not found"}
    
    # Verify model exists
    models_file = app_path / "models.py"
    if models_file.exists():
        models = parse_model_file(models_file)
        if model_name not in models:
            return {"success": False, "error": f"Model '{model_name}' not found in {app_name}"}
        
        # Auto-detect fields if not specified
        if fields is None:
            model_fields = list(models[model_name]["fields"].keys())
            fields = ["id"] + model_fields
    
    # Generate serializer code
    serializer_code = generate_serializer_code(
        model_name=model_name,
        serializer_name=serializer_name,
        fields=fields,
        read_only_fields=read_only_fields,
        extra_fields=extra_fields,
    )
    
    # Write to serializers.py
    serializers_file = app_path / "serializers.py"
    current_content = serializers_file.read_text() if serializers_file.exists() else ""
    
    # Build imports
    imports = [
        "from zeeb_api import serializers",
        f"from .models import {model_name}",
    ]
    
    # Check which imports are needed
    imports_to_add = []
    for imp in imports:
        if imp not in current_content:
            imports_to_add.append(imp)
    
    # Build new content
    if current_content.strip():
        # Add missing imports at top
        if imports_to_add:
            # Find last import line
            lines = current_content.split("\n")
            last_import_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("from ") or line.startswith("import "):
                    last_import_idx = i
            
            for imp in imports_to_add:
                lines.insert(last_import_idx + 1, imp)
                last_import_idx += 1
            
            current_content = "\n".join(lines)
        
        new_content = current_content.rstrip() + "\n\n\n" + serializer_code + "\n"
    else:
        new_content = f'"""{app_name} serializers."""\n\n'
        new_content += "\n".join(imports) + "\n\n\n"
        new_content += serializer_code + "\n"
    
    serializers_file.write_text(new_content)
    
    name = serializer_name or f"{model_name}Serializer"
    
    return {
        "success": True,
        "serializer_name": name,
        "model_name": model_name,
        "file_path": str(serializers_file),
        "serializer_code": serializer_code,
        "fields": fields,
        "next_steps": [
            f"Create viewset with zeeb_create_viewset(app_name='{app_name}', model_name='{model_name}')",
        ]
    }


@register_tool(
    name="zeeb_update_serializer",
    description="Update an existing serializer",
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "App name"},
            "serializer_name": {"type": "string", "description": "Serializer class name"},
            "fields": {"type": "array", "items": {"type": "string"}, "description": "New fields list"},
            "read_only_fields": {"type": "array", "items": {"type": "string"}},
            "project_path": {"type": "string"}
        },
        "required": ["app_name", "serializer_name"]
    }
)
def zeeb_update_serializer(
    app_name: str,
    serializer_name: str,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Update an existing serializer."""
    root = Path(project_path) if project_path else find_project_root()
    if root is None:
        return {"success": False, "error": "Could not find project root"}
    
    serializers_file = root / "apps" / app_name / "serializers.py"
    if not serializers_file.exists():
        return {"success": False, "error": f"serializers.py not found in {app_name}"}
    
    content = serializers_file.read_text()
    
    # Update fields in Meta class (simplified)
    import re
    
    if fields:
        fields_str = ", ".join(f'"{f}"' for f in fields)
        # Find and replace fields in the specific serializer
        pattern = rf'(class {serializer_name}.*?fields\s*=\s*)\[[^\]]*\]'
        replacement = rf'\1[{fields_str}]'
        content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    
    if read_only_fields:
        ro_str = ", ".join(f'"{f}"' for f in read_only_fields)
        pattern = rf'(class {serializer_name}.*?read_only_fields\s*=\s*)\[[^\]]*\]'
        replacement = rf'\1[{ro_str}]'
        content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    
    serializers_file.write_text(content)
    
    return {
        "success": True,
        "serializer_name": serializer_name,
        "updated_fields": fields,
        "updated_read_only_fields": read_only_fields,
    }
