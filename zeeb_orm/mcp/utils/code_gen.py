"""Code generation utilities for MCP tools."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from zeeb_orm.mcp.utils.field_types import parse_field_definition, generate_field_line
from zeeb_orm.mcp.utils.project_utils import to_class_name


def generate_model_code(
    model_name: str,
    fields: list[dict[str, Any]],
    table_name: str | None = None,
    ordering: list[str] | None = None,
    abstract: bool = False,
) -> str:
    """
    Generate model class code.
    
    Args:
        model_name: Name of the model class (PascalCase)
        fields: List of field definitions
        table_name: Optional custom table name
        ordering: Optional default ordering
        abstract: Whether this is an abstract model
    
    Returns:
        Model class code as string
    """
    lines = [f"class {model_name}(Model):"]
    lines.append(f'    """Model for {model_name}."""')
    lines.append("")
    
    # Generate fields
    for field_def in fields:
        field_name = field_def.get("name")
        if field_name:
            field_line = generate_field_line(field_name, field_def)
            lines.append(field_line)
    
    # Generate Meta class if needed
    if table_name or ordering or abstract:
        lines.append("")
        lines.append("    class Meta:")
        if table_name:
            lines.append(f'        table_name = "{table_name}"')
        if ordering:
            ordering_str = ", ".join(f'"{o}"' for o in ordering)
            lines.append(f"        ordering = [{ordering_str}]")
        if abstract:
            lines.append("        abstract = True")
    
    return "\n".join(lines)


def generate_serializer_code(
    model_name: str,
    serializer_name: str | None = None,
    fields: list[str] | None = None,
    read_only_fields: list[str] | None = None,
    extra_fields: list[dict[str, Any]] | None = None,
) -> str:
    """
    Generate serializer class code.
    
    Args:
        model_name: Name of the model
        serializer_name: Optional custom serializer name
        fields: List of fields to include (default: all)
        read_only_fields: List of read-only fields
        extra_fields: Extra SerializerMethodField definitions
    
    Returns:
        Serializer class code as string
    """
    name = serializer_name or f"{model_name}Serializer"
    
    lines = [f"class {name}(serializers.ModelSerializer):"]
    lines.append(f'    """Serializer for {model_name}."""')
    
    # Add extra method fields if any
    if extra_fields:
        lines.append("")
        for field in extra_fields:
            field_name = field.get("name")
            lines.append(f"    {field_name} = serializers.SerializerMethodField()")
    
    # Meta class
    lines.append("")
    lines.append("    class Meta:")
    lines.append(f"        model = {model_name}")
    
    if fields:
        fields_str = ", ".join(f'"{f}"' for f in fields)
        lines.append(f"        fields = [{fields_str}]")
    else:
        lines.append('        fields = "__all__"')
    
    if read_only_fields:
        ro_str = ", ".join(f'"{f}"' for f in read_only_fields)
        lines.append(f"        read_only_fields = [{ro_str}]")
    
    # Add method implementations for extra fields
    if extra_fields:
        for field in extra_fields:
            field_name = field.get("name")
            method_code = field.get("code", "return None")
            lines.append("")
            lines.append(f"    def get_{field_name}(self, obj):")
            # Indent the method code
            for code_line in method_code.split("\n"):
                lines.append(f"        {code_line}")
    
    return "\n".join(lines)


def generate_viewset_code(
    model_name: str,
    viewset_name: str | None = None,
    serializer_name: str | None = None,
    actions: list[str] | None = None,
    permission_classes: list[str] | None = None,
) -> str:
    """
    Generate viewset class code.
    
    Args:
        model_name: Name of the model
        viewset_name: Optional custom viewset name
        serializer_name: Optional custom serializer name
        actions: List of actions to include (default: all CRUD)
        permission_classes: List of permission class names
    
    Returns:
        ViewSet class code as string
    """
    name = viewset_name or f"{model_name}ViewSet"
    serializer = serializer_name or f"{model_name}Serializer"
    
    # Determine base class based on actions
    if actions:
        if set(actions) == {"list", "retrieve"}:
            base_class = "viewsets.ReadOnlyModelViewSet"
        else:
            base_class = "viewsets.ModelViewSet"
    else:
        base_class = "viewsets.ModelViewSet"
    
    lines = [f"class {name}({base_class}):"]
    lines.append(f'    """ViewSet for {model_name}."""')
    lines.append("")
    lines.append(f"    queryset = {model_name}.objects")
    lines.append(f"    serializer_class = {serializer}")
    
    if permission_classes:
        perms_str = ", ".join(f"permissions.{p}" for p in permission_classes)
        lines.append(f"    permission_classes = [{perms_str}]")
    
    return "\n".join(lines)


def generate_url_registration(
    app_name: str,
    model_name: str,
    viewset_name: str | None = None,
    url_prefix: str | None = None,
) -> str:
    """Generate router.register() line for a viewset."""
    vs_name = viewset_name or f"{model_name}ViewSet"
    prefix = url_prefix or model_name.lower() + "s"
    return f'router.register("{prefix}", {vs_name})'


def parse_model_file(file_path: Path) -> dict[str, Any]:
    """
    Parse a models.py file to extract model definitions.
    
    Returns:
        Dict with model names as keys and field info as values
    """
    if not file_path.exists():
        return {}
    
    content = file_path.read_text()
    models = {}
    
    # Simple regex-based parsing (more robust than AST for partial files)
    class_pattern = re.compile(
        r'class\s+(\w+)\s*\(\s*(?:Model|[\w.]+)\s*\)\s*:',
        re.MULTILINE
    )
    
    for match in class_pattern.finditer(content):
        model_name = match.group(1)
        start_pos = match.end()
        
        # Find the end of this class (next class or end of file)
        next_class = class_pattern.search(content, start_pos)
        end_pos = next_class.start() if next_class else len(content)
        
        class_body = content[start_pos:end_pos]
        
        # Extract fields
        fields = {}
        field_pattern = re.compile(
            r'^\s+(\w+)\s*=\s*fields\.(\w+)\s*\((.*?)\)',
            re.MULTILINE
        )
        
        for field_match in field_pattern.finditer(class_body):
            field_name = field_match.group(1)
            field_type = field_match.group(2)
            field_args = field_match.group(3)
            
            fields[field_name] = {
                "type": field_type,
                "args": field_args,
            }
        
        # Extract Meta if present
        meta = {}
        meta_match = re.search(
            r'class\s+Meta\s*:\s*(.*?)(?=\n\s*(?:class|def)|\Z)',
            class_body,
            re.DOTALL
        )
        if meta_match:
            meta_body = meta_match.group(1)
            table_match = re.search(r'table_name\s*=\s*["\'](\w+)["\']', meta_body)
            if table_match:
                meta["table_name"] = table_match.group(1)
        
        models[model_name] = {
            "fields": fields,
            "meta": meta,
        }
    
    return models


def update_model_file(
    file_path: Path,
    model_name: str,
    new_field: dict[str, Any] | None = None,
    remove_field: str | None = None,
) -> str:
    """
    Update a model in a models.py file.
    
    Args:
        file_path: Path to models.py
        model_name: Name of model to update
        new_field: Field definition to add
        remove_field: Name of field to remove
    
    Returns:
        Updated file content
    """
    content = file_path.read_text()
    
    # Find the model class
    class_pattern = re.compile(
        rf'class\s+{model_name}\s*\([^)]+\)\s*:',
        re.MULTILINE
    )
    
    match = class_pattern.search(content)
    if not match:
        raise ValueError(f"Model {model_name} not found")
    
    if new_field:
        # Find insertion point (after last field, before Meta or end of class)
        class_start = match.end()
        
        # Find where to insert (look for Meta class or next class)
        meta_match = re.search(r'\n(\s+)class\s+Meta\s*:', content[class_start:])
        next_class = re.search(r'\nclass\s+\w+', content[class_start:])
        
        if meta_match:
            insert_pos = class_start + meta_match.start()
        elif next_class:
            insert_pos = class_start + next_class.start()
        else:
            insert_pos = len(content)
        
        # Generate field line
        field_name = new_field.get("name")
        field_line = generate_field_line(field_name, new_field)
        
        # Insert the field
        content = content[:insert_pos] + field_line + "\n" + content[insert_pos:]
    
    if remove_field:
        # Remove field line
        field_pattern = re.compile(
            rf'^\s+{remove_field}\s*=\s*fields\.\w+\s*\([^)]*\)\n?',
            re.MULTILINE
        )
        content = field_pattern.sub('', content)
    
    return content


def add_import_to_file(file_path: Path, import_line: str) -> str:
    """Add an import line to a file if not already present."""
    content = file_path.read_text()
    
    if import_line in content:
        return content
    
    # Find the last import line
    lines = content.split("\n")
    last_import_idx = 0
    
    for i, line in enumerate(lines):
        if line.startswith("from ") or line.startswith("import "):
            last_import_idx = i
    
    # Insert after last import
    lines.insert(last_import_idx + 1, import_line)
    return "\n".join(lines)
